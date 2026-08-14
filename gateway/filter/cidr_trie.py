# binary trie for CIDR prefix matching
# algo - 
# - converts ip addresses to 32 bit binary 
# - stores CIDR block as prefixes in a binary trie (0/1 children)
# - each internal node has at most 2 children:  left(0), right(1)
# - to insert 10.0.0.0/8 - walk the first 8 bits of 10.0.0.0, mark the final node
# - to check an IP - walk all 32 bits, if any node on the path is marked - blocked

from re import L
import threading
from typing import Optional
from dataclasses import dataclass, field

from jwt.utils import bytes_to_number

from sympy import false, isprime

@dataclass
class TrieNode:
    # binary trie node, children[0] = left (bit 0), children[1] = right (bit 1)

    children: list = field(default_factory=lambda : [None, None])
    is_prefix_end: bool = false
    cidr: Optional[str] = None # the cidr notation stored at this prefix

class CidrTrie:
    # binary trie for IPv4 CIDR block matching

    def __init__(self):
        self._root = TrieNode()
        self._count = 0
        self._lock = threading.Lock()
        self._cidrs: list[str] = [] # ordered insert list for display


    @staticmethod
    def _validate_ip(ip: str):
        # validate IPv4 address format. Raises error is invalid

        parts = ip.split('.')
        if len(parts) != 4:
            raise ValueError(f"Invalid IP address (expected 4 octets): {ip}")
        for part in parts:
            try:
                val = int(part)
            except ValueError:
                raise ValueError(f"Invalid IP address (non numeric octet): {ip}")

            if val < 0 or val > 255:
                raise ValueError(f"Invalid IP address (octet out of range 0-255): {ip}")

    @staticmethod
    def _parse_cidr(cidr: str) -> tuple[str, int]:
        # parse 10.0.0.0/8 into ('10.0.0.0' , 8). Plain IP - /32

        if '/' in cidr:
            ip, prefix_len = cidr.split('/')
            prefix_len = int(prefix_len)
            if prefix_len < 0 or prefix_len > 32:
                raise ValueError(f"Invalid CIDR prefix length (must be 0-32): {cidr}")
            return ip, prefix_len
        return cidr, 32

    @staticmethod
    def _ip_to_bits(ip: str) -> list[int]:
        # convert dotted decimal IP to 32 bit lists
        # '10.0.0.1' - [0,0,0,0,1,0,1,0, 0,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,1]

        parts = ip.split('.')
        bits = []
        for part in parts:
            val  = int(part)
            for i in range(7, -1, -1):
                bits.append((val >> i)&1)

        return bits


    def add(self, cidr: str):
        # add a CIDR block to the trie
        # ex - '10.0.0.0/8' , '192.168.1.0/24' , '172.16.0.50' (exact /32)

        ip, prefix_len = self._parse_cidr(cidr)
        self._validate_ip(ip)
        bits = self._ip_to_bits(ip)

        with self._lock:
            node = self._root
            for i in range (prefix_len):
                bit = bits[i]
                if node.children[bit]  is None:
                    node.children[bit] = TrieNode()
                node = node.children[bit]
            node.is_prefix_end = True
            node.cidr = cidr
            self._count += 1
            if cidr not in self._cidrs:
                self._cidrs.append(cidr)


    def contains(self, ip:str) -> bool:
        # check if an IP matches any CIDR block in the trie

        self._validate_ip(ip)
        bits = self._ip_to_bits(ip)

        with self._lock:
            node = self._root
            if node.is_prefix_end:
                return True

            for bit in bits:
                if node.children[bit] is None:
                    return False
                node  = node.children[bit]
                if node.is_prefix_end:
                    return True

            return False

    def longest_prefix_match(self, ip:str) -> Optional[str]:
        # find the most longest CIDR matching this IP
        # used for determining which rule applies when range overlap

        self._validate_ip(ip)
        bits = self._ip_to_bits(ip)
        best_match = None

        with self._lock:
            node = self._root
            if node.is_prefix_end:
                best_match = node.cidr


            for bit in bits:
                if node.children[bit] is None:
                    break

                node = node.children[bit]
                if node.is_prefix_end:
                    best_match = node.cidr

        return best_match

    def remove(self, cidr: str) -> bool:
        # remove a CIDR block from the trie, prunes empty leaf nodes

        ip, prefix_len = self._parse_cidr(cidr)
        self._validate_ip(ip)
        bits = self._ip_to_bits(ip)

        with self._lock:
            # walk to the target node, recording the oath
            path = [(self._root, -1)] # node, bit taken

            node = self._root
            for i in range(prefix_len):
                bit  = bits[i]
                if node.children[bit] is None:
                    return False

                node = node.children[bit]
                path.append((node, bit))

            if not node.is_prefix_end:
                return False

            node.is_prefix_end = False
            node.cidr = None
            self._count -= 1
            if cidr in self._cidrs:
                self._cidrs.remove(cidr)


            for i in range(len(path) - 1, 0, -1):
                child_node, bit = path[i]
                if child_node.is_prefix_end or child_node.children[0] or child_node.children[1]:
                    break
                parent_node = path[i-1][0]
                parent_node.children[bit] = None

        return True

    def _count_nodes(self, node: Optional[TrieNode] = None) -> int:
            # count total nodes in the trie
            if node is None:
                node = self._root
    
            count   = 1
            for child in node.children:
                if child is not None:
                    count += self._count_nodes(child)
            return count
    
    def _max_depth(self, node: Optional[TrieNode] = None, depth: int  = 0) -> int:
        # find the maximum depth (longest prefix stored)
        if node is None:
            node = self._root

        max_d = depth
        for child in node.children:
            if child is not None:
                max_d = max(max_d, self._max_depth(child, depth+1))
        return max_d

    def get_stats(self)-> dict:
        # return the stats for the ui side
        with self._lock:
            response = {
                "cidr_count" : self._count,
                "trie_nodes" : self._count_nodes(),
                "max_depth" : self._max_depth(),
                "cidrs" : self._cidrs[:30]
            }

            return response

    def clear(self):
        with self._lock:
            self._root = TrieNode()
            self._count = 0
            self._cidrs.clear()