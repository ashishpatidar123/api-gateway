"""
Request/Response Transformer Middleware

Applies configurable transformations at the gateway layer:

Request transformations (before proxying):
- Header injection (add/override headers sent to backend)
- Header removal (strip sensitive client headers)
- Path rewriting (e.g., strip /api/v1 prefix before forwarding)

Response transformations (after proxying):
- Header injection (add gateway-specific response headers)
- Header removal (strip internal backend headers from client response)
- Body field mapping (rename/filter JSON fields)

All rules are stored in a list and applied in order.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("gateway.transformer")

class TransformPhase(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"

class TransformAction(str, Enum):
    ADD_HEADER = "add_header"
    REMOVE_HEADER = "remove_header"
    REWRITE_PATH = "rewrite_path"
    MAP_BODY_FIELD = "map_body_field"
    REMOVE_BODY_FIELD = "remove_body_field"

@dataclass
class TransformRule:
    """A single transformation rule."""
    phase: TransformPhase
    action: TransformAction
    key: str            # header name, path prefix, or field name
    value: str = ""     # replacement value, new header value, new field name
    route_pattern: str = "*" # which routes this applies to ("*" = all)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase.value,
            "action": self.action.value,
            "key": self.key,
            "value": self.value,
            "route_pattern": self.route_pattern,
        }

class RequestResponseTransformer:
    """Applies ordered transformation rules to requests and responses."""

    def __init__(self):
        self._rules: list[TransformRule] = []
        self._applied_count = 0

        # Register sensible defaults
        self._register_defaults()

    def _register_defaults(self):
        """Add default transformation rules."""
        # Request: inject gateway identifier header
        self.add_rule(TransformRule(
            phase=TransformPhase.REQUEST,
            action=TransformAction.ADD_HEADER,
            key="X-Forwarded-By",
            value="api-gateway",
        ))
        # Request: strip cookie from being forwarded (security)
        self.add_rule(TransformRule(
            phase=TransformPhase.REQUEST,
            action=TransformAction.REMOVE_HEADER,
            key="cookie",
        ))
        # Response: add strict transport security
        self.add_rule(TransformRule(
            phase=TransformPhase.RESPONSE,
            action=TransformAction.ADD_HEADER,
            key="Strict-Transport-Security",
            value="max-age=31536000; includeSubDomains",
        ))
        # Response: remove internal server header
        self.add_rule(TransformRule(
            phase=TransformPhase.RESPONSE,
            action=TransformAction.REMOVE_HEADER,
            key="server",
        ))

    def add_rule(self, rule: TransformRule):
        """Add a transformation rule."""
        self._rules.append(rule)

    def remove_rule(self, index: int) -> bool:
        """Remove a rule by index. Returns True if removed."""
        if 0 <= index < len(self._rules):
            self._rules.pop(index)
            return True
        return False

    def transform_request(self, headers: dict, path: str, route_pattern: str = "*") -> tuple[dict, str]:
        """
        Apply request-phase transformations.
        Returns (modified_headers, modified_path).
        """
        headers = dict(headers) # copy
        modified_path = path

        for rule in self._rules:
            if rule.phase != TransformPhase.REQUEST:
                continue
            if rule.route_pattern != "*" and rule.route_pattern != route_pattern:
                continue

            if rule.action == TransformAction.ADD_HEADER:
                headers[rule.key] = rule.value
                self._applied_count += 1

            elif rule.action == TransformAction.REMOVE_HEADER:
                key_lower = rule.key.lower()
                to_remove = [k for k in headers if k.lower() == key_lower]
                for k in to_remove:
                    del headers[k]
                    self._applied_count += 1

            elif rule.action == TransformAction.REWRITE_PATH:
                if modified_path.startswith(rule.key):
                    modified_path = rule.value + modified_path[len(rule.key):]
                    self._applied_count += 1

        return headers, modified_path

    def transform_response(self, headers: dict, body: dict, route_pattern: str = "*") -> tuple[dict, dict]:
        """
        Apply response-phase transformations.
        Returns (modified_headers, modified_body).
        """
        headers = dict(headers)
        body = dict(body) if isinstance(body, dict) else body

        for rule in self._rules:
            if rule.phase != TransformPhase.RESPONSE:
                continue
            if rule.route_pattern != "*" and rule.route_pattern != route_pattern:
                continue

            if rule.action == TransformAction.ADD_HEADER:
                headers[rule.key] = rule.value
                self._applied_count += 1

            elif rule.action == TransformAction.REMOVE_HEADER:
                key_lower = rule.key.lower()
                to_remove = [k for k in headers if k.lower() == key_lower]
                for k in to_remove:
                    del headers[k]
                    self._applied_count += 1

            elif rule.action == TransformAction.MAP_BODY_FIELD and isinstance(body, dict):
                if rule.key in body:
                    body[rule.value] = body.pop(rule.key)
                    self._applied_count += 1

            elif rule.action == TransformAction.REMOVE_BODY_FIELD and isinstance(body, dict):
                if rule.key in body:
                    del body[rule.key]
                    self._applied_count += 1

        return headers, body

    def get_stats(self) -> dict:
        """Return transformer statistics."""
        return {
            "total_rules": len(self._rules),
            "applied_count": self._applied_count,
            "rules": [r.to_dict() for r in self._rules],
        }