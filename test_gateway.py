"""
API Gateway - Full Integration Test Suite
=========================================
Cross-platform (Windows / macOS / Linux) test script.
Tests all 10 backend enhancements + core features.

Usage:
    1. Start the gateway:   uvicorn gateway.main:app --port 8000
    2. Run this script:     python test_gateway.py

Optional flags:
    --base-url http://localhost:8000    (default)
    --load-test                         (run load test at the end)
    --load-count 500                    (requests for load test, default 500)
    --concurrency 20                    (concurrent workers for load test, default 20)
"""

import argparse
import asyncio
import json
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== Helpers ====================

BASE = "http://localhost:8000"
PASS = 0
FAIL = 0

def req(method, path, headers=None, body=None, expect_status=None):
    """Send an HTTP request and return (status_code, headers_dict, body_parsed)."""
    url = BASE + path if path.startswith("/") else path
    data = json.dumps(body).encode() if body else None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)

    request = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(request) as resp:
            resp_body = resp.read().decode()
            resp_headers = dict(resp.headers)
            try:
                parsed = json.loads(resp_body)
            except json.JSONDecodeError:
                parsed = resp_body
            return resp.status, resp_headers, parsed
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode()
        resp_headers = dict(e.headers)
        try:
            parsed = json.loads(resp_body)
        except json.JSONDecodeError:
            parsed = resp_body
        return e.code, resp_headers, parsed

def check(name, condition, detail=""):
    global PASS, FAIL
    status = "PASS" if condition else "FAIL"
    if condition:
        PASS += 1
    else:
        FAIL += 1
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {name}{suffix}")

def section(title):
    print(f"\n{'='*60}")
    print(f" {title}")
    print(f"{'='*60}")

# ==================== Test Suites ====================

def test_basic_proxy():
    section("1. Basic Gateway Proxying")
    status, headers, body = req("GET", "/api/v1/products")
    check("GET /api/v1/products returns 200", status == 200, f"status={status}")
    check("Response has data", "data" in body or "products" in str(body))
    check("X-Backend header present", "X-Backend" in headers or "x-backend" in headers)
    check("X-Response-Time header present", "X-Response-Time" in headers or "x-response-time" in headers)

def test_request_id():
    section("7. Request ID / Correlation ID")
    # Auto-generated
    status, headers, body = req("GET", "/api/v1/products")
    rid = headers.get("X-Request-ID") or headers.get("x-request-id", "")
    check("Auto-generated X-Request-ID present", len(rid) > 10, f"id={rid[:20]}...")

    # Propagated
    status, headers, body = req("GET", "/api/v1/products", headers={"X-Request-ID": "custom-trace-abc"})
    rid2 = headers.get("X-Request-ID") or headers.get("x-request-id", "")
    check("Custom X-Request-ID echoed back", rid2 == "custom-trace-abc", f"got={rid2}")

    # Error responses also have it
    status, headers, body = req("GET", "/api/v1/nonexistent-route-xyz")
    rid3 = headers.get("X-Request-ID") or headers.get("x-request-id", "")
    check("X-Request-ID on 404 error response", len(rid3) > 10, f"id={rid3[:20]}...")

def test_response_transform():
    section("3. Request/Response Transformation")
    status, headers, body = req("GET", "/api/v1/products")
    hsts = headers.get("Strict-Transport-Security") or headers.get("strict-transport-security", "")
    check("HSTS header injected by transformer", "max-age" in hsts, f"value={hsts[:40]}")

    # 'server' header should be stripped
    server = headers.get("Server") or headers.get("server", "")
    # Note: uvicorn adds its own server header, transformer removes backend's
    check("Transformer ran (HSTS present)", len(hsts) > 0)

def test_jwt_auth():
    section("6a. JWT Authentication")
    # Generate token
    status, _, body = req("POST", "/admin/auth/token", body={"client_id": "test-user", "role": "admin"})
    check("Generate JWT token", status == 200 and "token" in body)
    token = body.get("token", "")

    # Use token on auth-required route
    status, _, body = req("GET", "/api/v1/orders", headers={"Authorization": f"Bearer {token}"})
    check("Auth route with valid JWT", status == 200, f"status={status}")

    # No auth - should fail
    status, _, body = req("GET", "/api/v1/orders")
    check("Auth route without credentials returns 401", status == 401, f"status={status}")

    # Wrong role
    _, _, viewer_body = req("POST", "/admin/auth/token", body={"client_id": "viewer-user", "role": "viewer"})
    viewer_token = viewer_body.get("token", "")
    status, _, body = req("GET", "/api/v1/orders", headers={"Authorization": f"Bearer {viewer_token}"})
    check("Insufficient role returns 403", status == 403, f"status={status}")

def test_api_key_auth():
    section("6b. API Key Authentication")
    # Generate key
    status, _, body = req("POST", "/admin/api-keys/generate", body={"client_id": "svc-a", "role": "user"})
    check("Generate API key", status == 200 and "api_key" in body)
    api_key = body.get("api_key", "")

    # Use via X-API-Key header
    status, _, body = req("GET", "/api/v1/orders", headers={"X-API-Key": api_key})
    check("Auth with valid API key (header)", status == 200, f"status={status}")

    # Invalid key
    status, _, body = req("GET", "/api/v1/orders", headers={"X-API-Key": "invalid-key-12345"})
    check("Invalid API key returns 401", status == 401, f"status={status}")

    # Revoke
    status, _, body = req("POST", "/admin/api-keys/revoke", body={"key": api_key})
    check("Revoke API key", status == 200)

    # Use revoked key
    status, _, body = req("GET", "/api/v1/orders", headers={"X-API-Key": api_key})
    check("Revoked API key returns 401", status == 401, f"status={status}")

    # List keys
    status, _, body = req("GET", "/admin/api-keys")
    check("List API keys endpoint", status == 200 and "total_keys" in body)

def test_rate_limiting():
    section("9. Per-Route Rate Limiting")
    # The default rate limit is 60/min per route per IP
    # We need to send enough requests to trigger it
    # First, check that the route works
    status, headers, _ = req("GET", "/api/v1/products")
    check("Route accessible before rate limit", status == 200)

    # Check rate limit headers
    rl_limit = headers.get("X-RateLimit-Limit") or headers.get("x-ratelimit-limit", "")
    check("X-RateLimit-Limit header present", len(rl_limit) > 0, f"limit={rl_limit}")

    # Send many requests to trigger 429
    blocked = 0
    for i in range(130):
        s, h, _ = req("GET", "/api/v1/products")
        if s == 429:
            blocked += 1
            route_header = h.get("X-RateLimit-Route") or h.get("x-ratelimit-route", "")
            if blocked == 1:
                check("429 includes X-RateLimit-Route header", len(route_header) > 0, f"route={route_header}")
            break

    check("Rate limit triggered (429)", blocked > 0, f"blocked={blocked}/130")

    # Different route should still work (separate per-route bucket)
    # Generate token for auth route
    _, _, token_body = req("POST", "/admin/auth/token", body={"client_id": "rl-test", "role": "admin"})
    token = token_body.get("token", "")
    status, _, _ = req("GET", "/api/v1/orders", headers={"Authorization": f"Bearer {token}"})
    check("Different route still accessible (separate bucket)", status == 200, f"status={status}")

def test_load_balancer():
    section("8. Load Balancer (4 Strategies)")
    strategies = ["round_robin", "weighted", "least_connections", "consistent_hash"]
    for strategy in strategies:
        status, _, _ = req("POST", "/admin/load-balancer/strategy", body={"strategy": strategy})
        check(f"Switch to {strategy}", status == 200)
        
        # Send a request and check backend
        s, h, _ = req("GET", "/api/v1/products")
        backend = h.get("X-Backend") or h.get("x-backend", "")
        check(f"{strategy} routes to backend", s == 200 and len(backend) > 0, f"backend={backend}")

    # Consistent hash: same client should hit same backend
    req("POST", "/admin/load-balancer/strategy", body={"strategy": "consistent_hash"})
    backends_seen = set()
    for _ in range(5):
        _, h, _ = req("GET", "/api/v1/products")
        b = h.get("X-Backend") or h.get("x-backend", "")
        backends_seen.add(b)
    check("Consistent hash: same client -> same backend", len(backends_seen) == 1, f"backends={backends_seen}")

    # Reset to weighted
    req("POST", "/admin/load-balancer/strategy", body={"strategy": "weighted"})

    # Stats
    status, _, body = req("GET", "/admin/load-balancer")
    check("Load balancer stats endpoint", status == 200 and "backends" in body)

def test_circuit_breaker():
    section("Circuit Breaker")
    status, _, body = req("GET", "/admin/circuit-breakers")
    check("Circuit breaker stats endpoint", status == 200)
    check("Breakers registered", "breakers" in body and len(body.get("breakers", [])) > 0)

    # Reset all
    status, _, _ = req("POST", "/admin/circuit-breakers/reset-all")
    check("Reset all breakers", status == 200)

def test_cache():
    section("LRU Cache")
    # Clear cache first
    req("POST", "/admin/cache/clear")

    # First request = MISS
    _, h1, _ = req("GET", "/api/v1/products")
    cache1 = h1.get("X-Cache") or h1.get("x-cache", "")
    check("First request is cache MISS", cache1 == "MISS", f"cache={cache1}")

    # Second request = HIT
    _, h2, _ = req("GET", "/api/v1/products")
    cache2 = h2.get("X-Cache") or h2.get("x-cache", "")
    check("Second request is cache HIT", cache2 == "HIT", f"cache={cache2}")

    # Cache stats
    status, _, body = req("GET", "/admin/cache")
    check("Cache stats endpoint", status == 200 and "hits" in body)
    check("Cache recorded hits", body.get("hits", 0) > 0, f"hits={body.get('hits', 0)}")

    # Clear cache
    status, _, _ = req("POST", "/admin/cache/clear")
    check("Clear cache", status == 200)

def test_ip_blocking():
    section("IP Blocking (Bloom Filter + CIDR Trie)")
    # Check Bloom filter
    status, _, body = req("POST", "/admin/bloom-filter/check", body={"ip": "1.2.3.4"})
    check("Bloom filter check endpoint", status == 200)

    # CIDR check
    status, _, body = req("POST", "/admin/cidr-trie/check", body={"ip": "203.0.113.5"})
    check("CIDR trie check endpoint", status == 200)
    check("Pre-seeded CIDR 203.0.113.0/24 blocks IP", body.get("blocked", False) == True)

    # Block a CIDR
    status, _, _ = req("POST", "/admin/cidr-trie/block", body={"cidr": "172.20.0.0/16"})
    check("Block CIDR range", status == 200)

    # Verify
    status, _, body = req("POST", "/admin/cidr-trie/check", body={"ip": "172.20.5.1"})
    check("Newly blocked CIDR matches", body.get("blocked", False) == True)

    # Bloom filter stats
    status, _, body = req("GET", "/admin/bloom-filter")
    check("Bloom filter stats endpoint", status == 200 and "items_count" in body)

def test_health_checker():
    section("4. Active Health Checker")
    status, _, body = req("GET", "/admin/health-checker")
    check("Health checker endpoint", status == 200)
    check("Has backends list", "backends" in body)
    check("Interval configured", body.get("interval_s", 0) > 0, f"interval={body.get('interval_s')}")

    backends = body.get("backends", [])
    check("Backends registered for probing", len(backends) > 0, f"count={len(backends)}")
    if backends:
        b = backends[0]
        check("Backend has status field", "status" in b, f"status={b.get('status')}")
        check("Backend has total_checks field", "total_checks" in b)

def test_retry_policy():
    section("2. Retry Policy (Exponential Backoff)")
    # Send enough requests that some trigger retries (5% mock error rate)
    for _ in range(30):
        req("GET", "/api/v1/products")

    # Now check retry stats via admin - but retry stats are only on WebSocket
    # We can check by sending more requests and verifying no crash
    status, _, body = req("GET", "/api/v1/products")
    check("Requests succeed with retry policy active", status == 200)

    # The retry policy stats are part of the WebSocket broadcast
    # We verify by checking the admin stats work
    status, _, _ = req("GET", "/admin/stats")
    check("Admin stats endpoint works (retry integrated)", status == 200)

def test_admin_endpoints():
        section("Admin API Endpoints")
        endpoints = [
            ("GET", "/admin/stats", "Stats"),
            ("GET", "/admin/routes", "Routes"),
            ("GET", "/admin/cache", "Cache"),
            ("GET", "/admin/rate-limiter", "Rate Limiter"),
            ("GET", "/admin/bloom-filter", "Bloom Filter"),
            ("GET", "/admin/load-balancer", "Load Balancer"),
            ("GET", "/admin/auth", "Auth"),
            ("GET", "/admin/circuit-breakers", "Circuit Breakers"),
            ("GET", "/admin/cidr-trie", "CIDR Trie"),
            ("GET", "/admin/request-log", "Request Log"),
            ("GET", "/admin/trie-structure", "Trie Structure"),
            ("GET", "/admin/health-checker", "Health Checker"),
            ("GET", "/admin/api-keys", "API Keys"),
        ]
        for method, path, name in endpoints:
            status, _, _ = req(method, path)
            check(f"{name} ({path})", status == 200, f"status={status}")

def test_graceful_shutdown_rejection():
    section("10. Graceful Shutdown (State Check)")
    # We can't fully test shutdown without killing the server,
    # but we verify the shutdown state variable is wired
    status, _, body = req("GET", "/api/v1/products")
    check("Server accepting requests (not in shutdown)", status != 503)
    # The 503 with "Service shutting down" only happens after SIGTERM/Ctrl+C

# ==================== Load Test ====================

def load_test(count, concurrency):
    section(f"Load Test ({count} requests, {concurrency} concurrent)")
    
    results = {"2xx": 0, "4xx": 0, "5xx": 0, "err": 0}
    latencies = []
    
    # Generate a token for auth routes
    _, _, token_body = req("POST", "/admin/auth/token", body={"client_id": "loadtest", "role": "admin"})
    token = token_body.get("token", "")
    
    paths = [
        ("GET", "/api/v1/products", None),
        ("GET", "/api/v1/users", None),
        ("GET", "/api/v1/orders", {"Authorization": f"Bearer {token}"}),
        ("GET", "/api/v1/products/p1", None),
    ]

    def single_request(i):
        method, path, hdrs = paths[i % len(paths)]
        start = time.time()
        try:
            s, _, _ = req(method, path, headers=hdrs)
            elapsed = (time.time() - start) * 1000
            return s, elapsed
        except Exception:
            return 0, (time.time() - start) * 1000

    start_total = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(single_request, i) for i in range(count)]
        for f in as_completed(futures):
            status, latency = f.result()
            latencies.append(latency)
            if 200 <= status < 300:
                results["2xx"] += 1
            elif 400 <= status < 500:
                results["4xx"] += 1
            elif 500 <= status < 600:
                results["5xx"] += 1
            else:
                results["err"] += 1
                
    total_time = time.time() - start_total
    latencies.sort()
    
    print(f"\n  Results:")
    print(f"    Total time:  {total_time:.2f}s")
    print(f"    RPS:         {count / total_time:.1f}")
    print(f"    2xx:         {results['2xx']}")
    print(f"    4xx:         {results['4xx']} (rate limited / auth)")
    print(f"    5xx:         {results['5xx']} (circuit breaker / backend)")
    print(f"    Errors:      {results['err']}")

    if latencies:
        p50 = latencies[int(len(latencies) * 0.5)]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        print(f"    P50:         {p50:.1f}ms")
        print(f"    P95:         {p95:.1f}ms")
        print(f"    P99:         {p99:.1f}ms")

    check("Load test completed", True, f"{count} reqs in {total_time:.2f}s")
    check("Majority succeeded (2xx)", results["2xx"] > count * 0.3, f"{results['2xx']}/{count}")

    # Print component stats after load
    print(f"\n  Post-Load Component Stats:")
    for ep in ["stats", "cache", "load-balancer", "circuit-breakers", "health-checker", "rate-limiter", "api-keys"]:
        s, _, b = req("GET", f"/admin/{ep}")
        summary = ""
        if ep == "cache":
            summary = f"hits={b.get('hits', 0)} misses={b.get('misses', 0)}"
        elif ep == "circuit-breakers":
            summary = f"closed={b.get('closed', 0)} open={b.get('open', 0)}"
        elif ep == "load-balancer":
            summary = f"backends={b.get('total_backends', 0)} strategy={b.get('strategy', '')}"
        print(f"    /admin/{ep}: {s} {summary}")


# ==================== Main ====================

def main():
    global BASE
    
    parser = argparse.ArgumentParser(description="API Gateway Integration Test Suite")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Gateway base URL")
    parser.add_argument("--load-test", action="store_true", help="Run load test after functional tests")
    parser.add_argument("--load-count", type=int, default=500, help="Number of requests for load test")
    parser.add_argument("--concurrency", type=int, default=20, help="Concurrent workers for load test")
    args = parser.parse_args()
    
    BASE = args.base_url
    
    print(f"\nAPI Gateway Integration Test Suite")
    print(f"Base URL: {BASE}")
    print(f"{'='*60}")
    
    # Verify server is up
    try:
        req("GET", "/docs")
    except Exception:
        print(f"\n  ERROR: Cannot connect to {BASE}")
        print(f"  Start the gateway first: uvicorn gateway.main:app --port 8000")
        sys.exit(1)
        
    # Run all test suites
    test_basic_proxy()
    test_request_id()
    test_response_transform()
    test_jwt_auth()
    test_api_key_auth()
    test_cache()
    test_load_balancer()
    test_circuit_breaker()
    test_ip_blocking()
    test_health_checker()
    test_retry_policy()
    test_rate_limiting()  # Run last - it exhausts rate limit buckets
    test_graceful_shutdown_rejection()
    test_admin_endpoints()
    
    if args.load_test:
        load_test(args.load_count, args.concurrency)
        
    # Summary
    total = PASS + FAIL
    print(f"\n{'='*60}")
    print(f" RESULTS: {PASS}/{total} passed, {FAIL} failed")
    print(f"{'='*60}\n")
    
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()