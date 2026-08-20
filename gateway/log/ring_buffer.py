import time
import threading
from dataclasses import dataclass, field, asdict
from typing import Optional

from torch import bucketize

@dataclass
class RequestLogEntry:
    # single request log entry stored in the ring buffer
    timestamp:float
    method:str
    path:str
    status_code:int
    latency_ms: float
    client_ip:str
    pipeline_step: str
    cached: bool = False
    backend: Optional[str] = None
    rate_limited: bool = False
    blocked: bool = False


class RingBuffer:
    # fixed size circular buffer for request logging

    def __init__(self, capacity: int = 1000):
        self._capacity = capacity
        self._buffer: list[Optional[RequestLogEntry]] = [None] * capacity
        self._head = 0 # next write position
        self._count = 0 # total entries written
        self._lock = threading.Lock()

    def append(self, entry:RequestLogEntry):
        # insert an entry at the head position in the O(1) time
        # overwrites the oldest entry when the buffer is full

        with self._lock:
            self._buffer[self._head % self._capacity] = entry
            self._head += 1
            self._count +=1

    def get_recent(self, n:int  = 50) -> list[dict]:
        # get the n most recent entries, newest first 

        with self._lock:
            entries = []
            size = min(n, min(self._head, self._capacity))
            for i in range(size):
                idx = (self._head - 1- i)% self._capacity
                entry = self._buffer[idx]
                if entry is not None:
                    d = asdict(entry)
                    d["age_ms"] = round((time.time() - entry.timestamp)*100)
                    entries.append(d)

            return entries

    def get_stats(self)->dict:
        with self._lock:
            curr_size = min(self._head, self._capacity)
            response = {
                "capacity":self._capacity,
                "current_size" : curr_size,
                "total_logged": self._count,
                "fill_ratio": round(curr_size/self._capacity*100,2),
                "head_position": self._head % self._capacity,
                "overwrites": max(0, self._count - self._capacity),
            }

            return response

    def clear(self):
        with self._lock:
            self._buffer = [None] * self._capacity
            self._head = 0
            self._count = 0