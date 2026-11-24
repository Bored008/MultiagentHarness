"""
FastAPI Bearer Token Validator Middleware & Security Dependency (TSK-02).
Implements Bearer token extraction, RS256 JWT validation, and user claim injection.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from auth.schemas import UserClaims
from auth.verifier import TokenVerifier

logger = logging.getLogger("omniharness.auth")

# Standard HTTPBearer scheme for OpenAPI documentation
bearer_scheme = HTTPBearer(auto_error=False)


class BearerAuthDependency:
    """
    FastAPI route dependency that extracts Bearer token, validates via TokenVerifier,
    and returns validated UserClaims or raises HTTP 401 Unauthorized.
    """

    def __init__(self, verifier: TokenVerifier):
        self.verifier = verifier

    async def __call__(
        self,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    ) -> UserClaims:
        if not credentials or not credentials.credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Authorization Bearer token header",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = credentials.credentials
        result = self.verifier.verify_token(token)

        if not result.valid or not result.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=result.error or "Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            )

        return result.user


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """
    ASGI Middleware that validates Authorization header on incoming requests
    and injects verified `request.state.user` claims for downstream route handlers.
    Optionally allows unauthenticated access for excluded public paths (e.g. /health, /docs).
    """

    def __init__(
        self,
        app,
        verifier: TokenVerifier,
        exclude_paths: Optional[list[str]] = None,
    ):
        super().__init__(app)
        self.verifier = verifier
        self.exclude_paths = exclude_paths or ["/health", "/docs", "/openapi.json", "/dashboard", "/ws"]

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Check if current path is exempt from mandatory authentication
        path = request.url.path
        if any(path.startswith(exempt) for exempt in self.exclude_paths):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return Response(
                content='{"detail":"Missing Authorization Bearer token"}',
                status_code=status.HTTP_401_UNAUTHORIZED,
                media_type="application/json",
                headers={"WWW-Authenticate": "Bearer"},
            )

        result = self.verifier.verify_token(auth_header)
        if not result.valid or not result.user:
            return Response(
                content=f'{{"detail":"{result.error or "Invalid token"}"}}',
                status_code=status.HTTP_401_UNAUTHORIZED,
                media_type="application/json",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )

        # Inject validated user into request state
        request.state.user = result.user
        request.state.token_payload = result.payload

        return await call_next(request)
