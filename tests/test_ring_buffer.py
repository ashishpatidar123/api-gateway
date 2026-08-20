"""Tests for Ring Buffer - fixed-size circular request log."""

import time
from gateway.log.ring_buffer import RingBuffer, RequestLogEntry


def _make_entry(path="/test", status=200):
    return RequestLogEntry(
        timestamp=time.time(), method="GET", path=path,
        status_code=status, latency_ms=1.5, client_ip="127.0.0.1",
        pipeline_step="proxy"
    )


def test_append_and_read():
    buf = RingBuffer(capacity=10)
    buf.append(_make_entry("/a"))
    buf.append(_make_entry("/b"))
    recent = buf.get_recent(10)
    assert len(recent) == 2
    assert recent[0]["path"] == "/b"  # newest first


def test_overwrites_oldest_when_full():
    buf = RingBuffer(capacity=3)
    buf.append(_make_entry("/1"))
    buf.append(_make_entry("/2"))
    buf.append(_make_entry("/3"))
    buf.append(_make_entry("/4"))  # overwrites /1
    recent = buf.get_recent(10)
    assert len(recent) == 3
    paths = [r["path"] for r in recent]
    assert "/1" not in paths
    assert "/4" in paths


def test_stats():
    buf = RingBuffer(capacity=5)
    for i in range(8):
        buf.append(_make_entry())
    stats = buf.get_stats()
    assert stats["capacity"] == 5
    assert stats["current_size"] == 5
    assert stats["total_logged"] == 8
    assert stats["overwrites"] == 3


def test_clear_resets_buffer():
    buf = RingBuffer(capacity=5)
    buf.append(_make_entry())
    buf.clear()
    assert buf.get_recent(10) == []
    assert buf.get_stats()["total_logged"] == 0