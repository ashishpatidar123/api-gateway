from enum import Enum

from gateway.ratelimit.token_bucket import TokenBucketRateLimiter
from gateway.ratelimit.sliding_window_log import SlidingWindowLogRateLimiter
from gateway.ratelimit.sliding_window_counter import SlidingWindowCounterRateLimiter

class RateLimitStrategy(str, Enum):
    TOKEN_BUCKET = "token_bucket"
    SLIDING_WINDOW_LOG = "sliding_window_log"
    SLIDING_WINDOW_COUNTER = "sliding_window_counter"

class RateLimiter:
   

    def __init__(self, default_capacity: int = 60, window_seconds: float = 60.0):
        self._strategies = {
            RateLimitStrategy.TOKEN_BUCKET: TokenBucketRateLimiter(
                default_capacity=default_capacity,
                default_refill_rate=default_capacity / window_seconds,
            ),
            RateLimitStrategy.SLIDING_WINDOW_LOG: SlidingWindowLogRateLimiter(
                default_capacity=default_capacity,
                window_seconds=window_seconds,
            ),
            RateLimitStrategy.SLIDING_WINDOW_COUNTER: SlidingWindowCounterRateLimiter(
                default_capacity=default_capacity,
                window_seconds=window_seconds,
            ),
        }
        self._active: RateLimitStrategy = RateLimitStrategy.TOKEN_BUCKET

    @property
    def active_strategy(self) -> RateLimitStrategy:
        return self._active
    
    @property
    def _current(self):
        return self._strategies[self._active]

    def set_strategy(self, strategy: RateLimitStrategy):
        
        self._active = strategy

    def allow_request(self, client_id: str, capacity: int = None, # type: ignore
                      refill_rate: float = None) -> bool: # type: ignore
        
        return self._current.allow_request(client_id, capacity, refill_rate)

    def get_client_info(self, client_id: str) -> dict:
        
        return self._current.get_client_info(client_id)

    def get_stats(self) -> dict:
        
        stats = self._current.get_stats()
        stats["active_strategy"] = self._active.value
        stats["available_strategies"] = [s.value for s in RateLimitStrategy]
        return stats

    def get_all_clients(self) -> list[dict]:
       
        return self._current.get_all_clients()

    def reset_client(self, client_id: str):
        
        self._current.reset_client(client_id)