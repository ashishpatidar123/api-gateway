import secrets
import time
import threading
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("gateway.auth.apikey")

@dataclass
class ApiKeyRecord:
    key: str
    client_id: str
    role: str
    created_at: float
    last_used: float = 0.0
    request_count: int = 0


class ApiKeyAuthenticator:
    """API key management and validation."""

    def __init__(self):
        self._keys: dict[str, ApiKeyRecord] = {}
        self._lock = threading.Lock()

        # Seed a demo key
        self.generate_key("demo-client", "user")

    def generate_key(self, client_id: str, role: str = "user") -> str:
        """Generate a new API key for a client."""
        key = secrets.token_hex(16)
        with self._lock:
            self._keys[key] = ApiKeyRecord(
                key=key,
                client_id=client_id,
                role=role,
                created_at=time.time(),
            )
        logger.info("API key generated for client=%s role=%s", client_id, role)
        return key

    def revoke_key(self, key: str) -> bool:
        """Revoke an API key. Returns True if found and removed."""
        with self._lock:
            if key in self._keys:
                del self._keys[key]
                return True
        return False

    def validate(self, key: str) -> Optional[ApiKeyRecord]:
        """
        Validate an API key.
        Returns the ApiKeyRecord if valid, None otherwise.
        Updates last_used and request_count on successful validation.
        """
        with self._lock:
            record = self._keys.get(key)
            if record:
                record.last_used = time.time()
                record.request_count += 1
                return record
        return None

    def extract_key(self, headers: dict, query_params: dict) -> Optional[str]:
        """
        Extract API key from request headers or query parameters.
        Checks X-API-Key header first, then api_key query parameter.
        """
        # Header: X-API-Key
        key = headers.get("x-api-key") or headers.get("X-API-Key")
        if key:
            return key

        # Query parameter: ?api_key=...
        key = query_params.get("api_key")
        if key:
            return key

        return None

    def get_stats(self) -> dict:
        """Return API key auth statistics."""
        with self._lock:
            keys_info = []
            for record in self._keys.values():
                keys_info.append({
                    "key_prefix": record.key[:8] + "...",
                    "client_id": record.client_id,
                    "role": record.role,
                    "created_at": record.created_at,
                    "last_used": record.last_used,
                    "request_count": record.request_count,
                })
            return {
                "total_keys": len(self._keys),
                "keys": keys_info,
            }