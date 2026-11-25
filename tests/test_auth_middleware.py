"""
Unit & Integration Tests for Auth Token Verifier & FastAPI Middleware (TSK-02).
Verifies RS256 asymmetric token verification, expiration handling, and route protection.
"""
from __future__ import annotations

import time
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auth.middleware import BearerAuthDependency, BearerTokenMiddleware
from auth.schemas import UserClaims
from auth.verifier import TokenVerifier


@pytest.fixture(scope="module")
def rsa_keys():
    """Generate ephemeral RSA private and public key pair in PEM format."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return {"private": private_pem, "public": public_pem}


def make_token(
    private_pem: bytes,
    sub: str = "user-123",
    email: str = "alex@example.com",
    roles: list[str] = None,
    exp_offset_seconds: int = 900,  # 15 minutes (REQ-01)
    aud: str = "synapse-api",
    iss: str = "synapse-auth",
) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "exp": now + exp_offset_seconds,
        "iat": now,
        "iss": iss,
        "aud": aud,
        "email": email,
        "roles": roles or ["developer", "agent"],
    }
    return jwt.encode(payload, private_pem, algorithm="RS256")


def test_token_verifier_valid_token(rsa_keys):
    verifier = TokenVerifier(public_key=rsa_keys["public"])
    token = make_token(rsa_keys["private"], sub="agent-007")

    result = verifier.verify_token(token)
    assert result.valid is True
    assert result.error is None
    assert result.user is not None
    assert result.user.sub == "agent-007"
    assert "developer" in result.user.roles


def test_token_verifier_expired_token(rsa_keys):
    verifier = TokenVerifier(public_key=rsa_keys["public"])
    expired_token = make_token(rsa_keys["private"], exp_offset_seconds=-60)

    result = verifier.verify_token(expired_token)
    assert result.valid is False
    assert "expired" in (result.error or "").lower()


def test_token_verifier_invalid_signature(rsa_keys):
    # Generate a different foreign key
    foreign_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    foreign_pem = foreign_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    token = make_token(foreign_pem)
    verifier = TokenVerifier(public_key=rsa_keys["public"])
    result = verifier.verify_token(token)

    assert result.valid is False
    assert "invalid token" in (result.error or "").lower()


def test_fastapi_bearer_dependency(rsa_keys):
    verifier = TokenVerifier(public_key=rsa_keys["public"])
    auth_dep = BearerAuthDependency(verifier)

    app = FastAPI()

    @app.get("/api/secure")
    def secure_route(user: UserClaims = Depends(auth_dep)):
        return {"user": user.sub, "roles": user.roles}

    client = TestClient(app)

    # 1. Missing Authorization header -> 401
    res1 = client.get("/api/secure")
    assert res1.status_code == 401
    assert "missing" in res1.json()["detail"].lower()

    # 2. Valid token -> 200 OK
    token = make_token(rsa_keys["private"], sub="himanshu")
    res2 = client.get("/api/secure", headers={"Authorization": f"Bearer {token}"})
    assert res2.status_code == 200
    assert res2.json()["user"] == "himanshu"

    # 3. Expired token -> 401
    exp_token = make_token(rsa_keys["private"], exp_offset_seconds=-30)
    res3 = client.get("/api/secure", headers={"Authorization": f"Bearer {exp_token}"})
    assert res3.status_code == 401
    assert "expired" in res3.json()["detail"].lower()


def test_fastapi_bearer_middleware(rsa_keys):
    verifier = TokenVerifier(public_key=rsa_keys["public"])

    app = FastAPI()
    app.add_middleware(BearerTokenMiddleware, verifier=verifier, exclude_paths=["/health"])

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/dashboard/metrics")
    def metrics():
        return {"active": 42}

    client = TestClient(app)

    # Exempt route works without auth
    res_health = client.get("/health")
    assert res_health.status_code == 200

    # Protected route without auth -> 401
    res_no_auth = client.get("/dashboard/metrics")
    assert res_no_auth.status_code == 401

    # Protected route with valid token -> 200
    token = make_token(rsa_keys["private"], sub="agent-dashboard")
    res_auth = client.get("/dashboard/metrics", headers={"Authorization": f"Bearer {token}"})
    assert res_auth.status_code == 200
    assert res_auth.json()["active"] == 42
