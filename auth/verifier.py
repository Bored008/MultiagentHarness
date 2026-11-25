"""
Token Verifier implementing stateless asymmetric RS256 JWT validation.
Adheres to REQ-01 (RS256, 15min expiry) and DEC-01 (Stateless asymmetric architecture).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Union
import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError, PyJWTError

from auth.schemas import TokenPayload, TokenVerificationResult, UserClaims


class TokenVerifier:
    """
    Validates asymmetric RS256 JWT Bearer tokens using an RSA Public Key.
    Ensures expiry checks, algorithm enforcement, and Pydantic schema parsing.
    """

    def __init__(
        self,
        public_key: Union[str, bytes],
        algorithm: str = "RS256",
        audience: Optional[str] = "synapse-api",
        issuer: Optional[str] = "synapse-auth",
        leeway_seconds: int = 0,
    ):
        self.public_key = public_key
        self.algorithm = algorithm
        self.audience = audience
        self.issuer = issuer
        self.leeway = leeway_seconds

    def verify_token(self, token: str) -> TokenVerificationResult:
        """
        Verify raw JWT token string against public key and algorithm.
        Returns TokenVerificationResult containing parsed UserClaims or error reason.
        """
        if not token or not token.strip():
            return TokenVerificationResult(valid=False, error="Token is empty or missing")

        clean_token = token.strip()
        if clean_token.lower().startswith("bearer "):
            clean_token = clean_token[7:].strip()

        try:
            decode_options: Dict[str, Any] = {
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "require": ["exp", "sub"],
            }
            if self.audience:
                decode_options["verify_aud"] = True
            else:
                decode_options["verify_aud"] = False

            if self.issuer:
                decode_options["verify_iss"] = True
            else:
                decode_options["verify_iss"] = False

            decoded = jwt.decode(
                clean_token,
                key=self.public_key,
                algorithms=[self.algorithm],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options=decode_options,
            )

            # Map into Pydantic schema
            sub = decoded.get("sub")
            exp = decoded.get("exp")
            iat = decoded.get("iat", int(time.time()))
            iss = decoded.get("iss")
            aud = decoded.get("aud")

            # Extract user claims
            claims_data = decoded.get("claims") or {}
            user_claims = UserClaims(
                sub=sub,
                email=claims_data.get("email") or decoded.get("email"),
                roles=claims_data.get("roles") or decoded.get("roles", []),
                metadata=claims_data.get("metadata") or decoded.get("metadata", {}),
            )

            payload = TokenPayload(
                sub=sub,
                exp=exp,
                iat=iat,
                iss=iss,
                aud=aud if isinstance(aud, str) else (aud[0] if aud else None),
                claims=user_claims,
            )

            return TokenVerificationResult(
                valid=True,
                user=user_claims,
                payload=payload,
                error=None,
            )

        except ExpiredSignatureError:
            return TokenVerificationResult(valid=False, error="Token has expired")
        except InvalidTokenError as ite:
            return TokenVerificationResult(valid=False, error=f"Invalid token: {str(ite)}")
        except Exception as e:
            return TokenVerificationResult(valid=False, error=f"Verification failed: {str(e)}")
