

from gateway.filter.bloom_filter import BloomFilter


def test_blocked_ip_is_detected():
    bf = BloomFilter(expected_items=100, false_positive_rate=0.01)
    bf.add("192.168.1.1")
    assert bf.contains("192.168.1.1") is True


def test_unblocked_ip_not_detected():
    bf = BloomFilter(expected_items=100, false_positive_rate=0.01)
    bf.add("192.168.1.1")
    # Not guaranteed but extremely likely with low FP rate
    print('running')
    assert bf.contains("10.0.0.1") is False


def test_no_false_negatives():
    """Every added item MUST be found - bloom filters have zero false negatives."""
    bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
    ips = [f"10.0.{i // 256}.{i % 256}" for i in range(200)]
    for ip in ips:
        bf.add(ip)
    for ip in ips:
        assert bf.contains(ip) is True, f"False negative for {ip}"


def test_stats():
    bf = BloomFilter(expected_items=100, false_positive_rate=0.01)
    bf.add("1.2.3.4")
    bf.add("5.6.7.8")
    stats = bf.get_stats()
    assert stats["items_count"] == 2
    assert stats["bit_array_size"] > 0
    assert stats["hash_functions"] > 0