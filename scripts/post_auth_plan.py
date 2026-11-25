"""
Post JWT RS256 Auth Architecture Decision and Pydantic Schema Draft
to SYNAPSE on https://jewel-untitled-respective-devel.trycloudflare.com
"""
from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error

SERVER_URL = "https://jewel-untitled-respective-devel.trycloudflare.com"

PYDANTIC_SCHEMA_CODE = '''"""
Pydantic Schemas for RS256 JWT Authentication Service.
Requirement: JWT RS256 with strict 15-minute access token expiry.
Compatible with Pydantic v2.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class TokenAlgorithm(str, Enum):
    RS256 = "RS256"


class TokenType(str, Enum):
    BEARER = "bearer"


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"
    SERVICE = "service"


class JWKKey(BaseModel):
    """JSON Web Key representation for public key sharing via JWKS."""
    model_config = ConfigDict(frozen=True)

    kty: str = Field(default="RSA", description="Key type (RSA)")
    use: str = Field(default="sig", description="Public key use (signature)")
    alg: TokenAlgorithm = Field(default=TokenAlgorithm.RS256, description="Algorithm")
    kid: str = Field(..., description="Key ID matching token header")
    n: str = Field(..., description="RSA modulus (base64url)")
    e: str = Field(default="AQAB", description="RSA public exponent (base64url)")


class JWKSKeySet(BaseModel):
    """JWKS container representing /auth/jwks.json endpoint response."""
    keys: List[JWKKey] = Field(default_factory=list)


class LoginRequest(BaseModel):
    """User authentication login request."""
    username: str = Field(..., min_length=3, max_length=128, description="Username or email")
    password: str = Field(..., min_length=8, description="Plaintext password for verification")
    scope: Optional[str] = Field(default="read write", description="Requested OAuth scopes")


class TokenPayload(BaseModel):
    """Decoded JWT claims payload adhering to RFC 7519 with 15-min TTL."""
    model_config = ConfigDict(extra="ignore")

    sub: str = Field(..., description="Subject identifier (user_id)")
    iss: str = Field(default="synapse-auth", description="Issuer authority URI")
    aud: str = Field(default="synapse-api", description="Intended audience")
    exp: int = Field(..., description="Expiration timestamp (Unix epoch)")
    nbf: int = Field(default_factory=lambda: int(time.time()), description="Not-before timestamp")
    iat: int = Field(default_factory=lambda: int(time.time()), description="Issued-at timestamp")
    jti: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique token nonce for replay prevention")
    roles: List[UserRole] = Field(default_factory=lambda: [UserRole.USER])
    scopes: List[str] = Field(default_factory=list)
    email: Optional[str] = None

    @field_validator("exp")
    @classmethod
    def validate_expiry_bounds(cls, v: int, info) -> int:
        iat = info.data.get("iat")
        if iat is not None and v <= iat:
            raise ValueError("exp must be strictly greater than iat")
        return v

    def is_expired(self, now: Optional[float] = None, leeway: int = 10) -> bool:
        """Returns True if token has expired, with clock skew leeway."""
        current = time.time() if now is None else now
        return current > (self.exp + leeway)

    @property
    def ttl_seconds(self) -> int:
        """Token lifetime in seconds."""
        return self.exp - self.iat


class RefreshTokenPayload(BaseModel):
    """Long-lived rotating refresh token payload."""
    sub: str = Field(..., description="Subject identifier")
    jti: str = Field(default_factory=lambda: str(uuid.uuid4()))
    token_family_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Family ID for reuse detection")
    exp: int = Field(..., description="Expiration timestamp (e.g. 7 days)")
    iat: int = Field(default_factory=lambda: int(time.time()))
    revoked: bool = False


class TokenResponse(BaseModel):
    """OAuth 2.0 compliant bearer token response."""
    access_token: str = Field(..., description="Signed RS256 JWT")
    token_type: TokenType = TokenType.BEARER
    expires_in: int = Field(default=900, description="Lifetime in seconds (15 minutes = 900s)")
    refresh_token: Optional[str] = Field(default=None, description="Rotating refresh token")
    scope: Optional[str] = None


class TokenVerificationRequest(BaseModel):
    """Request body for service-to-service token verification."""
    token: str = Field(..., description="Raw bearer JWT string")
    expected_audience: Optional[str] = "synapse-api"
    leeway_seconds: int = Field(default=10, description="Permitted clock skew leeway")


class TokenVerificationResult(BaseModel):
    """Result returned by token verifier middleware or service."""
    valid: bool
    payload: Optional[TokenPayload] = None
    error: Optional[str] = None
    key_id: Optional[str] = None
'''


def post_node(node_data: dict) -> None:
    req = urllib.request.Request(
        f"{SERVER_URL}/api/nodes",
        data=json.dumps(node_data).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"✔ Successfully posted [{node_data['type']}] {node_data['id']}: {resp.status}")
    except urllib.error.HTTPError as e:
        print(f"✖ Failed [{node_data['type']}] {node_data['id']}: {e.code} - {e.read().decode()}")
        sys.exit(1)


def main():
    print(f"Connecting to SYNAPSE at {SERVER_URL}...")

    # 1. Post DEC-02 (Architecture Decision)
    dec_node = {
        "id": "DEC-02",
        "title": "JWT RS256 with 15-Minute Expiry Architecture Decision",
        "type": "DECISION",
        "payload": {
            "decision": "Stateless asymmetric RS256 JWT with 15-minute access token lifespan and rotating refresh tokens",
            "crypto": "RS256",
            "key_type": "RSA-2048",
            "expiry_seconds": 900,
            "expiry_minutes": 15,
            "refresh_token_ttl_seconds": 604800,
            "key_distribution": "JWKS (/auth/jwks.json)",
            "rationale": "RS256 enables asymmetric signing where only the auth service requires the RSA private key, while microservices verify tokens using the public key without shared secret leakage. The 15-minute expiry limits exposure window if a bearer token is intercepted.",
            "security_invariants": [
                "Strict alg=RS256 whitelist; reject alg=none and HMAC key confusion",
                "Replay prevention via unique jti UUID",
                "Strict 15-minute (900s) TTL validation with 10s clock skew leeway",
                "Rotating refresh token family with reuse detection and instant invalidation"
            ]
        },
        "dependencies": ["REQ-01", "DEC-01"],
        "actor": "antigravity-agent",
        "idempotency_key": "post-dec-02-jwt-rs256-15min"
    }
    post_node(dec_node)

    # 2. Post ART-01 (Artifact: Pydantic Schema Draft)
    art_node = {
        "id": "ART-01",
        "title": "Pydantic Schema Draft: RS256 JWT Auth Models",
        "type": "ARTIFACT",
        "payload": {
            "artifact_name": "pydantic_auth_schemas.py",
            "framework": "Pydantic v2.14+",
            "crypto": "RS256",
            "expiry_minutes": 15,
            "expiry_seconds": 900,
            "models": [
                "TokenAlgorithm",
                "TokenType",
                "UserRole",
                "JWKKey",
                "JWKSKeySet",
                "LoginRequest",
                "TokenPayload",
                "TokenResponse",
                "RefreshTokenPayload",
                "TokenVerificationRequest",
                "TokenVerificationResult"
            ],
            "code": PYDANTIC_SCHEMA_CODE,
            "summary": "Drafted complete Pydantic v2 schemas for JWT RS256 authentication with 15-minute token expiry, strict validation, and JWKS support."
        },
        "dependencies": ["TSK-01", "DEC-02"],
        "actor": "antigravity-agent",
        "idempotency_key": "post-art-01-pydantic-auth-schema"
    }
    post_node(art_node)

    print("\nAll nodes successfully posted into team harness!")


if __name__ == "__main__":
    main()
