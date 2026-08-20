"""
Active Health Checker - Background Probing

Periodically sends HTTP GET requests to each backend's /health endpoint.
Automatically marks backends healthy or unhealthy based on response.

Algorithm:
- Every `interval` seconds, probes all registered backends in parallel
- A backend is marked healthy if it responds with 2xx within `timeout` seconds
- A backend is marked unhealthy after `unhealthy_threshold` consecutive failures
- A previously unhealthy backend is restored after `healthy_threshold` consecutive successes

Time Complexity: O(n) per check cycle where n = number of backends
Space Complexity: O(n) for tracking consecutive results per backend
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("gateway.health")

@dataclass
class BackendHealth:
    """Tracks consecutive health check results for a backend."""
    url: str
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_check: float = 0.0
    last_status: str = "unknown"
    last_latency_ms: float = 0.0
    total_checks: int = 0
    total_failures: int = 0

class HealthChecker:
    """Active health checker that probes backends at regular intervals."""

    def __init__(
        self,
        interval: float = 10.0,
        timeout: float = 5.0,
        health_path: str = "/health",
        unhealthy_threshold: int = 3,
        healthy_threshold: int = 2,
    ):
        self._interval = interval
        self._timeout = timeout
        self._health_path = health_path
        self._unhealthy_threshold = unhealthy_threshold
        self._healthy_threshold = healthy_threshold
        self._backends: dict[str, BackendHealth] = {}
        self._on_status_change = None  # callback(url, healthy)
        self._running = False
        self._task = None

    def register_backend(self, url: str):
        """Register a backend for health checking."""
        if url not in self._backends:
            self._backends[url] = BackendHealth(url=url)

    def set_status_change_callback(self, callback):
        """Set callback invoked when a backend's health status changes.
        Callback signature: callback(url: str, healthy: bool)"""
        self._on_status_change = callback

    async def start(self):
        """Start the background health check loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Health checker started: interval=%.0fs, timeout=%.0fs, "
            "unhealthy_after=%d failures, healthy_after=%d successes",
            self._interval, self._timeout,
            self._unhealthy_threshold, self._healthy_threshold,
        )

    async def stop(self):
        """Stop the background health check loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Health checker stopped")

    async def _loop(self):
        """Main loop - probe all backends every interval."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout)) as client:
            while self._running:
                await self._check_all(client)
                await asyncio.sleep(self._interval)

    async def _check_all(self, client: httpx.AsyncClient):
        """Probe all backends in parallel."""
        tasks = [self._check_one(client, bh) for bh in self._backends.values()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_one(self, client: httpx.AsyncClient, bh: BackendHealth):
        """Probe a single backend."""
        url = f"{bh.url.rstrip('/')}{self._health_path}"
        start = time.time()
        bh.total_checks += 1

        try:
            resp = await client.get(url)
            latency_ms = (time.time() - start) * 1000
            bh.last_latency_ms = round(latency_ms, 2)
            bh.last_check = time.time()

            if 200 <= resp.status_code < 300:
                bh.consecutive_failures = 0
                bh.consecutive_successes += 1
                bh.last_status = "healthy"

                if bh.consecutive_successes >= self._healthy_threshold:
                    if self._on_status_change:
                        self._on_status_change(bh.url, True)
            else:
                self._record_failure(bh, f"status={resp.status_code}")

        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            bh.last_latency_ms = round(latency_ms, 2)
            bh.last_check = time.time()
            self._record_failure(bh, str(e))

    def _record_failure(self, bh: BackendHealth, reason: str):
        """Record a health check failure."""
        bh.consecutive_successes = 0
        bh.consecutive_failures += 1
        bh.total_failures += 1
        bh.last_status = f"unhealthy ({reason})"

        if bh.consecutive_failures >= self._unhealthy_threshold:
            if self._on_status_change:
                self._on_status_change(bh.url, False)

    def get_stats(self) -> dict:
        """Return health check statistics."""
        return {
            "interval_s": self._interval,
            "timeout_s": self._timeout,
            "health_path": self._health_path,
            "unhealthy_threshold": self._unhealthy_threshold,
            "healthy_threshold": self._healthy_threshold,
            "backends": [
                {
                    "url": bh.url,
                    "status": bh.last_status,
                    "consecutive_failures": bh.consecutive_failures,
                    "consecutive_successes": bh.consecutive_successes,
                    "last_check": bh.last_check,
                    "last_latency_ms": bh.last_latency_ms,
                    "total_checks": bh.total_checks,
                    "total_failures": bh.total_failures,
                }
                for bh in self._backends.values()
            ],
        }