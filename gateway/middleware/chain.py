"""
Request Interceptor Chain (Chain of Responsibility Pattern)

Each middleware in the chain can:
- Inspect/modify the request before forwarding
- Short-circuit the chain (e.g., reject blocked IPs, rate-limited requests)
- Inspect/modify the response after the handler completes
- Record metrics

Order: IP Filter -> Rate Limiter -> Auth -> Cache -> Proxy -> Response
"""

import asyncio
import time 
import logging
import uuid
from dataclasses import dataclass, field

from typing import Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from gateway.filter.bloom_filter import BloomFilter
from gateway.filter.cidr_trie import CidrTrie
from gateway.ratelimit.rate_limiter import RateLimiter
from gateway.auth.jwt_auth import JWTAuthenticator
from gateway.cache.lru_cache import LRUCache
from gateway.router.trie_router import TrieRouter, MatchResult
from gateway.metrics.collector import MetricsCollector
from gateway.auth.api_key_auth import ApiKeyAuthenticator

logger = logging.getLogger("gateway.middleware")

@dataclass
class RequestContext:
    """Carries state through the middleware chain."""
    request: Request
    client_ip: str
    client_id: str = "anonymous"
    role: str = "viewer"
    route_match: Optional[MatchResult] = None
    start_time: float = field(default_factory=time.time)
    cached: bool = False
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict = field(default_factory=dict)

class MiddlewareChain:
    """
    Orchestrates the request processing pipeline.
    Each step can short-circuit by returning a Response directly.
    """

    def __init__(
        self,
        router: TrieRouter,
        rate_limiter: RateLimiter,
        bloom_filter: BloomFilter,
        cidr_trie: CidrTrie,
        jwt_auth: JWTAuthenticator,
        cache: LRUCache,
        metrics: MetricsCollector,
        api_key_auth: ApiKeyAuthenticator = None, # type: ignore
    ):
        self.router = router
        self.rate_limiter = rate_limiter
        self.bloom_filter = bloom_filter
        self.cidr_trie = cidr_trie
        self.jwt_auth = jwt_auth
        self.cache = cache
        self.metrics = metrics
        self.api_key_auth = api_key_auth

    async def process(self, request: Request) -> tuple[Optional[Response], RequestContext]:
        """Run the full middleware chain. Returns (response, ctx).
        If response is None, the caller should proceed to proxy."""
        # Use incoming X-Request-ID for correlation, or generate a new one
        incoming_id = request.headers.get("X-Request-ID", "")
        ctx = RequestContext(
            request=request,
            client_ip=request.client.host if request.client else "127.0.0.1",
            request_id=incoming_id if incoming_id else str(uuid.uuid4()),
        )

        loop = asyncio.get_event_loop()

        # Step 1a: IP Blocklist - Bloom Filter (exact IP, probabilistic)
        response = await loop.run_in_executor(None, self._check_ip_block, ctx)
        if response:
            self.metrics.record_request(ctx, response.status_code, time.time() - ctx.start_time, step="ip_block")
            return response, ctx

        # Step 1b: IP Blocklist - CIDR Trie (prefix ranges, deterministic)
        response = await loop.run_in_executor(None, self._check_cidr_block, ctx)
        if response:
            self.metrics.record_request(ctx, response.status_code, time.time() - ctx.start_time, step="cidr_block")
            return response, ctx

        # Step 2: Route Matching (Trie)
        response = await loop.run_in_executor(None, self._match_route, ctx)
        if response:
            self.metrics.record_request(ctx, response.status_code, time.time() - ctx.start_time, step="route_match")
            return response, ctx

        # Step 3: Rate Limiting (Token Bucket)
        response = await loop.run_in_executor(None, self._check_rate_limit, ctx)
        if response:
            self.metrics.record_request(ctx, response.status_code, time.time() - ctx.start_time, step="rate_limit")
            return response, ctx

        # Step 4: Authentication (JWT)
        response = await loop.run_in_executor(None, self._check_auth, ctx)
        if response:
            self.metrics.record_request(ctx, response.status_code, time.time() - ctx.start_time, step="auth")
            return response, ctx

        # Step 5: Cache Lookup (LRU)
        cached_response = await loop.run_in_executor(None, self._check_cache, ctx)
        if cached_response:
            self.metrics.record_request(ctx, 200, time.time() - ctx.start_time, step="cache_hit")
            return cached_response, ctx

        # Step 6: Forward to backend (handled by the caller - main.py proxy)
        # Return None to signal "proceed to proxy"
        return None, ctx

    def _check_ip_block(self, ctx: RequestContext) -> Optional[JSONResponse]:
        """Step 1a: Check if client IP is in the Bloom filter blocklist."""
        if self.bloom_filter.contains(ctx.client_ip):
            logger.warning(f"Blocked IP (Bloom): {ctx.client_ip}")
            return JSONResponse(
                status_code=403,
                content={"error": "Forbidden", "detail": "IP address is blocked (bloom filter)"},
            )
        return None

    def _check_cidr_block(self, ctx: RequestContext) -> Optional[JSONResponse]:
        """Step 1b: Check if client IP matches any blocked CIDR range."""
        match = self.cidr_trie.longest_prefix_match(ctx.client_ip)
        if match:
            logger.warning(f"Blocked IP (CIDR): {ctx.client_ip} matched {match}")
            return JSONResponse(
                status_code=403,
                content={"error": "Forbidden", "detail": f"IP blocked by CIDR rule: {match}"},
            )
        return None

    def _match_route(self, ctx: RequestContext) -> Optional[JSONResponse]:
        """Step 2: Match the request path against the trie router."""
        match = self.router.match(ctx.request.method, ctx.request.url.path)
        if not match:
            return JSONResponse(
                status_code=404,
                content={"error": "Not Found", "detail": f"No route for {ctx.request.method} {ctx.request.url.path}"},
            )
        ctx.route_match = match
        return None

    def _check_rate_limit(self, ctx: RequestContext) -> Optional[JSONResponse]:
        """Step 3: Check rate limit per IP per route.
        Key format: '{client_ip}:{route_path}' - so each IP gets separate
        budgets for different routes based on their configured rate_limit."""
        route = ctx.route_match.route if ctx.route_match else None
        capacity = route.rate_limit if route and route.rate_limit else 60
        refill_rate = capacity / 60.0  # per second

        # Per-route key: each route gets its own bucket per client IP
        route_path = route.path if route else "unknown"
        rate_key = f"{ctx.client_ip}:{route_path}"

        if not self.rate_limiter.allow_request(rate_key, capacity=capacity, refill_rate=refill_rate):
            logger.warning(f"Rate limited: {ctx.client_ip} on {route_path}")
            bucket_info = self.rate_limiter.get_client_info(rate_key)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Too Many Requests",
                    "detail": f"Rate limit exceeded for {route_path}",
                    "route": route_path,
                },
                headers={
                    "X-RateLimit-Limit": str(capacity),
                    "X-RateLimit-Remaining": str(int(bucket_info.get("tokens", 0))),
                    "X-RateLimit-Route": route_path,
                    "Retry-After": "60",
                },
            )
        return None

    def _check_auth(self, ctx: RequestContext) -> Optional[JSONResponse]:
        """Step 4: Validate JWT token or API key if route requires authentication."""
        assert ctx.route_match is not None
        route = ctx.route_match.route
        if not route.requires_auth:
            return None

        # Try API key first (X-API-Key header or ?api_key= query param)
        if self.api_key_auth:
            headers_dict = dict(ctx.request.headers)
            query_dict = dict(ctx.request.query_params)
            api_key = self.api_key_auth.extract_key(headers_dict, query_dict)
            if api_key:
                record = self.api_key_auth.validate(api_key)
                if record:
                    # Check role hierarchy
                    role_hierarchy = {"admin": 3, "user": 2, "viewer": 1}
                    if route.required_role:
                        if role_hierarchy.get(record.role, 0) < role_hierarchy.get(route.required_role, 0):
                            return JSONResponse(
                                status_code=403,
                                content={"error": "Forbidden", "detail": f"Insufficient role: {record.role}, required: {route.required_role}"},
                            )
                    ctx.client_id = record.client_id
                    ctx.role = record.role
                    return None
                else:
                    return JSONResponse(
                        status_code=401,
                        content={"error": "Unauthorized", "detail": "Invalid API key"},
                    )

        # Fall back to JWT Bearer token
        auth_header = ctx.request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "detail": "Missing Bearer token or API key"},
            )

        token = auth_header[7:]
        allowed, claims, error_msg = self.jwt_auth.check_access(token, route.required_role) # type: ignore

        if not allowed:
            status = 403 if claims else 401
            return JSONResponse(
                status_code=status,
                content={"error": "Forbidden" if claims else "Unauthorized", "detail": error_msg},
            )

        assert claims is not None
        ctx.client_id = claims.sub
        ctx.role = claims.role
        return None

    def _check_cache(self, ctx: RequestContext) -> Optional[JSONResponse]:
        """Step 5: Check LRU cache for GET requests with cache_ttl set."""
        if ctx.request.method != "GET":
            return None

        assert ctx.route_match is not None
        route = ctx.route_match.route
        if not route.cache_ttl:
            return None

        cache_key = f"{ctx.request.method}:{ctx.request.url.path}:{ctx.request.url.query}"
        cached = self.cache.get(cache_key)
        if cached:
            ctx.cached = True
            logger.debug(f"Cache hit: {cache_key}")
            return JSONResponse(
                status_code=200,
                content=cached,
                headers={"X-Cache": "HIT"},
            )
        return None

    def store_in_cache(self, ctx: RequestContext, response_body: dict):
        """Store a response in the cache after proxying."""
        if ctx.request.method != "GET":
            return
        assert ctx.route_match is not None
        route = ctx.route_match.route
        if not route.cache_ttl:
            return
        cache_key = f"{ctx.request.method}:{ctx.request.url.path}:{ctx.request.url.query}"
        self.cache.put(cache_key, response_body, ttl=route.cache_ttl)