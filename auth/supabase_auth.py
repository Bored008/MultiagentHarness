"""
Supabase Auth verification, login/signup proxy helpers, and revocable Board Agent Key utilities.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

import httpx
import jwt
from jwt import PyJWKClient

DEFAULT_SUPABASE_URL = "https://zxycpgztcttumhhazadf.supabase.co"
DEFAULT_SUPABASE_PUBLISHABLE_KEY = "sb_publishable_Ayj649eKgOO3-sLj94OuPg_e2Yyd5v4"
AGENT_KEY_PREFIX = "omni_"


@dataclass(frozen=True)
class AuthPrincipal:
    """Verified caller identity extracted from a Supabase JWT or Board Agent Key."""

    kind: Literal["human", "agent", "anonymous"]
    user_id: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    board_id: Optional[str] = None
    agent_key_id: Optional[str] = None
    agent_key_name: Optional[str] = None
    scopes: Tuple[str, ...] = field(default_factory=tuple)


def generate_agent_key() -> Tuple[str, str, str]:
    """
    Generate a revocable persistent Board Agent Key.
    Returns (raw_api_key, display_prefix, sha256_hex_hash).
    """
    secret_part = secrets.token_urlsafe(24)
    raw_key = f"{AGENT_KEY_PREFIX}{secret_part}"
    key_prefix = raw_key[:13]
    key_hash = hash_agent_key(raw_key)
    return raw_key, key_prefix, key_hash


def hash_agent_key(raw_key: str) -> str:
    """Return SHA-256 hex digest of a raw Board Agent Key."""
    return hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()


def extract_bearer_token(header_val: Optional[str], query_token: Optional[str] = None) -> Optional[str]:
    """Extract raw token from Authorization Bearer header, X-Api-Key, or query param."""
    if header_val and header_val.strip():
        clean = header_val.strip()
        if clean.lower().startswith("bearer "):
            clean = clean[7:].strip()
        if clean:
            return clean
    if query_token and query_token.strip():
        return query_token.strip()
    return None


class SupabaseAuthClient:
    """
    Verifies Supabase Auth JWTs (via JWKS or /auth/v1/user) and handles email/password + OTP auth.
    Also accepts an optional local JWT verifier/secret for deterministic offline tests.
    """

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        publishable_key: Optional[str] = None,
        local_jwt_secret: Optional[str] = None,
        local_public_key: Optional[str] = None,
        local_algorithm: str = "HS256",
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.supabase_url = (
            supabase_url
            or os.environ.get("SUPABASE_URL")
            or DEFAULT_SUPABASE_URL
        ).rstrip("/")
        self.publishable_key = (
            publishable_key
            or os.environ.get("SUPABASE_PUBLISHABLE_KEY")
            or os.environ.get("SUPABASE_ANON_KEY")
            or DEFAULT_SUPABASE_PUBLISHABLE_KEY
        )
        self.local_jwt_secret = local_jwt_secret or os.environ.get("OMNIHARNESS_JWT_SECRET")
        self.local_public_key = local_public_key
        self.local_algorithm = local_algorithm
        self._http_client = http_client
        self._jwk_client: Optional[PyJWKClient] = None
        self._user_cache: Dict[str, Tuple[AuthPrincipal, float]] = {}

    def _get_jwk_client(self) -> PyJWKClient:
        if self._jwk_client is None:
            jwks_url = f"{self.supabase_url}/auth/v1/.well-known/jwks.json"
            self._jwk_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=300)
        return self._jwk_client

    def _auth_headers(self, access_token: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "apikey": self.publishable_key,
            "Content-Type": "application/json",
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        return headers

    @staticmethod
    def _principal_from_claims(decoded: Dict[str, Any]) -> AuthPrincipal:
        sub = str(decoded.get("sub") or "")
        email = decoded.get("email")
        user_meta = decoded.get("user_metadata") if isinstance(decoded.get("user_metadata"), dict) else {}
        display_name = user_meta.get("display_name") or user_meta.get("full_name") or (
            email.split("@")[0] if isinstance(email, str) and "@" in email else sub
        )
        return AuthPrincipal(
            kind="human",
            user_id=sub,
            email=email,
            display_name=display_name,
            scopes=("board:read", "board:write", "board:admin"),
        )

    async def verify_human_jwt(self, token: str) -> Optional[AuthPrincipal]:
        """
        Verify a Supabase Auth (or local test) JWT and return an AuthPrincipal.
        Never uses unverified claims or user_metadata for authorization roles.
        """
        if not token or token.startswith(AGENT_KEY_PREFIX):
            return None

        # 1. Optional local secret / test key path (used in deterministic tests)
        if self.local_public_key or self.local_jwt_secret:
            key = self.local_public_key or self.local_jwt_secret
            algs = ["RS256", "ES256"] if self.local_public_key else [self.local_algorithm]
            try:
                decoded = jwt.decode(
                    token,
                    key=key,
                    algorithms=algs,
                    options={"verify_aud": False, "require": ["sub", "exp"]},
                )
                return self._principal_from_claims(decoded)
            except Exception:
                return None

        # 2. Check short-lived verified cache (max 45s) keyed by SHA-256 of token
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        cached = self._user_cache.get(token_digest)
        now = time.time()
        if cached and cached[1] > now:
            return cached[0]

        # 3. Try asymmetric JWKS verification (RS256 / ES256)
        try:
            header = jwt.get_unverified_header(token)
            alg = str(header.get("alg", ""))
            if alg in ("RS256", "ES256") and header.get("kid"):
                signing_key = self._get_jwk_client().get_signing_key_from_jwt(token)
                decoded = jwt.decode(
                    token,
                    key=signing_key.key,
                    algorithms=[alg],
                    audience="authenticated",
                    issuer=f"{self.supabase_url}/auth/v1",
                    options={"require": ["sub", "exp"]},
                )
                principal = self._principal_from_claims(decoded)
                exp = float(decoded.get("exp", now + 45))
                self._user_cache[token_digest] = (principal, min(exp, now + 45))
                return principal
        except Exception:
            # Fall through to Supabase /auth/v1/user (handles HS256 or rotated keys)
            pass

        # 4. Authoritative verification against Supabase Auth /auth/v1/user
        try:
            if self._http_client is not None:
                res = await self._http_client.get(
                    f"{self.supabase_url}/auth/v1/user",
                    headers=self._auth_headers(token),
                    timeout=10.0,
                )
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    res = await client.get(
                        f"{self.supabase_url}/auth/v1/user",
                        headers=self._auth_headers(token),
                    )
            if res.status_code != 200:
                return None
            data = res.json()
            uid = str(data.get("id") or "")
            if not uid:
                return None
            email = data.get("email")
            user_meta = data.get("user_metadata") if isinstance(data.get("user_metadata"), dict) else {}
            display_name = user_meta.get("display_name") or user_meta.get("full_name") or (
                email.split("@")[0] if isinstance(email, str) and "@" in email else uid
            )
            principal = AuthPrincipal(
                kind="human",
                user_id=uid,
                email=email,
                display_name=display_name,
                scopes=("board:read", "board:write", "board:admin"),
            )
            self._user_cache[token_digest] = (principal, now + 45)
            return principal
        except Exception:
            return None

    async def _post_auth(self, path: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        url = f"{self.supabase_url}/auth/v1{path}"
        if self._http_client is not None:
            res = await self._http_client.post(url, json=payload, headers=self._auth_headers(), timeout=15.0)
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, json=payload, headers=self._auth_headers())
        try:
            body = res.json()
        except Exception:
            body = {"error_description": res.text}
        return res.status_code, body if isinstance(body, dict) else {"data": body}

    async def sign_up(self, email: str, password: str, display_name: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        payload: Dict[str, Any] = {"email": email.strip(), "password": password}
        if display_name and display_name.strip():
            payload["data"] = {"display_name": display_name.strip()}
        return await self._post_auth("/signup", payload)

    async def sign_in_password(self, email: str, password: str) -> Tuple[int, Dict[str, Any]]:
        return await self._post_auth(
            "/token?grant_type=password",
            {"email": email.strip(), "password": password},
        )

    async def send_email_otp(self, email: str) -> Tuple[int, Dict[str, Any]]:
        return await self._post_auth("/otp", {"email": email.strip(), "create_user": True})

    async def verify_email_otp(self, email: str, token: str) -> Tuple[int, Dict[str, Any]]:
        return await self._post_auth(
            "/verify",
            {"email": email.strip(), "token": token.strip(), "type": "email"},
        )

    async def refresh_session(self, refresh_token: str) -> Tuple[int, Dict[str, Any]]:
        return await self._post_auth(
            "/token?grant_type=refresh_token",
            {"refresh_token": refresh_token.strip()},
        )
