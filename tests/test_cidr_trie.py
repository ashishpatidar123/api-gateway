"""Tests for CIDR Binary Trie - IP range blocking."""

from gateway.filter.cidr_trie import CidrTrie


def test_block_cidr_range():
    trie = CidrTrie()
    trie.add("10.0.0.0/8")
    assert trie.contains("10.5.3.1") is True
    assert trie.contains("10.255.255.255") is True


def test_non_blocked_ip():
    trie = CidrTrie()
    trie.add("10.0.0.0/8")
    assert trie.contains("192.168.1.1") is False


def test_exact_ip_block():
    trie = CidrTrie()
    trie.add("172.16.0.50")  # treated as /32
    assert trie.contains("172.16.0.50") is True
    assert trie.contains("172.16.0.51") is False


def test_remove_cidr():
    trie = CidrTrie()
    trie.add("10.0.0.0/8")
    assert trie.contains("10.1.1.1") is True
    trie.remove("10.0.0.0/8")
    assert trie.contains("10.1.1.1") is False


def test_hierarchical_blocking():
    """Blocking /8 should cover all IPs in that range."""
    trie = CidrTrie()
    trie.add("192.168.0.0/16")
    assert trie.contains("192.168.0.1") is True
    assert trie.contains("192.168.255.254") is True
    assert trie.contains("192.167.0.1") is False