import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("gateway.simulator")

# Default: enabled unless explicitly disabled
INITIAL_ENABLED = os.environ.get("ENABLE_TRAFFIC_SIM", "true").lower() in ("true", "1", "yes")

@dataclass
class SimulatorConfig:
    """Tunable parameters for traffic generation."""
    # Requests per second range (varies randomly within this band)
    min_rps: float = 8.0
    max_rps: float = 35.0

    # Burst config: every burst_interval_s, spike traffic for burst_duration_s
    burst_interval_s: float = 45.0
    burst_duration_s: float = 8.0
    burst_multiplier: float = 3.0

    # Backend failure simulation
    failure_episode_interval_s: float =90.0  # how often a failure episode starts
    failure_episode_duration_s: float = 12.0  # how long the elevated failure rate lasts
    failure_episode_rate: float = 0.20        # failure probability during episode

    # Normal failure rate (outside episodes)
    normal_failure_rate: float = 0.03

    # Simulated client IPs
    client_ips: list = field(default_factory=lambda: [
        "10.0.1.10", "10.0.1.11", "10.0.1.12", "10.0.1.13",
        "10.0.2.20", "10.0.2.21", "10.0.2.22",
        "172.16.0.5", "172.16.0.6", "172.16.0.7",
        "192.168.10.100", "192.168.10.101",
    ])

    # Route weights (relative frequency)
    route_weights: list = field(default_factory=lambda: [
        # (method, path, weight)
        ("GET",    "/api/v1/products",    30),
        ("GET",    "/api/v1/products/p1", 15),
        ("GET",    "/api/v1/products/p2",  8),
        ("GET",    "/api/v1/users",       12),
        ("GET",    "/api/v1/users/u1",     6),
        ("POST",   "/api/v1/users",        3),
        ("GET",    "/api/v1/orders",      10),
        ("GET",    "/api/v1/orders/o1",    5),
        ("POST",   "/api/v1/orders",       4),
        ("GET",    "/api/v1/search",      15),
        ("GET",    "/api/v1/health",      20),
        ("GET",    "/static/logo.png",     5),
    ])

class TrafficSimulator:
    """
    Async traffic simulator that drives requests through the gateway
    pipeline to produce realistic dashboard metrics.
    """

    def __init__(self, config: Optional[SimulatorConfig] = None):
        self._config = config or SimulatorConfig()
        self._enabled = INITIAL_ENABLED
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._total_generated = 0
        self._start_time: Optional[float] = None

        # Pre-compute weighted route selection
        self._routes = []
        self._route_weights = []
        for method, path, weight in self._config.route_weights:
            self._routes.append((method, path))
            self._route_weights.append(weight)

        # Failure episode tracking
        self._in_failure_episode = False
        self._failure_episode_start = 0.0
        self._last_failure_episode = 0.0

        # Burst tracking
        self._in_burst = False
        self._burst_start = 0.0
        self._last_burst = 0.0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def running(self) -> bool:
        return self._running

    def get_stats(self) -> dict:
        """Return simulator status for the dashboard."""
        uptime = time.time() - self._start_time if self._start_time else 0
        return {
            "enabled": self._enabled,
            "running": self._running,
            "total_generated": self._total_generated,
            "uptime_s": round(uptime, 1),
            "in_burst": self._in_burst,
            "in_failure_episode": self._in_failure_episode,
            "config": {
                "min_rps": self._config.min_rps,
                "max_rps": self._config.max_rps,
                "burst_multiplier": self._config.burst_multiplier,
                "client_count": len(self._config.client_ips),
                "route_count": len(self._routes),
            },
        }

    def toggle(self) -> bool:
        """Toggle simulator on/off. Returns new enabled state."""
        if self._enabled:
            self._enabled = False
            self._stop_loop()
        else:
            self._enabled = True
            self._start_loop()
        return self._enabled

    def set_enabled(self, enabled: bool):
        """Explicitly set simulator state."""
        if enabled and not self._enabled:
            self._enabled = True
            self._start_loop()
        elif not enabled and self._enabled:
            self._enabled = False
            self._stop_loop()

    def start(self):
        """Start the simulator if enabled. Called during app lifespan startup."""
        if self._enabled:
            self._start_loop()
        logger.info("TrafficSimulator initialized (enabled=%s)", self._enabled)

    def stop(self):
        """Stop the simulator. Called during app lifespan shutdown."""
        self._stop_loop()
        logger.info("TrafficSimulator stopped (generated %d requests)", self._total_generated)

    def _start_loop(self):
        if self._running:
            return
        self._running = True
        self._start_time = time.time()
        self._last_burst = time.time()
        self._last_failure_episode = time.time()
        self._task = asyncio.create_task(self._run())
        logger.info("TrafficSimulator started")

    def _stop_loop(self):
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("TrafficSimulator stopped")

    async def _run(self):
        """Main simulation loop."""
        # These will be set by wire() from main.py
        try:
            while self._running:
                now = time.time()

                # --- Burst management ---
                if not self._in_burst and (now - self._last_burst) > self._config.burst_interval_s:
                    self._in_burst = True
                    self._burst_start = now
                    self._last_burst = now

                if self._in_burst and (now - self._burst_start) > self._config.burst_duration_s:
                    self._in_burst = False

                # --- Failure episode management ---
                if not self._in_failure_episode and (now - self._last_failure_episode) > self._config.failure_episode_interval_s:
                    self._in_failure_episode = True
                    self._failure_episode_start = now
                    self._last_failure_episode = now

                if self._in_failure_episode and (now - self._failure_episode_start) > self._config.failure_episode_duration_s:
                    self._in_failure_episode = False

                # --- Calculate current RPS ---
                base_rps = random.uniform(self._config.min_rps, self._config.max_rps)
                if self._in_burst:
                    current_rps = base_rps * self._config.burst_multiplier
                else:
                    current_rps = base_rps

                # --- Determine failure rate ---
                if self._in_failure_episode:
                    failure_rate = self._config.failure_episode_rate
                else:
                    failure_rate = self._config.normal_failure_rate

                # --- Generate a batch of requests ---
                # We generate in small batches with sleep to avoid blocking
                batch_size = max(1, int(current_rps / 5))  # 5 batches per second
                sleep_interval = 1.0 / max(1, current_rps / batch_size)

                for _ in range(batch_size):
                    if not self._running:
                        return
                    await self._generate_one_request(failure_rate)
                    self._total_generated += 1

                await asyncio.sleep(sleep_interval)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("TrafficSimulator error: %s", e)
            self._running = False

    async def _generate_one_request(self, failure_rate: float):
        """Generate a single simulated request through the gateway pipeline."""
        # Pick a random route and client
        method, path = random.choices(self._routes, weights=self._route_weights, k=1)[0]
        client_ip = random.choice(self._config.client_ips)

        # Simulate latency: mostly fast, occasionally slow
        # Log-normal distribution gives a realistic tail
        base_latency = random.lognormvariate(3.5, 0.6)  # median ~33ms, occasional spikes
        latency_ms = min(base_latency, 2000)  # cap at 2s

        # Determine if this request "fails"
        is_failure = random.random() < failure_rate

        if is_failure:
            # Failures tend to be slower (timeouts)
            latency_ms = random.uniform(500, 1500)

        # Simulate the processing delay
        await asyncio.sleep(latency_ms / 1000.0 * 0.1)  # scaled down to not actually wait full time

        # Call the injected handler
        if self._request_handler:
            await self._request_handler(
                method=method,
                path=path,
                client_ip=client_ip,
                latency_ms=round(latency_ms, 2),
                is_failure=is_failure,
            )

    # --- Dependency injection ---

    _request_handler = None

    def wire(self, request_handler):
        """
        Inject the request handler function.

        The handler signature should be:
            async def handler(method, path, client_ip, latency_ms, is_failure)
        """
        self._request_handler = request_handler