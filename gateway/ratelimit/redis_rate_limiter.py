import os
import time
import logging
from typing import Optional

logger = logging.getLogger("gateway.ratelimit.redis")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
REDIS_ENABLED = os.environ.get("RATE_LIMIT_REDIS_ENABLED", "false").lower() in ("true", "1", "yes")

class RedisRateLimiter:
    """Distributed sliding-window rate limiter backed by Redis sorted sets."""

    def __init__(self, window_seconds: float = 60.0, key_prefix: str = "rl:"):
        self._window = window_seconds
        self._prefix = key_prefix
        self._redis = None
        self._available = False

        # Stats (local counts for monitoring)
        self._allowed = 0
        self._rejected = 0

        if REDIS_ENABLED:
            self._connect()

    def _connect(self):
        """Attempt to connect to Redis."""
        try:
            import redis
            self._redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
            self._redis.ping()
            self._available = True
            logger.info("Redis rate limiter connected to %s", REDIS_URL)
        except ImportError:
            logger.warning("redis package not installed - Redis rate limiter disabled")
            self._available = False
        except Exception as e:
            logger.warning("Redis connection failed (%s) - Redis rate limiter disabled", e)
            self._available = False

    @property
    def is_available(self) -> bool:
        return self._available

    def allow_request(self, client_id: str, capacity: int = 60) -> bool:
        """
        Check if a request is allowed under the sliding window.

        Uses a Redis pipeline:
        1. Remove expired entries
        2. Count current entries
        3. If under capacity, add the new entry

        Returns True if allowed, False if rate limited.
        """
        if not self._available or self._redis is None:
            return True # Fail open if Redis is down

        key = f"{self._prefix}{client_id}"
        now = time.time()
        window_start = now - self._window

        try:
            pipe = self._redis.pipeline(True)
            # Remove old entries outside the window
            pipe.zremrangebyscore(key, "-inf", window_start)
            # Count entries in current window
            pipe.zcard(key)
            results = pipe.execute()
            current_count = results[1]

            if current_count >= capacity:
                self._rejected += 1
                return False

            # Add the new request
            pipe2 = self._redis.pipeline(True)
            pipe2.zadd(key, {f"{now}:{id(pipe2)}": now})
            pipe2.expire(key, int(self._window) + 1)
            pipe2.execute()

            self._allowed += 1
            return True

        except Exception as e:
            logger.error("Redis rate limit error: %s - failing open", e)
            return True # Fail open on Redis errors

    def get_client_info(self, client_id: str) -> dict:
        """Get current window count for a client."""
        if not self._available or self._redis is None:
            return {"tokens": -1, "capacity": -1}

        key = f"{self._prefix}{client_id}"
        now = time.time()
        window_start = now - self._window

        try:
            pipe = self._redis.pipeline(True)
            pipe.zremrangebyscore(key, "-inf", window_start)
            pipe.zcard(key)
            results = pipe.execute()
            count = results[1]
            return {"current_count": count, "window_seconds": self._window}
        except Exception:
            return {"current_count": -1, "window_seconds": self._window}

    def get_stats(self) -> dict:
        """Return Redis rate limiter stats."""
        return {
            "backend": "redis",
            "redis_url": REDIS_URL if self._available else None,
            "available": self._available,
            "window_seconds": self._window,
            "allowed": self._allowed,
            "rejected": self._rejected,
        }

    def reset_client(self, client_id: str):
        """Remove all entries for a client."""
        if self._available and self._redis:
            try:
                self._redis.delete(f"{self._prefix}{client_id}")
            except Exception:
                pass

    def get_all_clients(self) -> list[dict]:
        """List active clients (limited scan)."""
        if not self._available or self._redis is None:
            return []
        try:
            clients = []
            for key in self._redis.scan_iter(f"{self._prefix}*", count=100):
                client_id = key.replace(self._prefix, "", 1)
                count = self._redis.zcard(key)
                clients.append({"client_id": client_id, "current_count": count})
            return clients[:50]
        except Exception:
            return []