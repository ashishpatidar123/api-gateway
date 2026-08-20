"""Integration tests for the full API Gateway middleware pipeline.

Tests the end-to-end flow: request -> IP filter -> route match -> rate limit -> auth -> cache -> proxy -> response.
Uses FastAPI TestClient to simulate real HTTP requests against the gateway.
"""

import pytest
from fastapi.testclient import TestClient

from gateway.main import app, bloom_filter, cidr_trie, rate_limiter, cache, jwt_auth, load_balancer


@pytest.fixture(autouse=True)
def reset_state():
    """Reset stateful components between tests."""
    cache.clear()
    rate_limiter.set_strategy(rate_limiter.active_strategy)
    yield


@pytest.fixture
def client():
    return TestClient(app)


# ==================== Basic Proxy Flow ====================

class TestProxyFlow:
    """Test the full request lifecycle through the gateway."""

    def test_successful_get_returns_200(self, client):
        response = client.get("/api/v1/products")
        assert response.status_code in (200, 502)  # 502 if mock backend random error
        if response.status_code == 200:
            data = response.json()
            assert "data" in data or "products" in str(data)

    def test_response_includes_gateway_headers(self, client):
        response = client.get("/api/v1/products")
        if response.status_code == 200:
            assert "X-Cache" in response.headers
            assert "X-Backend" in response.headers
            assert "X-Response-Time" in response.headers

    def test_unknown_route_returns_404(self, client):
        response = client.get("/api/v1/nonexistent")
        assert response.status_code == 404
        assert "error" in response.json()


# ==================== IP Blocking (Bloom Filter + CIDR Trie) ====================

class TestIPBlocking:
    """Test IP filtering via Bloom Filter and CIDR Trie."""

    def test_bloom_filter_block_ip_via_admin(self, client):
        response = client.post(
            "/admin/bloom-filter/block",
            json={"ip": "1.2.3.4"},
        )
        assert response.status_code == 200

        check = client.post(
            "/admin/bloom-filter/check",
            json={"ip": "1.2.3.4"},
        )
        assert check.json()["blocked"] is True

    def test_bloom_filter_unblocked_ip(self, client):
        check = client.post(
            "/admin/bloom-filter/check",
            json={"ip": "99.99.99.99"},
        )
        assert check.json()["blocked"] is False

    def test_cidr_block_and_check(self, client):
        client.post("/admin/cidr-trie/block", json={"cidr": "10.0.0.0/8"})
        check = client.post("/admin/cidr-trie/check", json={"ip": "10.1.2.3"})
        assert check.json()["blocked"] is True
        assert check.json()["matched_cidr"] == "10.0.0.0/8"

    def test_cidr_unblock(self, client):
        client.post("/admin/cidr-trie/block", json={"cidr": "172.20.0.0/16"})
        unblock = client.post("/admin/cidr-trie/unblock", json={"cidr": "172.20.0.0/16"})
        assert unblock.status_code == 200
        check = client.post("/admin/cidr-trie/check", json={"ip": "172.20.1.1"})
        assert check.json()["blocked"] is False

    def test_invalid_ip_returns_400(self, client):
        response = client.post(
            "/admin/cidr-trie/check",
            json={"ip": "not-an-ip"},
        )
        assert response.status_code == 400

    def test_invalid_cidr_returns_400(self, client):
        response = client.post(
            "/admin/cidr-trie/block",
            json={"cidr": "10.0.0.0/99"},
        )
        assert response.status_code == 400


# ==================== Rate Limiting ====================

class TestRateLimiting:
    """Test rate limiting across strategies."""

    def test_rate_limiter_strategy_switch(self, client):
        for strategy in ["token_bucket", "sliding_window_log", "sliding_window_counter"]:
            response = client.post(
                "/admin/rate-limiter/strategy",
                json={"strategy": strategy},
            )
            assert response.status_code == 200

    def test_invalid_strategy_returns_400(self, client):
        response = client.post(
            "/admin/rate-limiter/strategy",
            json={"strategy": "nonexistent"},
        )
        assert response.status_code == 400

    def test_rate_limiter_stats(self, client):
        response = client.get("/admin/rate-limiter")
        assert response.status_code == 200
        data = response.json()
        assert "stats" in data
        assert "clients" in data


# ==================== Authentication (JWT) ====================

class TestAuthentication:
    """Test JWT token generation and auth-protected routes."""

    def test_generate_token(self, client):
        response = client.post(
            "/admin/auth/token",
            json={"client_id": "test-user", "role": "admin"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "token" in data
        assert data["role"] == "admin"

    def test_protected_route_without_token_returns_401(self, client):
        response = client.post("/api/v1/users")
        assert response.status_code == 401

    def test_protected_route_with_valid_token(self, client):
        token_resp = client.post(
            "/admin/auth/token",
            json={"client_id": "admin-user", "role": "admin"},
        )
        token = token_resp.json()["token"]
        response = client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Should pass auth - either 200 or 502 (mock backend error)
        assert response.status_code in (200, 502)

    def test_protected_route_with_insufficient_role(self, client):
        token_resp = client.post(
            "/admin/auth/token",
            json={"client_id": "viewer-user", "role": "viewer"},
        )
        token = token_resp.json()["token"]
        response = client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


# ==================== Cache ====================

class TestCache:
    """Test LRU caching through the gateway."""

    def test_cache_stats(self, client):
        response = client.get("/admin/cache")
        assert response.status_code == 200
        data = response.json()
        assert "stats" in data

    def test_cache_clear(self, client):
        response = client.post("/admin/cache/clear")
        assert response.status_code == 200


# ==================== Load Balancer ====================

class TestLoadBalancer:
    """Test load balancer admin endpoints."""

    def test_get_stats(self, client):
        response = client.get("/admin/load-balancer")
        assert response.status_code == 200
        data = response.json()
        assert "strategy" in data
        assert "backends" in data

    def test_strategy_switch(self, client):
        for strategy in ["round_robin", "weighted", "least_connections"]:
            response = client.post(
                "/admin/load-balancer/strategy",
                json={"strategy": strategy},
            )
            assert response.status_code == 200

# ==================== Circuit Breaker ====================

class TestCircuitBreaker:
    """Test circuit breaker admin endpoints."""

    def test_get_circuit_breakers(self, client):
        response = client.get("/admin/circuit-breakers")
        assert response.status_code == 200
        data = response.json()
        assert "total_breakers" in data

    def test_reset_all_breakers(self, client):
        response = client.post("/admin/circuit-breakers/reset-all")
        assert response.status_code == 200

# ==================== Routes & Metrics ====================

class TestAdminEndpoints:
    """Test admin introspection endpoints."""

    def test_routes_list(self, client):
        response = client.get("/admin/routes")
        assert response.status_code == 200
        routes = response.json()
        assert isinstance(routes, list)
        assert len(routes) > 0
        assert "method" in routes[0]
        assert "path" in routes[0]

    def test_stats_endpoint(self, client):
        response = client.get("/admin/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_requests" in data

    def test_request_log(self, client):
        response = client.get("/admin/request-log")
        assert response.status_code == 200
        data = response.json()
        assert "stats" in data

    def test_dashboard_html(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "API Gateway Dashboard" in response.text