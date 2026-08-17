

import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field


class LatencyBucket:
    

    def __init__(self, max_size: int = 10000):
        self._values: deque[float] = deque(maxlen=max_size)

    def add(self, value_ms: float):
        self._values.append(value_ms)

    def percentile(self, p: float) -> float:
        if not self._values:
            return 0
        sorted_vals = sorted(self._values)
        idx = int(len(sorted_vals) * p / 100)
        idx = min(idx, len(sorted_vals) - 1)
        return round(sorted_vals[idx], 2)

class MetricsCollector:
    

    def __init__(self, window_seconds: int = 60):
        self._lock = threading.Lock()
        self._window = window_seconds

        # Counters
        self._total_requests = 0
        self._status_counts: dict[int, int] = defaultdict(int)
        self._method_counts: dict[str, int] = defaultdict(int)
        self._route_counts: dict[str, int] = defaultdict(int)
        self._step_counts: dict[str, int] = defaultdict(int)

        # Latency tracking
        self._latencies = LatencyBucket()
        self._route_latencies: dict[str, LatencyBucket] = defaultdict(LatencyBucket)

        # Sliding window for RPS
        self._request_timestamps: list[float] = []

        # Error tracking
        self._errors = 0
        self._recent_errors: list[dict] = []

    def record_request(self, ctx, status_code: int, duration_s: float, step: str = "proxy"):
        
        duration_ms = duration_s * 1000
        path = ctx.route_match.route.path if ctx.route_match else ctx.request.url.path

        with self._lock:
            self._total_requests += 1
            self._status_counts[status_code] += 1
            self._method_counts[ctx.request.method] += 1
            self._route_counts[path] += 1
            self._step_counts[step] += 1

            self._latencies.add(duration_ms)
            self._route_latencies[path].add(duration_ms)

            self._request_timestamps.append(time.time())

            if status_code >= 400:
                self._errors += 1
                self._recent_errors.append({
                    "time": time.time(),
                    "status": status_code,
                    "path": path,
                    "client": ctx.client_ip,
                    "step": step,
                })
                if len(self._recent_errors) > 50:
                    self._recent_errors = self._recent_errors[-50:]

    def get_stats(self) -> dict:
        
        with self._lock:
            now = time.time()

            # Clean old timestamps for RPS calculation
            cutoff = now - self._window
            self._request_timestamps = [
                ts for ts in self._request_timestamps if ts > cutoff
            ]
            rps = len(self._request_timestamps) / self._window if self._window > 0 else 0

            # Per-route stats
            route_stats = []
            for path, count in sorted(self._route_counts.items(), key=lambda x: -x[1]):
                lat = self._route_latencies.get(path, LatencyBucket())
                route_stats.append({
                    "path": path,
                    "count": count,
                    "p50": lat.percentile(50),
                    "p95": lat.percentile(95),
                    "p99": lat.percentile(99),
                })

            return {
                "total_requests": self._total_requests,
                "requests_per_second": round(rps, 2),
                "status_codes": dict(self._status_counts),
                "methods": dict(self._method_counts),
                "pipeline_steps": dict(self._step_counts),
                "latency": {
                    "p50": self._latencies.percentile(50),
                    "p95": self._latencies.percentile(95),
                    "p99": self._latencies.percentile(99),
                },
                "errors": {
                    "total": self._errors,
                    "error_rate": round(self._errors / self._total_requests * 100, 2) if self._total_requests > 0 else 0,
                    "recent": self._recent_errors[-10:],
                },
                "routes": route_stats,
            }

    def get_dashboard_snapshot(self) -> dict:
        
        with self._lock:
            now = time.time()
            cutoff = now - self._window
            recent = [ts for ts in self._request_timestamps if ts > cutoff]
            rps = len(recent) / self._window if self._window > 0 else 0

            return {
                "total_requests": self._total_requests,
                "rps": round(rps, 2),
                "p50": self._latencies.percentile(50),
                "p95": self._latencies.percentile(95),
                "p99": self._latencies.percentile(99),
                "errors": self._errors,
                "error_rate": round(self._errors / self._total_requests * 100, 2) if self._total_requests > 0 else 0,
                "status_2xx": self._status_counts.get(200, 0) + self._status_counts.get(201, 0),
                "status_4xx": sum(v for k, v in self._status_counts.items() if 400 <= k < 500),
                "status_5xx": sum(v for k, v in self._status_counts.items() if 500 <= k < 600),
            }