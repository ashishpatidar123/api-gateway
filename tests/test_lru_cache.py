"""Tests for LRU Cache with TTL."""

import time
from gateway.cache.lru_cache import LRUCache


def test_put_and_get():
    cache = LRUCache(capacity=10, default_ttl=300)
    cache.put("key1", "value1")
    assert cache.get("key1") == "value1"


def test_cache_miss_returns_none():
    cache = LRUCache(capacity=10, default_ttl=300)
    assert cache.get("nonexistent") is None


def test_evicts_lru_on_capacity():
    cache = LRUCache(capacity=3, default_ttl=300)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    cache.put("d", 4)  # should evict "a"
    assert cache.get("a") is None
    assert cache.get("b") == 2


def test_get_refreshes_lru_order():
    cache = LRUCache(capacity=3, default_ttl=300)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    cache.get("a")     # refresh "a" - now "b" is LRU
    cache.put("d", 4)  # should evict "b", not "a"
    assert cache.get("a") == 1
    assert cache.get("b") is None


def test_ttl_expiration():
    cache = LRUCache(capacity=10, default_ttl=300)
    cache.put("key", "val", ttl=0)  # TTL=0 means already expired
    time.sleep(0.05)
    assert cache.get("key") is None


def test_stats_tracking():
    cache = LRUCache(capacity=10, default_ttl=300)
    cache.put("k", "v")
    cache.get("k")           # hit
    cache.get("missing")     # miss
    stats = cache.get_stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1