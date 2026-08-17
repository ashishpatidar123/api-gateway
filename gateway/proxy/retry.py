"""
Retry with Exponential Backoff

Algorithm:
- On failure, wait: base_delay * (2 ^ attempt) + random jitter
- Jitter prevents thundering herd when multiple clients retry simultaneously
- Only retries on transient errors (5xx, connection errors), NOT on 4xx

Time Complexity: O(max_retries) per request in the worst case
Space Complexity: O(1) - no extra state beyond the attempt counter

Parameters:
- max_retries: maximum retry attempts (default 3)
- base_delay: initial delay in seconds (default 0.5)
- max_delay: cap on delay to prevent excessive waits (default 10.0)
- jitter: adds random [0, jitter] seconds to each delay (default 0.25)
"""

import asyncio
import logging
import random
from typing import Callable, Any

logger = logging.getLogger("gateway.retry")

# Status codes that are considered retryable (transient server errors)
RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})

class RetryPolicy:
    """Configurable retry policy with exponential backoff and jitter."""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 10.0,
        jitter: float = 0.25,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter

        # Stats
        self._total_retries = 0
        self._total_calls = 0
        self._total_exhausted = 0

    def _compute_delay(self, attempt: int) -> float:
        """Compute delay for a given attempt using exponential backoff + jitter."""
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        delay += random.uniform(0, self.jitter)
        return delay

    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute an async function with retry logic.

        The function should return a dict with a 'status_code' key.
        On retryable failures or exceptions, retries up to max_retries times.
        """
        self._total_calls += 1
        last_exception = None
        last_result = None

        for attempt in range(self.max_retries + 1):
            try:
                result = await func(*args, **kwargs)

                # Check if the result indicates a retryable error
                status = result.get("status_code", 200) if isinstance(result, dict) else 200
                if status in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                    delay = self._compute_delay(attempt)
                    logger.warning(
                        "Retry %d/%d: status=%d, backoff=%.2fs",
                        attempt + 1, self.max_retries, status, delay,
                    )
                    self._total_retries += 1
                    last_result = result
                    await asyncio.sleep(delay)
                    continue

                return result

            except Exception as e:
                last_exception = e
                if attempt < self.max_retries:
                    delay = self._compute_delay(attempt)
                    logger.warning(
                        "Retry %d/%d: error=%s, backoff=%.2fs",
                        attempt + 1, self.max_retries, str(e), delay,
                    )
                    self._total_retries += 1
                    await asyncio.sleep(delay)
                else:
                    break

        # All retries exhausted
        self._total_exhausted += 1
        if last_exception:
            raise last_exception
        return last_result

    def get_stats(self) -> dict:
        """Return retry statistics."""
        return {
            "total_calls": self._total_calls,
            "total_retries": self._total_retries,
            "total_exhausted": self._total_exhausted,
            "config": {
                "max_retries": self.max_retries,
                "base_delay": self.base_delay,
                "max_delay": self.max_delay,
                "jitter": self.jitter,
            }
        }