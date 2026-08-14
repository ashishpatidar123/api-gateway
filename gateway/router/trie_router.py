
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class RouteInfo:
    method: str
    path: str
    backend: str
    requires_auth: bool = False
    required_role: Optional[str] = None
    rate_limit: Optional[int] = None  # requests per minute
    cache_ttl: Optional[int] = None  # seconds

@dataclass
class TrieNode:
    children: dict = field(default_factory=dict)
    param_child: Optional['TrieNode'] = None
    param_name: Optional[str] = None
    wildcard_child: Optional['TrieNode'] = None
    routes: dict = field(default_factory=dict)  # method  RouteInfo

@dataclass
class MatchResult:
    route: RouteInfo
    params: dict
    path: str

class TrieRouter:
    # prefix-tree based URL router with parameter and wildcard support

    def __init__(self):
        self.root = TrieNode()
        self._routes: list[RouteInfo] = []

    def add_route(self, method: str, path: str, backend: str,
                  requires_auth: bool = False, required_role: str = None, # type: ignore
                  rate_limit: int = None, cache_ttl: int = None) -> RouteInfo: # type: ignore
        # register a route in the trie
        route = RouteInfo(
            method=method.upper(),
            path=path,
            backend=backend,
            requires_auth=requires_auth,
            required_role=required_role,
            rate_limit=rate_limit,
            cache_ttl=cache_ttl,
        )

        segments = self._split_path(path)
        node = self.root

        for seg in segments:
            if seg.startswith(":"):
                # Parameter segment
                if node.param_child is None:
                    node.param_child = TrieNode()
                    node.param_name = seg[1:]
                node = node.param_child
            elif seg == "*":
                # wildcard catchall
                if node.wildcard_child is None:
                    node.wildcard_child = TrieNode()
                node = node.wildcard_child
                break  # wildcard consumes everything after
            else:
                # Static segment
                if seg not in node.children:
                    node.children[seg] = TrieNode()
                node = node.children[seg]

        node.routes[method.upper()] = route
        self._routes.append(route)
        return route

    def match(self, method: str, path: str) -> Optional[MatchResult]:
        # Find a matching route for the given method and path
        segments = self._split_path(path)
        params = {}
        result = self._search(self.root, segments, 0, params, method.upper())
        return result

    def _search(self, node: TrieNode, segments: list, idx: int,
                params: dict, method: str) -> Optional[MatchResult]:
        #DFS through trie to find matching route
        if idx == len(segments):
            route = node.routes.get(method)
            if route:
                return MatchResult(route=route, params=dict(params), path=route.path)
            return None

        seg = segments[idx]

        # try exact static match first (highest priority)
        if seg in node.children:
            result = self._search(node.children[seg], segments, idx + 1, params, method)
            if result:
                return result

        # try parameter match
        if node.param_child is not None:
            params[node.param_name] = seg
            result = self._search(node.param_child, segments, idx + 1, params, method)
            if result:
                return result
            del params[node.param_name]

        # try wildcard match (lowest priority)
        if node.wildcard_child is not None:
            route = node.wildcard_child.routes.get(method)
            if route:
                params["wildcard"] = "/".join(segments[idx:])
                return MatchResult(route=route, params=dict(params), path=route.path)

        return None

    def get_all_routes(self) -> list[RouteInfo]:
        return list(self._routes)

    def get_trie_structure(self) -> dict:
        def _export(node: TrieNode, label: str = "/") -> dict:
            result = {"label": label, "children": [], "routes": list(node.routes.keys())}
            for seg, child in sorted(node.children.items()):
                result["children"].append(_export(child, seg))
            if node.param_child:
                result["children"].append(_export(node.param_child, ":" + (node.param_name or "param")))
            if node.wildcard_child:
                result["children"].append(_export(node.wildcard_child, "*"))
            return result
        return _export(self.root)

    @staticmethod
    def _split_path(path: str) -> list[str]:
        # split URL path into segments, filtering empty strings
        return [s for s in path.strip("/").split("/") if s]