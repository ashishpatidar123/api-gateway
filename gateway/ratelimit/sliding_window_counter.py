"""
sliding window counter rate limiter

algo:
 - combines the fixed window counting with interpolation for smooth sliding behaviour
 - maintains 2 fixed windoes per client, previous and current
 - on each request calculates weighted count : 
    weighted = prev_count * (1 - elapsed_fraction) + current_count
 - if wieghted count is less than limit then allow otw reject

properties:
 - O(log 1) per request
 - very low memory, only 2 counters
  
"""

from http import client
from itertools import count
import time
import threading

from dataclasses import dataclass

from sympy import frac, rem

@dataclass
class WindowCounter:
    prev_count: int = 0
    curr_count: int = 0
    window_start: float = 0.0
    capacity: int = 60



class SlidingWindowCounterRateLimiter:
    # per-client sliding window log rate limiter

    def __init__(self, default_capacity: int = 60, window_seconds: float = 60.0):

        self._counters: dict[str, WindowCounter] = {}
        self._lock = threading.Lock()

        self.default_capacity = default_capacity
        self.window_seconds = window_seconds
        self._stats = {
            "total_requests" : 0,
            "allowed" : 0,
            "rejected" : 0
        }

    def _advance_window(self, counter : WindowCounter, now: float):
        # advance fixed windows if time has elapsed past boundaries

        elapsed = now - counter.window_start

        if elapsed >= 2 * self.window_seconds:
            counter.prev_count = 0
            counter.curr_count = 0
            counter.window_start = now

        elif elapsed >= self.window_seconds:
            counter.prev_count = counter.curr_count
            counter.curr_count = 0
            counter.window_start += self.window_seconds


    def _weighted_count(self, counter:WindowCounter, now:float) -> float:

        # calculated the interpolated requsst count
        # weighted = prev_count * (1 - elapsed_fraction) + current_count

        elapsed = now - counter.window_start
        fraction = elapsed / self.window_seconds if self.window_seconds > 0 else 1
        fraction = min(1.0, max(0.0, fraction))
        weighted = counter.prev_count * (1 - fraction) + counter.curr_count

        return weighted


    def allow_request(self, client_id:str, capacity: int = None) -> bool: # type: ignore

        # check if a request from client should be allowed or not,
        

        cap = capacity or self.default_capacity
        

        with self._lock:
            self._stats["total_requests"] +=1 
            now  =  time.monotonic()

            if client_id not in self._counters:
                self._counters[client_id] = WindowCounter(window_start=now, capacity=cap)

            counter = self._counters[client_id]
            self._advance_window(counter, now)

            weighted = self._weighted_count(counter, now)

            if weighted < cap:
                counter.curr_count += 1
                self._stats["allowed"] += 1
                return True
            else:
                self._stats["rejected"] += 1
                return False


    def get_client_info(self, client_id:str) -> dict:
        # getting the counter state for the client

        with self._lock:
            counter  = self._counters.get(client_id)

            if not counter:
                response = {
                    "tokens" : self.default_capacity,
                    "capacity" : self.default_capacity
                }

                return response

            now = time.monotonic()
            self._advance_window(counter, now)

            weighted = self._weighted_count(counter, now)

            remaining = max(0, int(counter.capacity - weighted))



            response = {
                "weighted_count" : round(weighted, 2),
               
                "capacity": self.default_capacity,
                "window_seconds" : self.window_seconds,
                "tokens" :remaining
            }

            return response


    def get_stats(self) -> dict:

        with self._lock:
            response = {
                **self._stats,
                "active_clients" : len(self._counters),
                "rejection_rate" : (
                    round(self._stats["rejected"]/self._stats["total_requests"]*100,2)
                    if self._stats["total_requests"] > 0 else 0
                ),
                "algorithm" : "sliding_window_counter",
                "window_seconds" : self.window_seconds
            }

            return response

    def get_all_clients(self) -> list[dict]:

        with self._lock:
            now = time.monotonic()
            
            result = []

            for cid, counter in self._counters.items():
                now = time.monotonic()
                self._advance_window(counter, now)

                weighted = self._weighted_count(counter, now)

                remaining = max(0, int(counter.capacity - weighted))
                

                response = {
                    "client_id" : cid,
                    "tokens" : remaining,
                    "capacity" : self.default_capacity,
                    "weighted_count" : round(weighted,2)
                   
                }

                result.append(response)

            return result   

    def reset_client(self, client_id:str):
        # reset counters for a client
        with self._lock:
           self._counters.pop(client_id, None)
                
