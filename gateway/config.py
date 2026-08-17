"""
Gateway configuration - routes, backends, and settings.
Hot-reloadable at runtime via the admin API.
"""

from gateway.router.trie_router import TrieRouter
from gateway.balancer.load_balancer import LoadBalancer, Strategy

def setup_routes(router: TrieRouter):
    """Register all gateway routes in the trie."""

    # Public routes (no auth)
    router.add_route("GET", "/api/v1/products", "product-service",
                     rate_limit=120, cache_ttl=30)
    router.add_route("GET", "/api/v1/products/:id", "product-service",
                     rate_limit=120, cache_ttl=60)
    router.add_route("GET", "/api/v1/health", "health-service",
                     rate_limit=300, cache_ttl=5)

    # Authenticated routes
    router.add_route("GET", "/api/v1/users", "user-service",
                     requires_auth=True, rate_limit=60, cache_ttl=15)
    router.add_route("GET", "/api/v1/users/:id", "user-service",
                     requires_auth=True, rate_limit=60, cache_ttl=15)
    router.add_route("POST", "/api/v1/users", "user-service",
                     requires_auth=True, required_role="admin", rate_limit=30)
    router.add_route("PUT", "/api/v1/users/:id", "user-service",
                     requires_auth=True, required_role="admin", rate_limit=30)
    router.add_route("DELETE", "/api/v1/users/:id", "user-service",
                     requires_auth=True, required_role="admin", rate_limit=10)

    # Order service (auth required)
    router.add_route("GET", "/api/v1/orders", "order-service",
                     requires_auth=True, rate_limit=60, cache_ttl=10)
    router.add_route("GET", "/api/v1/orders/:id", "order-service",
                     requires_auth=True, rate_limit=60, cache_ttl=10)
    router.add_route("POST", "/api/v1/orders", "order-service",
                     requires_auth=True, required_role="user", rate_limit=20)

    # Search (public, cached)
    router.add_route("GET", "/api/v1/search", "search-service",
                     rate_limit=100, cache_ttl=30)

    # Wildcard catch-all for static assets
    router.add_route("GET", "/static/*", "static-service",
                     rate_limit=300, cache_ttl=3600)

def setup_backends(balancer: LoadBalancer):
    """Register backend servers with the load balancer."""
    # In production these would be real service URLs.
    # Here we use the built-in mock backend endpoints.
    balancer.add_backend("http://localhost:9001", weight=3)
    balancer.add_backend("http://localhost:9002", weight=2)
    balancer.add_backend("http://localhost:9003", weight=1)