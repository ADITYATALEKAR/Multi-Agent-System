"""Authentication middleware for OAuth2/JWT token handling."""

from __future__ import annotations

import base64
import json
import time

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)


class TokenPayload(BaseModel):
    """Decoded JWT-like token payload."""

    sub: str  # user_id
    tenant_id: str = "default"
    roles: list[str] = Field(default_factory=list)
    exp: float = 0.0


class AuthMiddleware:
    """Authenticates incoming API requests using base64-encoded JSON tokens.

    Uses a simplified token scheme for testing and development.
    In production this would delegate to a proper JWT library with
    asymmetric key verification.
    """

    def __init__(self, secret: str = "dev-secret") -> None:
        self._secret = secret

    # ------------------------------------------------------------------
    # Token lifecycle
    # ------------------------------------------------------------------

    def create_token(
        self,
        user_id: str,
        tenant_id: str = "default",
        roles: list[str] | None = None,
        ttl_seconds: int = 3600,
    ) -> str:
        """Create a base64-encoded JSON token (simulated JWT).

        Args:
            user_id: Unique identifier for the user.
            tenant_id: Tenant scope for multi-tenant isolation.
            roles: List of role strings (e.g. ``["admin", "reader"]``).
            ttl_seconds: Token time-to-live in seconds.

        Returns:
            A URL-safe base64-encoded token string.
        """
        payload = {
            "sub": user_id,
            "tenant_id": tenant_id,
            "roles": roles or [],
            "exp": time.time() + ttl_seconds,
            "secret": self._secret,
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        token = base64.urlsafe_b64encode(raw).decode()
        log.info("token_created", user_id=user_id, tenant_id=tenant_id)
        return token

    def verify_token(self, token: str) -> TokenPayload | None:
        """Decode and validate a token string.

        Returns:
            A ``TokenPayload`` on success, or ``None`` when the token is
            invalid or expired.
        """
        try:
            raw = base64.urlsafe_b64decode(token.encode())
            data = json.loads(raw)
        except Exception:
            log.warning("token_decode_failed")
            return None

        # Verify the embedded secret matches
        if data.get("secret") != self._secret:
            log.warning("token_secret_mismatch")
            return None

        # Check expiry
        if data.get("exp", 0) < time.time():
            log.warning("token_expired", sub=data.get("sub"))
            return None

        log.debug("token_verified", sub=data.get("sub"))
        return TokenPayload(
            sub=data["sub"],
            tenant_id=data.get("tenant_id", "default"),
            roles=data.get("roles", []),
            exp=data.get("exp", 0.0),
        )

    # ------------------------------------------------------------------
    # Role enforcement
    # ------------------------------------------------------------------

    def require_role(self, token: TokenPayload, role: str) -> bool:
        """Return ``True`` if *role* is present in the token's role list."""
        return role in token.roles
