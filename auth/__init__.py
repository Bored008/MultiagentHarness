"""
Auth Module: Token verification and FastAPI middleware.
Adheres to SYNAPSE requirements (RS256 asymmetric stateless JWT validation).
"""
from auth.middleware import BearerAuthDependency, BearerTokenMiddleware
from auth.schemas import TokenPayload, TokenVerificationResult, UserClaims
from auth.verifier import TokenVerifier

__all__ = [
    "UserClaims",
    "TokenPayload",
    "TokenVerificationResult",
    "TokenVerifier",
    "BearerTokenMiddleware",
    "BearerAuthDependency",
]
