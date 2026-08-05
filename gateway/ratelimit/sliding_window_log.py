"""
sliding window log rate limiter

algo:
 - maintains a sorted list of request timestamps per client
 - one each request, remove all timestamps outside the window using binary search
 - if remaining count is less than limit, allow the request and record timestamp
 - otherwise reject the request

properties:
 - exact counting
 - O(log n) per request
  - higher memory usage than counter approach (stores every timestamp)
  - no burst allowance

comparison with token bucket:
 - token bucket allows burst
 - token bucket is O(1)
 - this gives accurate counts, token bucket approximates via refil
"""

import time
import threading
import bisect
from collections import defaultdict



class SlidingWindowLogRateLimiter:
    # per-client sliding window log rate limiter

    def __init__(self, default_capacity: int = 60, window_seconds: float = 60.0):

        self._logs: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

        self.default_capacity = default_capacity
        self.window_seconds = window_seconds
        self._stats = {
            "total_requests" : 0,
            "allowed" : 0,
            "rejected" : 0
        }


    def allow_request(self, client_id:str, capacity: int = None) -> bool: # type: ignore

        # check if a request from client should be allowed or not,
        # removes expired timestamps, then checks count against capacity

        cap = capacity or self.default_capacity
        

        with self._lock:
            self._stats["total_requests"] +=1 
            now  =  time.monotonic()
            cutoff = now - self.window_seconds

            log = self._logs[client_id]

            # remove expired timestamp using binary search

            idx = bisect.bisect_left(log, cutoff)
            self._logs[client_id] = log = log[idx:]

            if len(log) < cap:
                bisect.insort(log,now) # insert in sorted order
                self._stats["allowed"] += 1
                return True
            else:
                self._stats["rejected"] += 1
                return False


    def get_client_info(self, client_id:str) -> dict:
        # getting the current bucket info for the client

        with self._lock:
            now =  time.monotonic()
            cutoff = now - self.window_seconds
            log = self._logs.get(client_id, [])
            idx = bisect.bisect_left(log, cutoff)
            current_count = len(log) - idx
            

            response = {
                "requests_in_window" : current_count,
               
                "capacity": self.default_capacity,
                "window_seconds" : self.window_seconds,
                "tokens" : max(0, self.default_capacity - current_count)
            }

            return response


    def get_stats(self) -> dict:

        with self._lock:
            response = {
                **self._stats,
                "active_clients" : len(self._logs),
                "rejection_rate" : (
                    round(self._stats["rejected"]/self._stats["total_requests"]*100,2)
                    if self._stats["total_requests"] > 0 else 0
                ),
                "algorithm" : "sliding_window_log",
                "window_seconds" : self.window_seconds
            }

            return response

    def get_all_clients(self) -> list[dict]:

        with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_seconds
            result = []

            for cid, log in self._logs.items():
                idx = bisect.bisect_left(log, cutoff)
                current = len(log) - idx
                

                response = {
                    "client_id" : cid,
                    "tokens" : max(0, self.default_capacity - current),
                    "capacity" : self.default_capacity,
                    "requests_in_window" : current
                   
                }

                result.append(response)

            return result   

    def reset_client(self, client_id:str):
        # clear all timestamps for a client
        with self._lock:
           self._logs.pop(client_id, None)
                
