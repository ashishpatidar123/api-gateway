import math
import threading
from typing import Optional
import mmh3

class BloomFilter:

    def __init__(self, expected_items: int = 10000, false_positive_rate: float = 0.01):

        self._expected_items = expected_items
        self._fp_rate = false_positive_rate

        # calculate the optimal bit array size - m and number of hash functions - k 
        # m = -(n * ln(p)) / (ln(2)^2)
        # k = (m / n) * ln(2)
        self._m = self._optimal_bit_count( expected_items, false_positive_rate)
        self._k = self._optimal_hash_count(self._m, expected_items)

        self._bit_array = bytearray(math.ceil(self._m / 8))
        self._count = 0
        self._lock = threading.Lock()

    def add(self, item:str):
        # add an item to the bloom filter , mark as blocked

        with self._lock:
            for i in range(self._k):
                pos = self._hash(item, i) % self._m
                byte_idx = pos//8
                bit_idx = pos%8
                self._bit_array[byte_idx] |= (1 << bit_idx)
            self._count += 1

    def contains(self, item:str) -> bool:
        # check if an item might be in the set
        # returns true is probably present, or false if definitely not present

        with self._lock:
            for i in range(self._k):
                pos = self._hash(item, i) % self._m
                byte_idx = pos//8
                bit_idx = pos%8
                if not (self._bit_array[byte_idx] & (1 << bit_idx)):
                    return False

            return True

    def _hash(self, item:str, seed:int) -> int:
        return abs(mmh3.hash(item, seed))


    @staticmethod
    def _optimal_bit_count(n : int, p: float) -> int:
        m = -(n * math.log(p)) / (math.log(2) ** 2)
        return int(math.ceil(m))

    @staticmethod
    def _optimal_hash_count(m: int, n: int) -> int:
        k = (m/n) * math.log(2)
        return max(1, int(math.ceil(k)))

    def get_stats(self) -> dict:

        with self._lock:
            bits_set = sum(bin(byte).count('1') for byte in self._bit_array)

            fill_ratio = bits_set / self._m  if self._m > 0 else 0

            # actual false positive rate estimate
            actual_fp = (1 - math.exp(-self._k * self._count / self._m)) ** self._k  if self._m > 0 and self._count  else 0

            # sample bits for visualisation 

            sample_size = min(256, self._m)
            step = max(1, self._m // sample_size)
            bit_sample = []

            for i in range(0, min(self._m, sample_size * step), step):
                byte_idx = i//8
                bit_idx = i%8
                bit_sample.append(1 if (self._bit_array[byte_idx] & (1 << bit_idx)) else 0)

            response = {
                "items_count" : self._count,
                "bit_array_size": self._m,
                "hash_functions": self._k,
                "bits_set": bits_set,
                "fill_ratio" : round(fill_ratio * 100, 2),
                "expected_fp_rate": round(self._fp_rate * 100, 4),
                "estimated_actual_fp_rate" : round(actual_fp*100, 4),
                "bit_sample" : bit_sample
            }

            return response

    def clear(self):
        with self._lock:
            self._bit_array = bytearray(math.ceil(self._m/8))
            self._count = 0

            

