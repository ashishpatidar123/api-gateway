"""Tests for Trie Router - prefix tree URL routing."""

from gateway.router.trie_router import TrieRouter


def _build_router():
    router = TrieRouter()
    router.add_route("GET", "/api/v1/products", "product-svc", rate_limit=100)
    router.add_route("GET", "/api/v1/products/:id", "product-svc", cache_ttl=60)
    router.add_route("POST", "/api/v1/users", "user-svc", requires_auth=True, required_role="admin")
    router.add_route("GET", "/static/*", "static-svc")
    return router


def test_exact_match():
    router = _build_router()
    match = router.match("GET", "/api/v1/products")
    assert match is not None
    assert match.route.backend == "product-svc"
    assert match.params == {}


def test_param_extraction():
    router = _build_router()
    match = router.match("GET", "/api/v1/products/42")
    assert match is not None
    assert match.params["id"] == "42"
    assert match.route.cache_ttl == 60


def test_wildcard_match():
    router = _build_router()
    match = router.match("GET", "/static/css/style.css")
    assert match is not None
    assert match.route.backend == "static-svc"


def test_no_match_returns_none():
    router = _build_router()
    assert router.match("GET", "/api/v1/nonexistent") is None


def test_method_mismatch_returns_none():
    router = _build_router()
    assert router.match("DELETE", "/api/v1/products") is None


def test_auth_metadata_preserved():
    router = _build_router()
    match = router.match("POST", "/api/v1/users")
    assert match is not None
    assert match.route.requires_auth is True
    assert match.route.required_role == "admin"