"""
Gateway configuration - routes, backends, and settings.
Hot-reloadable at runtime via the admin API.
"""

from gateway.router.trie_router import TrieRouter
from gateway.balancer.load_balancer import LoadBalancer, Strategy

REAL_BACKENDS   = [
    ("https://jsonplaceholder.typicode.com",3),
    ("https://dummyjson.com",2)
]
MOCK_BACKENDS = [
    ("http://localhost:9001", 3),
    ("http://localhost:9002", 2),
    ("http://localhost:9003", 1)
]

def setup_routes(router: TrieRouter, use_mock: bool = True):
    """Register all gateway routes in the trie."""

    
    router.add_route("GET", "/api/v1/products", "product-service",
                     rate_limit=120, cache_ttl=30)
    router.add_route("GET", "/api/v1/products/:id", "product-service",
                     rate_limit=120, cache_ttl=60)
    router.add_route("GET", "/api/v1/health", "health-service",
                     rate_limit=300, cache_ttl=5)

    
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

    
    router.add_route("GET", "/api/v1/orders", "order-service",
                     requires_auth=True, required_role='user',rate_limit=60, cache_ttl=10)
    router.add_route("GET", "/api/v1/orders/:id", "order-service",
                     requires_auth=True, required_role='user',rate_limit=60, cache_ttl=10)
    router.add_route("POST", "/api/v1/orders", "order-service",
                     requires_auth=True, required_role="user", rate_limit=20)

    
    router.add_route("GET", "/api/v1/search", "search-service",
                     rate_limit=100, cache_ttl=30)

    
    router.add_route("GET", "/static/*", "static-service",
                     rate_limit=300, cache_ttl=3600)

    # -- Real API routes (work with JSONPlaceholder & DummyJSON) --
    if not use_mock:
        router.add_route("GET", "/posts", "api-service",
                        rate_limit=120, cache_ttl=30)
        router.add_route("GET", "/posts/:id", "api-service",
                        rate_limit=120, cache_ttl=60)
        router.add_route("GET", "/users", "api-service",
                        rate_limit=60, cache_ttl=15)
        router.add_route("GET", "/users/:id", "api-service",
                        rate_limit=60, cache_ttl=15)
        router.add_route("GET", "/todos", "api-service",
                        rate_limit=100, cache_ttl=30)
        router.add_route("GET", "/todos/:id", "api-service",
                        rate_limit=100, cache_ttl=60)
        router.add_route("GET", "/products", "api-service",
                        rate_limit=120, cache_ttl=30)
        router.add_route("GET", "/products/:id", "api-service",
                        rate_limit=120, cache_ttl=60)
        router.add_route("GET", "/comments", "api-service",
                        rate_limit=100, cache_ttl=30)


def setup_backends(balancer: LoadBalancer, use_mock: bool = True):
    """Register backend servers with the load balancer."""
    backends = MOCK_BACKENDS if use_mock else REAL_BACKENDS
    for url, weight in backends:
        balancer.add_backend(url, weight=weight)
