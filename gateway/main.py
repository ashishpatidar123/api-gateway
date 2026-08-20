import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import random
import signal
import time
from turtle import back
from typing import Set, cast

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from torch.nn import TransformerEncoder


from gateway.auth.api_key_auth import ApiKeyAuthenticator
from gateway.auth.jwt_auth import JWTAuthenticator
from gateway.balancer.health_checker import HealthChecker
from gateway.balancer.load_balancer import LoadBalancer, Strategy
from gateway.cache.lru_cache import LRUCache
from gateway.circuitBreaker.circuit_breaker import CircuitBreakerManager, CircuitState
from gateway.config import setup_backends, setup_routes, MOCK_BACKENDS, REAL_BACKENDS
from gateway.filter.bloom_filter import BloomFilter
from gateway.filter.cidr_trie import CidrTrie
from gateway.log.ring_buffer import RequestLogEntry, RingBuffer
from gateway.metrics.collector import MetricsCollector
from gateway.middleware.chain import MiddlewareChain, RequestContext
from gateway.middleware.transformer import RequestResponseTransformer
from gateway.proxy.http_proxy import USE_MOCK, HttpProxy
from gateway.proxy.retry import RetryPolicy
from gateway.ratelimit.rate_limiter import RateLimitStrategy, RateLimiter
from gateway.router.trie_router import TrieRouter
from gateway.simulator.traffic_simulator import TrafficSimulator


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("gateway")

# ========================== Component Initialization ==========================

router = TrieRouter()
rate_limiter = RateLimiter(default_capacity=60, window_seconds=60.0)
cache = LRUCache(capacity=500, default_ttl=300)
bloom_filter = BloomFilter(expected_items=10000, false_positive_rate=0.01)
cidr_trie = CidrTrie()
jwt_auth = JWTAuthenticator()
load_balancer = LoadBalancer(strategy=Strategy.WEIGHTED)
metrics = MetricsCollector(window_seconds=60)
request_log = RingBuffer(capacity=1000)
circuit_breaker_manager = CircuitBreakerManager(failure_threshold=15, reset_timeout_s=30.0, window_s=60.0)
api_key_auth = ApiKeyAuthenticator()
middleware_chain = MiddlewareChain(router, rate_limiter, bloom_filter, cidr_trie, jwt_auth, cache, metrics, api_key_auth)
http_proxy = HttpProxy(timeout=10.0)
retry_policy = RetryPolicy(max_retries=3, base_delay=0.5, max_delay=10.0, jitter=0.25)
transformer = RequestResponseTransformer()
health_checker = HealthChecker(interval=10.0, timeout=5.0)
traffic_simulator = TrafficSimulator()

# WebSocket clients for live dashboard updates
ws_clients: Set[WebSocket] = set()
ws_clients_lock = asyncio.Lock()

# Graceful shutdown state
_shutting_down = False
_inflight = 0
_inflight_lock = asyncio.Lock()
_drain_event = asyncio.Event()


# ========================== Mock Backend ==========================

MOCK_DATA = {
    "product-service": {
        "GET": {
            "products": [
                {"id": "p1", "name": "Laptop Pro", "price": 1299.99, "stock": 45},
                {"id": "p2", "name": "Wireless Mouse", "price": 29.99, "stock": 200},
                {"id": "p3", "name": "USB-C Hub", "price": 49.99, "stock": 120},
                {"id": "p4", "name": "Monitor 27\"", "price": 399.99, "stock": 30},
            ]
        },
    },
    "user-service": {
        "GET": {
            "users": [
                {"id": "u1", "name": "Alice Johnson", "email": "alice@example.com", "role": "admin"},
                {"id": "u2", "name": "Bob Smith", "email": "bob@example.com", "role": "user"},
                {"id": "u3", "name": "Carol White", "email": "carol@example.com", "role": "viewer"},
            ]
        },
    },
    "order-service": {
        "GET": {
            "orders": [
                {"id": "o1", "user_id": "u1", "product_id": "p1", "status": "shipped", "total": 1299.99},
                {"id": "o2", "user_id": "u2", "product_id": "p2", "status": "pending", "total": 29.99},
            ]
        },
    },
    "search-service": {
        "GET": {"results": [{"id": "p1", "name": "Laptop Pro", "score": 0.95}]},
    },
    "health-service": {
        "GET": {"status": "healthy", "uptime": "99.99%"},
    },
    "static-service": {
        "GET": {"message": "Static content served"},
    },
}

async def mock_backend_response(service: str, method: str, path: str, params: dict) -> dict:
    """Simulate a backend service response with random latency."""
    await asyncio.sleep(random.uniform(0.01, 0.15))  # Simulate 10-150ms latency

    # Simulate occasional errors (5% chance)
    if random.random() < 0.05:
        raise Exception("Backend service temporarily unavailable")
    
    service_data = MOCK_DATA.get(service, {}).get(method, {})

    # If path has an :id param, filter the list
    if "id" in params and service_data:
        for key, items in service_data.items():
            if isinstance(items, list):
                match = [i for i in items if i.get("id") == params["id"]]
                if match:
                    return {"data": match[0], "service": service}
                return {"error": "Not found", "service": service}

    return {"data": service_data, "service": service, "timestamp": time.time()}


# ========================== Lifespan ==========================

async def broadcast_metrics():
    """Periodically broadcast metrics to WebSocket clients."""
    while True:
        await asyncio.sleep(2)
        
        # Safely check and copy the current clients
        async with ws_clients_lock:
            if not ws_clients:
                continue
            clients = set(ws_clients)
            
        try:
            # Gather all metrics
            snapshot = metrics.get_dashboard_snapshot()
            snapshot["rate_limiter"] = {"stats": rate_limiter.get_stats(), "clients": rate_limiter.get_all_clients()}
            snapshot["cache"] = cache.get_stats()
            snapshot["bloom_filter"] = bloom_filter.get_stats()
            snapshot["cidr_trie"] = cidr_trie.get_stats()
            snapshot["load_balancer"] = load_balancer.get_stats()
            snapshot["circuit_breaker"] = circuit_breaker_manager.get_stats()
            snapshot["request_log"] = {"stats": request_log.get_stats(), "recent": request_log.get_recent(30)}
            snapshot["health_checker"] = health_checker.get_stats()
            snapshot["retry"] = retry_policy.get_stats()
            snapshot["transformer"] = transformer.get_stats()
            snapshot["simulator"] = traffic_simulator.get_stats()

            data = json.dumps(snapshot)
            disconnected = set()
            
            # Iterate over the COPY (clients) so the app doesn't crash if the main set changes
            for ws in clients:
                try:
                    await ws.send_text(data)
                except Exception:
                    disconnected.add(ws)
                    
            # Safely remove disconnected clients without triggering the Pylance unbound error
            if disconnected:
                async with ws_clients_lock:
                    ws_clients.difference_update(disconnected)
                    
        except Exception as e:
            logger.error("broadcast_metrics_error: %s", e, exc_info=True)

            

async def cache_cleanup():
    """Periodically clean up expired cache entries."""
    while True:
        await asyncio.sleep(30)
        removed = cache.cleanup_expired()
        if removed > 0:
            logger.info(f"Cache cleanup: removed {removed} expired entries")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    setup_routes(router, use_mock=USE_MOCK)
    setup_backends(load_balancer, use_mock=USE_MOCK)

    # Register all backends with the circuit breaker manager and health checker
    backend_list = MOCK_BACKENDS if USE_MOCK else REAL_BACKENDS
    for backend_url, weight in backend_list:
        circuit_breaker_manager.register_backend(backend_url)
        health_checker.register_backend(backend_url)

    # Wire health checker status changes to load balancer
    def on_health_change(url: str, healthy: bool):
        load_balancer.set_health(url, healthy)
        logger.info(f"Health check: {url} -> {'healthy' if healthy else 'unhealthy'}")
    health_checker.set_status_change_callback(on_health_change)

    # Seed some blocked IPs for demo (Bloom Filter - exact IPs)
    for ip in ["192.168.1.100", "10.0.0.99", "172.16.0.50"]:
        bloom_filter.add(ip)

    # Seed some blocked CIDR ranges for demo (Binary Trie - prefix ranges)
    for cidr in ["203.0.113.0/24", "198.51.100.0/24"]:
        cidr_trie.add(cidr)

    logger.info(f"Gateway started with {len(router.get_all_routes())} routes")
    logger.info(f"Load balancer: {load_balancer.get_stats()['total_backends']} backends")
    logger.info(f"Bloom filter: {bloom_filter.get_stats()['items_count']} blocked IPs")

    # Start http proxy client
    await http_proxy.startup()

    # Wire and start traffic simulator
    async def sim_request_handler(method, path, client_ip, latency_ms, is_failure):
        """Handle a simulated request - record metrics and log entry."""
        import uuid

        # Lightweight mock request to satisfy RequestContext and MetricsCollect
        class _SimUrl:
            def __init__(self, p): self.path = p
        class _SimRequest:
            def __init__(self, m, p):
                self.method = m
                self.url = _SimUrl(p)

        ctx = RequestContext(
            request=cast(Request, _SimRequest(method, path)), 
            client_ip=client_ip,
            request_id=f"sim-{uuid.uuid4().hex[:8]}",
            start_time=time.time() - (latency_ms / 1000.0),
        )
        ctx.route_match = router.match(method, path)
        status_code = 502 if is_failure else 200
        duration_s = latency_ms / 1000.0
        metrics.record_request(ctx, status_code, duration_s, step="proxy")

        # Pick a backend for realism
        backend = load_balancer.select_backend(routing_key=client_ip)
        backend_url = backend.url if backend else "sim-backend"

        if backend:
            load_balancer.acquire_connection(backend)
            load_balancer.release_connection(backend, latency_ms, success=not is_failure)

        # Record in circuit breaker
        if backend and not is_failure:
            circuit_breaker_manager.record_success(backend_url)
            
                

        # Consume a rate-limit token
        rate_limiter.allow_request(client_ip, capacity=120)

        retry_policy._total_calls += 1
        if is_failure:
            retries = random.randint(1,3)
            retry_policy._total_retries += retries
            if random.random() < 0.25:
                retry_policy._total_exhausted += 1

        route_match = ctx.route_match
        route_pat = route_match.route.path if route_match and route_match.route else '*'
        transformer.transform_response({}, {}, route_pat)

        # Cache interaction for GET requests
        if method == "GET" and not is_failure:
            cache_key = f"{method}:{path}"
            cached = cache.get(cache_key)
            if cached is None:
                cache.put(cache_key, {"sim": True}, ttl=30)

        request_log.append(RequestLogEntry(
            timestamp=time.time(), method=method, path=path,
            status_code=status_code, latency_ms=latency_ms,
            client_ip=client_ip, pipeline_step="proxy", backend=backend_url,
        ))

    traffic_simulator.wire(sim_request_handler)

    # Override simulator routes for real backends (different URL paths)
    if not USE_MOCK:
        traffic_simulator._config.route_weights = [
            ("GET", "/posts",       30),
            ("GET", "/posts/1",     15),
            ("GET", "/posts/2",      8),
            ("GET", "/users",       12),
            ("GET", "/users/1",      6),
            ("GET", "/todos",       10),
            ("GET", "/todos/1",      5),
            ("GET", "/products",    15),
            ("GET", "/products/1",   8),
            ("GET", "/comments",    10),
        ]
        # Rebuild pre-computed route selection lists
        traffic_simulator._routes = []
        traffic_simulator._route_weights = []
        for method, path, weight in traffic_simulator._config.route_weights:
            traffic_simulator._routes.append((method, path))
            traffic_simulator._route_weights.append(weight)
        
    traffic_simulator.start()

    # Start background tasks
    metrics_task = asyncio.create_task(broadcast_metrics())
    cleanup_task = asyncio.create_task(cache_cleanup())
    if not USE_MOCK:
        health_checker._health_path = "/"
        await health_checker.start()
    

    # Register SIGTERM handler for graceful shutdown
    loop = asyncio.get_event_loop()
    _sigint_count = 0

    def _handle_sigterm():
        nonlocal _sigint_count
        global _shutting_down
        _sigint_count += 1
        if _sigint_count >= 2:
            logger.info("Second interrrupt - forcing exit")
            os._exit(1)
        _shutting_down = True
        _drain_event.set()
        logger.info("SIGTERM received - draining in-flight requests...")
        
    try:
        loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
        loop.add_signal_handler(signal.SIGINT, _handle_sigterm)
    except NotImplementedError:
        # Windows: fall back to standard signal module
        signal.signal(signal.SIGINT, lambda s, f: _handle_sigterm())
        signal.signal(signal.SIGTERM, lambda s, f: _handle_sigterm())

    yield

    # Graceful drain: wait for in-flight requests (up to 30s)
    logger.info("Shutdown initiated - waiting for in-flight requests to complete...")
    try:
        await asyncio.wait_for(_drain_event.wait(), timeout=30.0)
    except asyncio.TimeoutError:
        async with _inflight_lock:
            remaining = _inflight
        logger.warning(f"Drain timeout: {remaining} requests still in-flight")

    metrics_task.cancel()
    cleanup_task.cancel()
    await health_checker.stop()
    await http_proxy.shutdown()
    logger.info("Gateway shut down gracefully")


# ========================== FastAPI App ==========================

app = FastAPI(
    title="API Gateway",
    description="Distributed Rate-Limited API Gateway with Trie Routing, LRU Cache, Bloom Filter, JWT Auth, and Load Balancing",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========================== Gateway Proxy (catch-all) ==========================

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def gateway_proxy(request: Request, path: str):
    """
    Main gateway entry point.
    Runs the middleware chain, then proxies to the mock backend.
    """
    global _inflight

    # Reject new requests during shutdown drain
    if _shutting_down:
        return JSONResponse(
            status_code=503,
            content={"error": "Service shutting down", "detail": "Connection draining in progress"},
            headers={"Connection": "close", "Retry-After": "5"},
        )

    # Track in-flight requests for graceful drain
    async with _inflight_lock:
        _inflight += 1
        
    try:
        return await _handle_proxy(request, path)
    finally:
        async with _inflight_lock:
            _inflight -= 1
            if _shutting_down and _inflight == 0:
                _drain_event.set()


async def _handle_proxy(request: Request, path: str):
    """Internal proxy handler - separated for drain tracking."""
    # Run middleware chain - returns (response, ctx) to avoid redundant context creation
    response, ctx = await middleware_chain.process(request)
    if response is not None:
        response.headers["X-Request-ID"] = ctx.request_id
        if ctx.cached and ctx.route_match:
            route = ctx.route_match.route
            route_pat = route.path if route else "*"
            extra_headers, _ = transformer.transform_response({}, {}, route_pat)
            for k, v in extra_headers.items():
                response.headers[k] = v
            capacity = route.rate_limit if route.rate_limit else 60
            rate_key = f"{ctx.client_ip}:{route.path}"
            bucket_info = rate_limiter.get_client_info(rate_key)
            response.headers["X-RateLimit-Limit"] = str(capacity)
            response.headers["X-RateLimit-Remaining"] = str(int(bucket_info.get("tokens", 0)))
        return response

    # Middleware passed - route match is already on ctx from the chain
    match = ctx.route_match
    if not match:
        return JSONResponse(status_code=404, content={"error": "Route not found"}, headers={"X-Request-ID": ctx.request_id})

    # Select backend via load balancer
    backend = load_balancer.select_backend(routing_key=ctx.client_ip)
    if not backend:
        metrics.record_request(ctx, 503, time.time() - ctx.start_time, step="no_backend")
        return JSONResponse(status_code=503, content={"error": "No healthy backends available"}, headers={"X-Request-ID": ctx.request_id})

    # Circuit Breaker check - reject if backend's breaker is OPEN
    if not circuit_breaker_manager.allow_request(backend.url):
        cb_state = circuit_breaker_manager.get_backend_state(backend.url)
        total_ms = (time.time() - ctx.start_time) * 1000
        metrics.record_request(ctx, 503, time.time() - ctx.start_time, step="circuit_breaker")
        request_log.append(RequestLogEntry(
            timestamp=time.time(), method=request.method, path=request.url.path,
            status_code=503, latency_ms=round(total_ms, 2),
            client_ip=ctx.client_ip, pipeline_step="circuit_breaker", backend=backend.url,
        ))
        return JSONResponse(
            status_code=503,
            content={
                "error": "Service Unavailable",
                "detail": f"Circuit breaker is {cb_state.value if cb_state else 'open'} for backend {backend.url}",
            },
            headers={"X-Circuit-Breaker": "OPEN", "X-Backend": backend.url, "X-Request-ID": ctx.request_id},
        )

    load_balancer.acquire_connection(backend)
    start = time.time()

    try:
        # Use real HTTP proxy or mock backend based on USE_MOCK_BACKENDS env var
        if USE_MOCK:
            result = await retry_policy.execute(
                mock_backend_response,
                match.route.backend, request.method, request.url.path, match.params,
            )
            resp_status = 200
        else:
            req_body = await request.body()
            proxy_headers = dict(request.headers)
            proxy_result = await retry_policy.execute(
                http_proxy.forward,
                method=request.method,
                backend_url=backend.url,
                path=request.url.path,
                headers=proxy_headers,
                body=req_body if req_body else None,
                query_string=str(request.url.query) if request.url.query else "",
            )
            result = proxy_result["body"]
            resp_status = proxy_result["status_code"]

        duration_ms = (time.time() - start) * 1000
        load_balancer.release_connection(backend, duration_ms, success=True)
        circuit_breaker_manager.record_success(backend.url)

        # Auto-restore health when breaker closes after half-open probe
        cb_state = circuit_breaker_manager.get_backend_state(backend.url)
        if cb_state == CircuitState.CLOSED:
            load_balancer.set_health(backend.url, True)

        # Store in cache if applicable
        if isinstance(result, dict):
            middleware_chain.store_in_cache(ctx, result)

        total_ms = (time.time() - ctx.start_time) * 1000
        metrics.record_request(ctx, resp_status, time.time() - ctx.start_time, step="proxy")
        
        # Log to ring buffer
        request_log.append(RequestLogEntry(
            timestamp=time.time(), method=request.method, path=request.url.path,
            status_code=resp_status, latency_ms=round(total_ms, 2),
            client_ip=ctx.client_ip, pipeline_step="proxy", backend=backend.url,
        ))

        resp_headers = {
            "X-Cache": "MISS",
            "X-Backend": backend.url,
            "X-Response-Time": f"{duration_ms:.2f}ms",
            "X-Circuit-Breaker": cb_state.value if cb_state else "unknown",
            "X-Request-ID": ctx.request_id,
        }

        if match and match.route:
            rl_cap = match.route.rate_limit if match.route.rate_limit else 60
            rl_key = f"{ctx.client_ip}:{match.route.path}"
            rl_info = rate_limiter.get_client_info(rl_key)
            resp_headers["X-RateLimit-Limit"] = str(rl_cap)
            resp_headers["X-RateLimit-Remaining"] = str(int(rl_info.get("tokens", 0)))

        route_pat = match.route.path if match else "*"
        resp_body = result if isinstance(result, dict) else {"data": result}
        resp_headers, resp_body = transformer.transform_response(resp_headers, resp_body, route_pat)

        return JSONResponse(
            status_code=resp_status,
            content=resp_body,
            headers=resp_headers,
        )

    except Exception as e:
        duration_ms = (time.time() - start) * 1000
        load_balancer.release_connection(backend, duration_ms, success=False)
        circuit_breaker_manager.record_failure(backend.url)

        # Auto-mark backend unhealthy when breaker trips OPEN
        cb_state = circuit_breaker_manager.get_backend_state(backend.url)
        if cb_state == CircuitState.OPEN:
            load_balancer.set_health(backend.url, False)

        total_ms = (time.time() - ctx.start_time) * 1000
        metrics.record_request(ctx, 502, time.time() - ctx.start_time, step="backend_error")

        request_log.append(RequestLogEntry(
            timestamp=time.time(), method=request.method, path=request.url.path,
            status_code=502, latency_ms=round(total_ms, 2),
            client_ip=ctx.client_ip, pipeline_step="backend_error", backend=backend.url,
        ))

        return JSONResponse(
            status_code=502,
            content={"error": "Backend Error", "detail": str(e)},
            headers={"X-Circuit-Breaker": cb_state.value if cb_state else "unknown", "X-Request-ID": ctx.request_id},
        )


# ========================== Admin API ==========================

@app.get("/admin/stats")
async def get_stats():
    """Full metrics snapshot."""
    return metrics.get_stats()

@app.get("/admin/routes")
async def get_routes():
    """List all registered routes."""
    routes = router.get_all_routes()
    return [
        {
            "method": r.method,
            "path": r.path,
            "backend": r.backend,
            "requires_auth": r.requires_auth,
            "required_role": r.required_role,
            "rate_limit": r.rate_limit,
            "cache_ttl": r.cache_ttl,
        }
        for r in routes
    ]

@app.get("/admin/cache")
async def get_cache_stats():
    """Cache statistics and entries."""
    return {
        "stats": cache.get_stats(),
        "entries": cache.get_entries(limit=50),
    }

@app.post("/admin/cache/clear")
async def clear_cache():
    """Clear the entire cache."""
    cache.clear()
    return {"message": "Cache cleared"}

@app.get("/admin/rate-limiter")
async def get_rate_limiter():
    """Rate limiter statistics."""
    return {
        "stats": rate_limiter.get_stats(),
        "clients": rate_limiter.get_all_clients(),
    }

@app.post("/admin/rate-limiter/{client_id}/reset")
async def reset_rate_limit(client_id: str):
    """Reset rate limit for a specific client."""
    rate_limiter.reset_client(client_id)
    return {"message": f"Rate limit reset for {client_id}"}

@app.post("/admin/rate-limiter/strategy")
async def set_rl_strategy(request: Request):
    """Change rate limiting algorithm at runtime."""
    body = await request.json()
    strategy_name = body.get("strategy", "token_bucket")
    try:
        strategy = RateLimitStrategy(strategy_name)
        rate_limiter.set_strategy(strategy)
        return {"message": f"Rate limiter strategy changed to {strategy.value}"}
    except ValueError:
        available = [s.value for s in RateLimitStrategy]
        return JSONResponse(status_code=400, content={"error": f"Unknown strategy: {strategy_name}", "available": available})

@app.get("/admin/bloom-filter")
async def get_bloom_filter():
    """Bloom filter statistics."""
    return bloom_filter.get_stats()

@app.post("/admin/bloom-filter/block")
async def block_ip(request: Request):
    """Add an IP to the blocklist."""
    body = await request.json()
    ip = body.get("ip")
    if not ip:
        return JSONResponse(status_code=400, content={"error": "Missing 'ip' field"})
    try:
        bloom_filter.add(ip)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"message": f"IP {ip} added to blocklist"}

@app.post("/admin/bloom-filter/check")
async def check_ip(request: Request):
    """Check if an IP is in the blocklist."""
    body = await request.json()
    ip = body.get("ip")
    if not ip:
        return JSONResponse(status_code=400, content={"error": "Missing 'ip' field"})
    try:
        blocked = bloom_filter.contains(ip)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"ip": ip, "blocked": blocked}

@app.get("/admin/load-balancer")
async def get_load_balancer():
    """Load balancer statistics."""
    return load_balancer.get_stats()

@app.post("/admin/load-balancer/strategy")
async def set_lb_strategy(request: Request):
    """Change load balancing strategy at runtime."""
    body = await request.json()
    strategy_name = body.get("strategy", "round_robin")
    try:
        strategy = Strategy(strategy_name)
        load_balancer.set_strategy(strategy)
        return {"message": f"Strategy changed to {strategy.value}"}
    except ValueError:
        return JSONResponse(status_code=400, content={"error": f"Unknown strategy: {strategy_name}"})

@app.post("/admin/load-balancer/{url:path}/health")
async def set_backend_health(url: str, request: Request):
    """Set backend health status."""
    body = await request.json()
    healthy = body.get("healthy", True)
    load_balancer.set_health(f"http://{url}", healthy)
    return {"message": f"Backend http://{url} set to {'healthy' if healthy else 'unhealthy'}"}

@app.get("/admin/health-checker")
async def get_health_checker():
    """Health checker statistics and per-backend probe results."""
    return health_checker.get_stats()

@app.get("/admin/auth")
async def get_auth_stats():
    """Auth statistics."""
    return jwt_auth.get_stats()

@app.post("/admin/auth/token")
async def generate_token(request: Request):
    """Generate a JWT token for testing."""
    body = await request.json()
    client_id = body.get("client_id", "test-client")
    role = body.get("role", "user")
    expiry = body.get("expiry_seconds", 3600)
    token = jwt_auth.generate_token(client_id, role, expiry)
    return {"token": token, "client_id": client_id, "role": role, "expires_in": expiry}

# ========================== API Key Admin ==========================

@app.get("/admin/api-keys")
async def get_api_keys():
    """API key statistics."""
    return api_key_auth.get_stats()

@app.post("/admin/api-keys/generate")
async def generate_api_key(request: Request):
    """Generate a new API key."""
    body = await request.json()
    client_id = body.get("client_id", "api-client")
    role = body.get("role", "user")
    key = api_key_auth.generate_key(client_id, role)
    return {"api_key": key, "client_id": client_id, "role": role}

@app.post("/admin/api-keys/revoke")
async def revoke_api_key(request: Request):
    """Revoke an API key."""
    body = await request.json()
    key = body.get("key")
    if not key:
        return JSONResponse(status_code=400, content={"error": "Missing 'key' field"})
    if api_key_auth.revoke_key(key):
        return {"message": "API key revoked"}
    return JSONResponse(status_code=404, content={"error": "API key not found"})

# ========================== Circuit Breaker Admin ==========================

@app.get("/admin/circuit-breakers")
async def get_circuit_breakers():
    """Circuit breaker statistics for all backends."""
    return circuit_breaker_manager.get_stats()

@app.post("/admin/circuit-breakers/{url:path}/reset")
async def reset_circuit_breaker(url: str):
    """Reset a specific backend's circuit breaker to CLOSED."""
    full_url = f"http://{url}"
    if circuit_breaker_manager.reset_backend(full_url):
        load_balancer.set_health(full_url, True)
        return {"message": f"Circuit breaker reset for {full_url}", "state": "closed"}
    return JSONResponse(status_code=404, content={"error": f"No circuit breaker found for {full_url}"})

@app.post("/admin/circuit-breakers/reset-all")
async def reset_all_circuit_breakers():
    """Reset all circuit breakers to CLOSED."""
    circuit_breaker_manager.reset_all()
    # Also restore all backends to healthy
    stats = circuit_breaker_manager.get_stats()
    for b in stats["breakers"]:
        load_balancer.set_health(b["backend_url"], True)
    return {"message": "All circuit breakers reset", "total": stats["total_breakers"]}

# ========================== CIDR Trie Admin ==========================

@app.get("/admin/cidr-trie")
async def get_cidr_trie():
    """CIDR binary trie statistics."""
    return cidr_trie.get_stats()

@app.post("/admin/cidr-trie/block")
async def cidr_block(request: Request):
    """Add a CIDR range to the blocklist. e.g. '10.0.0.0/8' or '192.168.1.100'."""
    body = await request.json()
    cidr = body.get("cidr")
    if not cidr:
        return JSONResponse(status_code=400, content={"error": "Missing 'cidr' field"})
    try:
        cidr_trie.add(cidr)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"message": f"CIDR {cidr} added to blocklist"}

@app.post("/admin/cidr-trie/unblock")
async def cidr_unblock(request: Request):
    """Remove a CIDR range from the blocklist."""
    body = await request.json()
    cidr = body.get("cidr")
    if not cidr:
        return JSONResponse(status_code=400, content={"error": "Missing 'cidr' field"})
    try:
        removed = cidr_trie.remove(cidr)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    if removed:
        return {"message": f"CIDR {cidr} removed from blocklist"}
    return JSONResponse(status_code=404, content={"error": f"CIDR {cidr} not found"})

@app.post("/admin/cidr-trie/check")
async def cidr_check(request: Request):
    """Check if an IP matches any CIDR block."""
    body = await request.json()
    ip = body.get("ip")
    if not ip:
        return JSONResponse(status_code=400, content={"error": "Missing 'ip' field"})
    try:
        match = cidr_trie.longest_prefix_match(ip)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"ip": ip, "blocked": match is not None, "matched_cidr": match}

# ========================== Request Log (Ring Buffer) ==========================

@app.get("/admin/request-log")
async def get_request_log():
    """Get recent request log entries from the ring buffer."""
    return {
        "stats": request_log.get_stats(),
        "entries": request_log.get_recent(100),
    }

@app.post("/admin/request-log/clear")
async def clear_request_log():
    """Clear the ring buffer."""
    request_log.clear()
    return {"message": "Request log cleared"}

# ==================== Traffic Simulator ====================

@app.get("/admin/simulator")
async def get_simulator_status():
    """Get traffic simulator status."""
    return traffic_simulator.get_stats()


@app.post("/admin/simulator/toggle")
async def toggle_simulator():
    """Toggle the traffic simulator on/off."""
    new_state = traffic_simulator.toggle()
    return {"enabled": new_state, "message": f"Traffic simulator {'enabled' if new_state else 'disabled'}"}


# ========================== WebSocket ==========================

@app.websocket("/ws/metrics")
async def websocket_metrics(websocket: WebSocket):
    """WebSocket endpoint for live metrics."""
    await websocket.accept()
    async with ws_clients_lock:
        ws_clients.add(websocket)
        client_count = len(ws_clients)
    logger.info(f"WebSocket client connected. Total: {client_count}")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with ws_clients_lock:
            ws_clients.discard(websocket)
            client_count = len(ws_clients)
        logger.info(f"WebSocket client disconnected. Total: {client_count}")

# ========================== Trie Structure ==========================

@app.get("/admin/trie-structure")
async def get_trie_structure():
    """Export the URL routing trie for visualization."""
    return router.get_trie_structure()

# ========================== Dashboard ==========================

_dashboard_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard")


@app.get("/", response_class=HTMLResponse)
async def root_redirect():
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/dashboard")


# 1. Create a safe wrapper to gracefully close non-HTTP / WebSocket scopes
class GuardWS:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1000})
            return
        await self.app(scope, receive, send)

# 2. Mount using the GuardWS wrapper
app.mount("/dashboard", GuardWS(StaticFiles(directory=_dashboard_dir, html=True)), name="dashboard-static")
