

import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger("gateway.proxy")

# Hop-by-hop headers that should NOT be forwarded
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
})

# When true, skip real proxying and use mock data (default for demo)
USE_MOCK = os.environ.get("USE_MOCK_BACKENDS", "true").lower() in ("true", "1", "yes")

class HttpProxy:
    """Async HTTP reverse proxy using httpx."""

    def __init__(self, timeout: float = 10.0):
        self._timeout = httpx.Timeout(timeout, connect=5.0)
        self._client: Optional[httpx.AsyncClient] = None

    async def startup(self):
        """Create the shared httpx client. Call during app lifespan startup."""
        self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
        logger.info("HttpProxy started (mock=%s, timeout=%.1fs)", USE_MOCK, self._timeout.read)

    async def shutdown(self):
        """Close the httpx client. Call during app lifespan shutdown."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("HttpProxy shut down")

    async def forward(
        self,
        method: str,
        backend_url: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        query_string: str = "",
    ) -> dict:
        """
        Forward a request to the backend and return the parsed response.

        Returns dict with keys: status_code, headers, body (parsed JSON or raw text).
        Raises httpx.HTTPError on network failures.
        """
        if self._client is None:
            raise RuntimeError("HttpProxy not started - call startup() first")

        url = f"{backend_url.rstrip('/')}/{path.lstrip('/')}"
        if query_string:
            url = f"{url}?{query_string}"

        # Filter out hop-by-hop headers
        forward_headers = {}
        if headers:
            for k, v in headers.items():
                if k.lower() not in HOP_BY_HOP:
                    forward_headers[k] = v

        response = await self._client.request(
            method=method,
            url=url,
            headers=forward_headers,
            content=body,
        )

        # Parse response body
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                resp_body = response.json()
            except Exception:
                resp_body = response.text
        else:
            resp_body = response.text

        # Filter response hop-by-hop headers
        resp_headers = {
            k: v for k, v in response.headers.items()
            if k.lower() not in HOP_BY_HOP
        }

        return {
            "status_code": response.status_code,
            "headers": resp_headers,
            "body": resp_body,
        }