"""
Token bucket rate limiter

Algo : 
 - each client gets a bucker with a fixed capacity of tokens
 - tokens are added at a constant rate - refill rate per second
 - each request from the client consumes one tokens
 - if the bucket becomes empty it means no more token left, so no more request, thus HTTP 429
 - bucket never exceeds it's capacity

properties:
 - allows short bursts upto bucket capacity - means the user can rapidly sends requests 
   up to the bucket capacity
 - smooths out the traffic to the refill rate over time
 - O(1) per request ( no sliding window storage)
"""

import time
import threading
from dataclasses import dataclass
import token



@dataclass
class Bucket:
    tokens: float
    last_refill: float
    capacity: int
    refill_rate: float


class TokenBucketRateLimiter:
    # per-client token bucket limiter

    def __init__(self, default_capacity: int = 60, default_refill_rate: float = 1.0):

        self._buckets: dict[str, Bucket] = {}
        self._lock = threading.Lock()
        self.default_capacity = default_capacity
        self.default_refill_rate = default_refill_rate
        self._stats = {
            "total_requests" : 0,
            "allowed" : 0,
            "rejected" : 0
        }


    def allow_request(self, client_id:str, capacity: int = None, refill_rate: float = None) -> bool: # type: ignore

        # check if a request from client should be allowed or not,
        # returns true is allowed otherwise false

        cap = capacity or self.default_capacity
        rate = refill_rate  or self.default_refill_rate

        with self._lock:
            self._stats["total_requests"] +=1 
            now  =  time.monotonic()

            if client_id not in self._buckets:
                self._buckets[client_id] = Bucket(
                  tokens = cap,
                  last_refill= now,
                  capacity = cap,
                  refill_rate= rate,
                )

            bucket = self._buckets[client_id]

            # refill tokens here based on the elapsed time

            elapsed = now - bucket.last_refill
            tokens_to_add = elapsed * bucket.refill_rate
            bucket.tokens = min(bucket.capacity, bucket.tokens + tokens_to_add)
            bucket.last_refill = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                self._stats["allowed"] += 1
                return True
            else:
                self._stats["rejected"] += 1
                return False


    def get_client_info(self, client_id:str) -> dict:
        # getting the current bucket info for the client

        with self._lock:
            bucket = self._buckets.get(client_id)
            if not bucket:
                response = {
                    "tokens": self.default_capacity,
                    "capacity": self.default_capacity
                }
                return response

            now  = time.monotonic()

            elapsed = now - bucket.last_refill
            current = min(bucket.capacity, bucket.tokens + elapsed * bucket.refill_rate) 

            response = {
                "tokens" : round(current, 2),
                "capacity": bucket.capacity,
                "refill_rate" : bucket.refill_rate,
            }

            return response


    def get_stats(self) -> dict:

        with self._lock:
            response = {
                **self._stats,
                "active_clients" : len(self._buckets),
                "rejection_rate" : (
                    round(self._stats["rejected"]/self._stats["total_requests"]*100,2)
                    if self._stats["total_requests"] > 0 else 0
                ),
            }

            return response

    def get_all_clients(self) -> list[dict]:

        with self._lock:
            now = time.monotonic()
            result = []

            for cid, b in self._buckets.items():
                elapsed = now - b.last_refill
                current = min(b.capacity, b.tokens + elapsed * b.refill_rate)

                response = {
                    "client_id" : cid,
                    "tokens" : round(current, 2),
                    "capacity" : b.capacity,
                    "refill_rate" : b.refill_rate
                }

                result.append(response)

            return result   

    def reset_client(self, client_id:str):
        with self._lock:
            if client_id  in self._buckets:
                b = self._buckets[client_id]
                b.tokens = b.capacity
                b.last_refill = time.monotonic()

