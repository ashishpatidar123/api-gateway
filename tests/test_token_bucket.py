"""Tests for Token Bucket rate limiter."""

from gateway.ratelimit.token_bucket import TokenBucketRateLimiter


def test_allows_requests_within_capacity():
    limiter = TokenBucketRateLimiter(default_capacity=5, default_refill_rate=1.0)
    for _ in range(5):
        assert limiter.allow_request("client-1") is True


def test_rejects_when_bucket_empty():
    limiter = TokenBucketRateLimiter(default_capacity=3, default_refill_rate=0.0)
    for _ in range(3):
        limiter.allow_request("client-1")
    assert limiter.allow_request("client-1") is False


def test_separate_buckets_per_client():
    limiter = TokenBucketRateLimiter(default_capacity=2, default_refill_rate=0.0)
    limiter.allow_request("client-1")
    limiter.allow_request("client-1")
    assert limiter.allow_request("client-1") is False
    # Different client should still have tokens
    assert limiter.allow_request("client-2") is True


def test_stats_tracking():
    limiter = TokenBucketRateLimiter(default_capacity=2, default_refill_rate=0.0)
    limiter.allow_request("c1")
    limiter.allow_request("c1")
    limiter.allow_request("c1")  # rejected
    stats = limiter.get_stats()
    assert stats["total_requests"] == 3
    assert stats["allowed"] == 2
    assert stats["rejected"] == 1