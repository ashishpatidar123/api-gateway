"""
Load Balancer
Algorithms:
1. Round Robin
2. Weighted Round Robin
3. Least Connections
"""

import bisect
import hashlib
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class Strategy(str, Enum):
    ROUND_ROBIN = "round_robin"
    WEIGHTED = "weighted"
    LEAST_CONNECTIONS = "least_connections"
    CONSISTENT_HASH = "consistent_hash"

@dataclass
class Backend:
    url: str
    weight: int = 1
    healthy: bool = True
    active_connections: int = 0
    total_requests: int = 0
    total_errors: int = 0
    last_health_check: float = field(default_factory=time.time)

    def record_response(self, duration_ms: float, success: bool):
        self.total_requests += 1
        if not success:
            self.total_errors += 1

class ConsistentHashRing:
    """
    Consistent Hash Ring with virtual nodes.

    Algorithm:
    - Each backend gets `replicas` virtual nodes placed on a hash ring
    - Node position = MD5("backend_url:replica_index") mod 2^32
    - To route a key, hash it and walk clockwise to the nearest node
    - Adding/removing a backend only remaps ~1/N of keys (minimal disruption)

    Time Complexity: O(log n) per lookup via bisect, O(n * replicas) to build
    Space Complexity: O(n * replicas) for the ring
    """

    def __init__(self, replicas: int = 150):
        self._replicas = replicas
        self._ring: list[int] = []          # sorted hash positions
        self._ring_map: dict[int, str] = {} # hash position -> backend url
        self._nodes: set[str] = set()

    def _hash(self, key: str) -> int:
        #MD5-based hash truncated to 32-bit integer
        return int(hashlib.md5(key.encode()).hexdigest(), 16) % (2 ** 32)

    def add_node(self, url: str):
        # adding  a backend to the ring with virtual nodes
        if url in self._nodes:
            return
        self._nodes.add(url)
        for i in range(self._replicas):
            h = self._hash(f"{url}:{i}")
            self._ring_map[h] = url
            bisect.insort(self._ring, h)

    def remove_node(self, url: str):
        # removing a backend and all its virtual nodes
        if url not in self._nodes:
            return
        self._nodes.discard(url)
        to_remove = [h for h, u in self._ring_map.items() if u == url]
        for h in to_remove:
            del self._ring_map[h]
            idx = bisect.bisect_left(self._ring, h)
            if idx < len(self._ring) and self._ring[idx] == h:
                self._ring.pop(idx)

    def get_node(self, key: str) -> Optional[str]:
    #    find the backend for the given key
        if not self._ring:
            return None
        h = self._hash(key)
        idx = bisect.bisect_right(self._ring, h)
        if idx == len(self._ring):
            idx = 0  # wrap around
        return self._ring_map[self._ring[idx]]

class LoadBalancer:
    

    def __init__(self, strategy: Strategy = Strategy.ROUND_ROBIN):
        self._backends: dict[str, Backend] = {}
        self._strategy = strategy
        self._rr_index = 0
        self._wrr_index = 0
        self._wrr_current_weight = 0
        self._hash_ring = ConsistentHashRing(replicas=150)
        self._lock = threading.Lock()

    def add_backend(self, url: str, weight: int = 1):
        
        with self._lock:
            self._backends[url] = Backend(url=url, weight=weight)
            self._hash_ring.add_node(url)

    def remove_backend(self, url: str):
        
        with self._lock:
            self._backends.pop(url, None)
            self._hash_ring.remove_node(url)

    def select_backend(self, routing_key: str = "") -> Optional[Backend]:
        
        with self._lock:
            healthy = [b for b in self._backends.values() if b.healthy]
            if not healthy:
                return None

            if self._strategy == Strategy.ROUND_ROBIN:
                return self._round_robin(healthy)
            elif self._strategy == Strategy.WEIGHTED:
                return self._weighted_round_robin(healthy)
            elif self._strategy == Strategy.LEAST_CONNECTIONS:
                return self._least_connections(healthy)
            elif self._strategy == Strategy.CONSISTENT_HASH:
                return self._consistent_hash(healthy, routing_key)

    def _round_robin(self, backends: list[Backend]) -> Backend:
       
        backend = backends[self._rr_index % len(backends)]
        self._rr_index += 1
        return backend

    def _weighted_round_robin(self, backends: list[Backend]) -> Backend:
        
        if not hasattr(self, '_wrr_weights'):
            self._wrr_weights = {}

        # Initialize weights
        for b in backends:
            if b.url not in self._wrr_weights:
                self._wrr_weights[b.url] = 0

        # Increment current weights by effective weight
        total = sum(b.weight for b in backends)
        for b in backends:
            self._wrr_weights[b.url] = self._wrr_weights.get(b.url, 0) + b.weight

        # Select the one with highest current weight
        selected = max(backends, key=lambda b: self._wrr_weights.get(b.url, 0))
        self._wrr_weights[selected.url] -= total
        return selected

    def _least_connections(self, backends: list[Backend]) -> Backend:
        
        return min(backends, key=lambda b: b.active_connections)

    def _consistent_hash(self, backends: list[Backend], key: str) -> Backend:
        
        if not key:
            return self._round_robin(backends)
        healthy_urls = {b.url for b in backends}
        # Walk the ring until we find a healthy backend
        url = self._hash_ring.get_node(key)
        if url and url in healthy_urls:
            return self._backends[url]
        # Fallback: try next nodes on the ring
        return self._round_robin(backends)

    def acquire_connection(self, backend: Backend):
        
        with self._lock:
            backend.active_connections += 1

    def release_connection(self, backend: Backend, duration_ms: float, success: bool):
       
        with self._lock:
            backend.active_connections = max(0, backend.active_connections - 1)
            backend.record_response(duration_ms, success)

    def set_health(self, url: str, healthy: bool):
        
        with self._lock:
            if url in self._backends:
                self._backends[url].healthy = healthy
                self._backends[url].last_health_check = time.time()

    def set_strategy(self, strategy: Strategy):
        
        with self._lock:
            self._strategy = strategy

    def get_stats(self) -> dict:
       
        with self._lock:
            backends_info = []
            for b in self._backends.values():
                backends_info.append({
                    "url": b.url,
                    "healthy": b.healthy,
                    "weight": b.weight,
                    "active_connections": b.active_connections,
                    "total_requests": b.total_requests,
                    "total_errors": b.total_errors,
                    "error_rate": round(b.total_errors / b.total_requests * 100, 2) if b.total_requests > 0 else 0
                })
            return {
                "strategy": self._strategy.value,
                "total_backends": len(self._backends),
                "healthy_backends": sum(1 for b in self._backends.values() if b.healthy),
                "backends": backends_info,
            }