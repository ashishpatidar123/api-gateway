
import os
import time
from dataclasses import dataclass
from typing import Optional

import jwt

SECRET_KEY = os.environ.get("JWT_SECRET", "api-gateway-secret-key-dev-only")
ALGORITHM = "HS256"
DEFAULT_EXPIRY_SECONDS = 3600  # 1 hour

@dataclass
class TokenClaims:
    sub: str        # subject (client_id or username)
    role: str       # admin, user, viewer
    exp: float      # expiry timestamp
    iat: float      # issued at
    iss: str = "api-gateway"

    def is_expired(self) -> bool:
        return time.time() > self.exp

    def has_role(self, required_role: str) -> bool:
        # check if the token's role meets the required access level
        role_hierarchy = {"admin": 3, "user": 2, "viewer": 1}
        return role_hierarchy.get(self.role, 0) >= role_hierarchy.get(required_role, 0)

class JWTAuthenticator:
    

    def __init__(self, secret_key: str = SECRET_KEY, algorithm: str = ALGORITHM):
        self._secret = secret_key
        self._algorithm = algorithm
        self._issued_tokens: list[dict] = []

    def generate_token(self, client_id: str, role: str = "user",
                       expiry_seconds: int = DEFAULT_EXPIRY_SECONDS) -> str:
        # generate a signed token
        now = time.time()
        payload = {
            "sub": client_id,
            "role": role,
            "iat": now,
            "exp": now + expiry_seconds,
            "iss": "api-gateway",
        }
        token = jwt.encode(payload, self._secret, algorithm=self._algorithm)

        self._issued_tokens.append({
            "client_id": client_id,
            "role": role,
            "issued_at": now,
            "expires_at": now + expiry_seconds,
        })

        # Keep only last 100 records
        if len(self._issued_tokens) > 100:
            self._issued_tokens = self._issued_tokens[-100:]

        return token

    def validate_token(self, token: str) -> Optional[TokenClaims]:
        # validating and decoding the jwt token, returns none if invalid or expired
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
            return TokenClaims(
                sub=payload["sub"],
                role=payload.get("role", "viewer"),
                exp=payload["exp"],
                iat=payload["iat"],
                iss=payload.get("iss", ""),
            )
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    def check_access(self, token: str, required_role: str = None) -> tuple[bool, Optional[TokenClaims], str]:
    
        # Full access check: validates token + checks role.
        # Returns (allowed, claims, error message).
        
        claims = self.validate_token(token)
        if claims is None:
            return False, None, "Invalid or expired token"
        if claims.is_expired():
            return False, None, "Token expired"
        if required_role and not claims.has_role(required_role):
            return False, claims, f"Insufficient role: {claims.role}, required: {required_role}"
        return True, claims, ""

    def get_stats(self) -> dict:
        
        now = time.time()
        active = sum(1 for t in self._issued_tokens if t["expires_at"] > now)
        return {
            "total_issued": len(self._issued_tokens),
            "active_tokens": active,
            "recent_tokens": self._issued_tokens[-10:],
        }