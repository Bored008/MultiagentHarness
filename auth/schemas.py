"""
Pydantic Schemas for Auth Token Validation (TSK-01 / REQ-01 / DEC-01).
Implements stateless asymmetric JWT token payloads and user claims.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class UserClaims(BaseModel):
    """Authenticated user claims payload extracted from a verified JWT."""
    sub: str = Field(..., description="Subject identifier (e.g. user ID)")
    email: Optional[str] = Field(None, description="User email address")
    roles: List[str] = Field(default_factory=list, description="Assigned authorization roles")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary user metadata")


class TokenPayload(BaseModel):
    """
    Standard JWT Claims payload adhering to REQ-01 (RS256 with 15min expiry).
    """
    sub: str = Field(..., description="Subject / User ID")
    exp: int = Field(..., description="Expiration timestamp (Unix epoch)")
    iat: int = Field(default_factory=lambda: int(time.time()), description="Issued-at timestamp")
    nbf: Optional[int] = Field(None, description="Not-before timestamp")
    iss: Optional[str] = Field("synapse-auth", description="Token issuer")
    aud: Optional[str] = Field("synapse-api", description="Token audience")
    claims: Optional[UserClaims] = Field(None, description="Embedded user claims")


class TokenVerificationResult(BaseModel):
    """Result of token validation by the TokenVerifier."""
    valid: bool
    user: Optional[UserClaims] = None
    payload: Optional[TokenPayload] = None
    error: Optional[str] = None
