"""Authentication for the app.

Two modes, selected by ``AUTH_MODE``:

* ``mock``  — requires an ``X-Mock-User-ID`` header; resolves a fixed user table
  (user-alice/bob/charlie). Unknown or missing → 401. Used for local dev/demo.
* ``entra`` — validates an RS256 Microsoft Entra ID JWT presented as
  ``Authorization: Bearer <token>``. The signing key is fetched from the tenant
  JWKS endpoint (cached). ``aud``/``iss``/``exp``/``iat`` are validated; optional
  required scopes/roles are enforced (403 on mismatch). The :class:`User` is built
  from ``oid``/``sub`` (id), ``preferred_username``/``email``/``upn`` and ``name``.

Any unauthenticated request → 401.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from config import get_settings

logger = logging.getLogger("auth")


@dataclass
class User:
    user_id: str
    display_name: str
    email: str
    initials: str
    avatar_url: str | None = None

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "email": self.email,
            "initials": self.initials,
            "avatar_url": self.avatar_url,
        }


# Mock user table (see PRD §18).
MOCK_USERS: dict[str, User] = {
    "user-alice": User("user-alice", "Alice Johnson", "alice@example.com", "AJ"),
    "user-bob": User("user-bob", "Bob Smith", "bob@example.com", "BS"),
    "user-charlie": User("user-charlie", "Charlie Lee", "charlie@example.com", "CL"),
}

AUTH_MODE = os.getenv("AUTH_MODE", "mock").lower()


def _resolve_mock_user(x_mock_user_id: str | None) -> User:
    if not x_mock_user_id:
        raise HTTPException(status_code=401, detail="Missing X-Mock-User-ID header")
    user = MOCK_USERS.get(x_mock_user_id)
    if user is None:
        raise HTTPException(status_code=401, detail=f"Unknown mock user: {x_mock_user_id}")
    return user


# ---------------------------------------------------------------- Entra ID
def _split_list(value: str) -> set[str]:
    """Parse a space/comma-delimited env value into a set of tokens."""
    return {t for t in value.replace(",", " ").split() if t}


class EntraValidator:
    """Validates Entra ID access tokens against the tenant JWKS (v2.0 endpoint)."""

    def __init__(self) -> None:
        s = get_settings()
        self.tenant_id = s.entra_tenant_id
        self.audience = s.entra_audience
        self.issuer = (
            s.entra_issuer
            or f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"
        )
        self.jwks_uri = (
            s.entra_jwks_uri
            or f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"
        )
        self.required_scopes = _split_list(s.entra_required_scopes)
        self.required_roles = _split_list(s.entra_required_roles)
        # PyJWKClient caches signing keys and refreshes on rotation.
        self._jwk_client = PyJWKClient(self.jwks_uri, cache_keys=True)

    def validate(self, authorization: str | None) -> User:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token = authorization.split(" ", 1)[1].strip()
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError as exc:
            logger.warning("[auth] token validation failed: %s", exc)
            raise HTTPException(status_code=401, detail="Invalid token")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — JWKS fetch / network
            logger.exception("[auth] JWKS validation error")
            raise HTTPException(status_code=401, detail="Token validation error") from exc

        self._enforce_scopes_and_roles(claims)
        return self._build_user(claims)

    def _enforce_scopes_and_roles(self, claims: dict) -> None:
        if self.required_scopes:
            granted = set(str(claims.get("scp", "")).split())
            if not self.required_scopes.issubset(granted):
                raise HTTPException(status_code=403, detail="Missing required scope")
        if self.required_roles:
            roles = claims.get("roles") or []
            if not self.required_roles.issubset(set(roles)):
                raise HTTPException(status_code=403, detail="Missing required role")

    @staticmethod
    def _initials(name: str, email: str) -> str:
        parts = [p for p in name.split() if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        if parts:
            return parts[0][:2].upper()
        return (email[:2] or "?").upper()

    def _build_user(self, claims: dict) -> User:
        user_id = claims.get("oid") or claims.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token missing subject claim")
        email = (
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("upn")
            or ""
        )
        name = claims.get("name") or email or user_id
        return User(
            user_id=str(user_id),
            display_name=str(name),
            email=str(email),
            initials=self._initials(str(name), str(email)),
        )


_entra_validator: EntraValidator | None = None


def _get_entra_validator() -> EntraValidator:
    global _entra_validator
    if _entra_validator is None:
        s = get_settings()
        if not s.entra_configured:
            raise HTTPException(
                status_code=500,
                detail="Entra auth selected but ENTRA_TENANT_ID/ENTRA_AUDIENCE unset",
            )
        _entra_validator = EntraValidator()
    return _entra_validator


def _resolve_entra_user(authorization: str | None) -> User:
    return _get_entra_validator().validate(authorization)


async def get_current_user(
    x_mock_user_id: str | None = Header(default=None, alias="X-Mock-User-ID"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> User:
    """FastAPI dependency resolving the authenticated user."""
    if AUTH_MODE == "mock":
        return _resolve_mock_user(x_mock_user_id)
    if AUTH_MODE == "entra":
        return _resolve_entra_user(authorization)
    raise HTTPException(status_code=500, detail=f"Invalid AUTH_MODE: {AUTH_MODE}")
