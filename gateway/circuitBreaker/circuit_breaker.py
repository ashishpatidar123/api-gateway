import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

@dataclass
class CircuitStats:
    
    failures: list[float] = field(default_factory=list)
    successes: int = 0
    total_requests: int = 0
    total_failures: int = 0
    total_short_circuited: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    last_state_change: float = field(default_factory=time.time)

class CircuitBreaker:
    """
    Circuit breaker for a single backend URL.

    Parameters:
        backend_url:       The backend this breaker protects.
        failure_threshold: Number of failures within window_s to trip the breaker.
        reset_timeout_s:   Seconds to wait in OPEN before transitioning to HALF_OPEN.
        window_s:          Sliding window duration for counting failures.
    """

    def __init__(
        self,
        backend_url: str,
        failure_threshold: int = 5,
        reset_timeout_s: float = 30.0,
        window_s: float = 60.0,
    ):
        self.backend_url = backend_url
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self.window_s = window_s

        self._state = CircuitState.CLOSED
        self._lock = threading.Lock()
        self._stats = CircuitStats()
        self._opened_at: float = 0.0
        self._half_open_in_flight: bool = False

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_transition()
            return self._state

    def allow_request(self) -> bool:
        
        # check if a request should be allowed
        # returns True if allowed, False if the circuit is open.
        
        with self._lock:
            self._maybe_transition()

            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.HALF_OPEN:
                if not self._half_open_in_flight:
                    self._half_open_in_flight = True
                    return True
                return False

            # OPEN
            self._stats.total_short_circuited += 1
            return False

    def record_success(self):
        """Record a successful backend response."""
        with self._lock:
            self._stats.successes += 1
            self._stats.total_requests += 1
            self._stats.last_success_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.CLOSED)
                self._stats.failures.clear()
                self._half_open_in_flight = False

    def record_failure(self):
        """Record a failed backend response."""
        now = time.time()
        with self._lock:
            self._stats.total_requests += 1
            self._stats.total_failures += 1
            self._stats.last_failure_time = now
            self._stats.failures.append(now)

            if self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.OPEN)
                self._half_open_in_flight = False
                return

            if self._state == CircuitState.CLOSED:
                self._prune_old_failures(now)
                if len(self._stats.failures) >= self.failure_threshold:
                    self._transition_to(CircuitState.OPEN)

    def reset(self):
        # manually resetting the ciruit to CLOSED state
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            self._stats.failures.clear()
            self._half_open_in_flight = False

    def get_stats(self) -> dict:
        
        with self._lock:
            self._maybe_transition()
            now = time.time()
            self._prune_old_failures(now)
            remaining = 0.0
            if self._state == CircuitState.OPEN:
                remaining = max(0.0, self.reset_timeout_s - (now - self._opened_at))

            return {
                "backend_url": self.backend_url,
                "state": self._state.value,
                "failure_count": len(self._stats.failures),
                "failure_threshold": self.failure_threshold,
                "reset_timeout_s": self.reset_timeout_s,
                "window_s": self.window_s,
                "total_requests": self._stats.total_requests,
                "total_failures": self._stats.total_failures,
                "total_short_circuited": self._stats.total_short_circuited,
                "successes": self._stats.successes,
                "last_failure_time": self._stats.last_failure_time,
                "last_state_change": self._stats.last_state_change,
                "open_remaining_s": round(remaining, 1),
            }

    
    def _maybe_transition(self):
        # Auto-transition OPEN -> HALF_OPEN after timeout (called under lock)
        if self._state == CircuitState.OPEN:
            if time.time() - self._opened_at >= self.reset_timeout_s:
                self._transition_to(CircuitState.HALF_OPEN)
                self._half_open_in_flight = False

    def _transition_to(self, new_state: CircuitState):
        # Change state (called under lock)
        self._state = new_state
        self._stats.last_state_change = time.time()
        if new_state == CircuitState.OPEN:
            self._opened_at = time.time()

    def _prune_old_failures(self, now: float):
        # Remove failures outside the sliding window (called under lock)
        cutoff = now - self.window_s
        self._stats.failures = [t for t in self._stats.failures if t > cutoff]


class CircuitBreakerManager:
    """
    Manages per-backend circuit breakers and provides aggregate per-service views.

    Usage:
        manager = CircuitBreakerManager()
        manager.register_backend("http://localhost:9001")
        if manager.allow_request("http://localhost:9001"):
            # proxy the request
            manager.record_success("http://localhost:9001")
        else:
            # return 503
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout_s: float = 30.0,
        window_s: float = 60.0,
    ):
        self._failure_threshold = failure_threshold
        self._reset_timeout_s = reset_timeout_s
        self._window_s = window_s
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def register_backend(self, backend_url: str):
        # register a new backend with its own circuit breaker
        with self._lock:
            if backend_url not in self._breakers:
                self._breakers[backend_url] = CircuitBreaker(
                    backend_url=backend_url,
                    failure_threshold=self._failure_threshold,
                    reset_timeout_s=self._reset_timeout_s,
                    window_s=self._window_s,
                )

    def allow_request(self, backend_url: str) -> bool:
        # check if a request to this backend is allowed
        breaker = self._get_breaker(backend_url)
        if breaker is None:
            return True
        return breaker.allow_request()

    def record_success(self, backend_url: str):
        # record a successful response from this backend
        breaker = self._get_breaker(backend_url)
        if breaker:
            breaker.record_success()

    def record_failure(self, backend_url: str):
        #Record a failed response from this backend.
        breaker = self._get_breaker(backend_url)
        if breaker:
            breaker.record_failure()

    def reset_backend(self, backend_url: str) -> bool:
        #Manually reset a backend's circuit breaker. Returns True if found
        breaker = self._get_breaker(backend_url)
        if breaker:
            breaker.reset()
            return True
        return False

    def reset_all(self):
        
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()

    def get_backend_state(self, backend_url: str) -> Optional[CircuitState]:
        
        breaker = self._get_breaker(backend_url)
        if breaker:
            return breaker.state
        return None

    def get_open_backends(self) -> list[str]:
        
        with self._lock:
            return [
                url for url, cb in self._breakers.items()
                if cb.state == CircuitState.OPEN
            ]

    def get_stats(self) -> dict:
        
        with self._lock:
            breakers_info = [cb.get_stats() for cb in self._breakers.values()]
            open_count = sum(1 for b in breakers_info if b["state"] == "open")
            half_open_count = sum(1 for b in breakers_info if b["state"] == "half_open")
            closed_count = sum(1 for b in breakers_info if b["state"] == "closed")
            total_short_circuited = sum(b["total_short_circuited"] for b in breakers_info)

            return {
                "total_breakers": len(self._breakers),
                "open": open_count,
                "half_open": half_open_count,
                "closed": closed_count,
                "total_short_circuited": total_short_circuited,
                "failure_threshold": self._failure_threshold,
                "reset_timeout_s": self._reset_timeout_s,
                "window_s": self._window_s,
                "breakers": breakers_info,
            }

    def _get_breaker(self, backend_url: str) -> Optional[CircuitBreaker]:
        
        with self._lock:
            return self._breakers.get(backend_url)