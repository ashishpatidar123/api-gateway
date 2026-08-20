"""
LRU Cache with TTL
"""

import time
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class CacheEntry:
    value: Any
    created_at: float
    ttl: int
    access_count: int  = 0

    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl


class LRUCache:
    # thread safe LRU cache with per-entry TTL

    def __init__(self, capacity: int = 1000, default_ttl: int = 300):
        self._store :  OrderedDict[str, CacheEntry] = OrderedDict()
        self._capacity = capacity
        self._default_ttl = default_ttl
        self._lock = threading.Lock()
        self._stats = {
            "hits" : 0,
            "misses" : 0,
            "evictions": 0,
            "expirations" : 0
        }

    def get(self, key:str) -> Optional[Any]:
        # retreive a cached value. Returns None on miss or expiry
        # moves the entry to the end (most recenty used on hit)

        with self._lock:
            if key not in self._store:
                self._stats["misses"] += 1
                return None
            entry = self._store[key]

            if entry.is_expired():
                del self._store[key]
                self._stats["expirations"] += 1
                self._stats["misses"] += 1
                return None

            # move to the end
            self._store.move_to_end(key)
            entry.access_count += 1
            self._stats["hits"] += 1
            return entry.value

    def put(self, key:str, value: Any, ttl: int = None): # type: ignore
        # insert or upate a cache entry
        # evicts the LRU entry is capacity is exceeded

        entry_ttl = ttl if ttl is not None else self._default_ttl

        with self._lock:
            if key in self._store:
                # update existing entry move to end
                self._store[key] = CacheEntry(value = value, created_at=time.time(), ttl=entry_ttl)

                self._store.move_to_end(key)
                return

            # evict LRU is at capacity

            while len(self._store) >= self._capacity:
                evicted_key, _ = self._store.popitem(last = False)
                self._stats["evictions"] += 1

            self._store[key] = CacheEntry(value=value, created_at=time.time(),ttl = entry_ttl)

    def invalidate(self, key:str)->bool:
        # remove a specific key from the table
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def clear(self):
        # clear the entire cache record
        with self._lock:
            self._store.clear()

    def cleanup_expired(self) -> int:
        # remove all expired entries
        with self._lock:
            expired_keys = []
            for k , v in self._store.items():
                if v.is_expired:
                    expired_keys.append(k)

            for k in expired_keys:
                del self._store[k]
                self._stats["expirations"] += 1
            return len(expired_keys)

    def get_stats(self) -> dict:
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            response = {
                **self._stats,
                "size" : len(self._store),
                "capacity" : self._capacity,
                "hit_rate" : (round(self._stats["hits"] / total * 100, 2)
                                if total > 0 else 0),
            }

            return response

    def get_entries(self, limit: int  = 30) -> list[dict]:
        with self._lock:
            entries = []
            now = time.time()
            for key, entry in list(self._store.items())[-limit:]:
                entry = {
                    "key" : key,
                    "ttl_remaining" :  max(0, round(entry.ttl - (now -entry.created_at))),
                    "access_count" : entry.access_count,
                    "expired": entry.is_expired(),
                }

                entries.append(entry)

            return entries

                
