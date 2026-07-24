"""Authentication for the app.

Two modes, selected by ``AUTH_MODE``:

* ``mock``  — requires an ``X-Mock-User-ID`` header; resolves a fixed user table
  (user-alice/bob/charlie). Unknown or missing → 401. Local development only.
* ``entra`` — validates an RS256 Microsoft Entra ID JWT. The signing key is fetched
  from the tenant JWKS endpoint (cached). ``aud``/``iss``/``exp``/``iat`` are
  validated; configured scopes/roles are enforced (403 on mismatch). The
  :class:`User` key is tenant-scoped as ``tid:oid`` (with ``sub`` as fallback).

Any unauthenticated request → 401.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from .config import get_settings

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


@dataclass(frozen=True)
class AgentCaller:
    principal_id: str
    tenant_id: str


# Mock user table (see PRD §18).
MOCK_USERS: dict[str, User] = {
    "user-alice": User("user-alice", "Alice Johnson", "alice@example.com", "AJ"),
    "user-bob": User("user-bob", "Bob Smith", "bob@example.com", "BS"),
    "user-charlie": User("user-charlie", "Charlie Lee", "charlie@example.com", "CL"),
}

def _validate_auth_configuration(auth_mode: str, app_environment: str) -> None:
    if auth_mode == "mock" and app_environment == "production":
        raise RuntimeError(
            "AUTH_MODE=mock is forbidden when APP_ENV=production; configure Entra auth"
        )


_startup_settings = get_settings()
_validate_auth_configuration(
    _startup_settings.auth_mode.lower(),
    _startup_settings.app_environment,
)
del _startup_settings


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


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return authorization.split(" ", 1)[1].strip()


def _decode_rs256_token(
    token: str,
    jwk_client: PyJWKClient,
    *,
    audience: str,
    issuer: str | None = None,
    required_claims: tuple[str, ...],
    verify_issuer: bool = True,
    invalid_log_message: str,
    jwks_log_message: str,
) -> dict[str, Any]:
    try:
        signing_key = jwk_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={
                "require": list(required_claims),
                "verify_iss": verify_issuer,
            },
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        logger.warning(invalid_log_message, exc)
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    except Exception as exc:
        logger.exception(jwks_log_message)
        raise HTTPException(
            status_code=401,
            detail="Token validation error",
        ) from exc


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
        token = _extract_bearer_token(authorization)
        claims = _decode_rs256_token(
            token,
            self._jwk_client,
            audience=self.audience,
            issuer=self.issuer,
            required_claims=("exp", "iat", "aud", "iss"),
            invalid_log_message="[auth] token validation failed: %s",
            jwks_log_message="[auth] JWKS validation error",
        )

        user = self._build_user(claims)
        self._enforce_scopes_and_roles(claims)
        return user

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
        subject = claims.get("oid") or claims.get("sub")
        tenant_id = claims.get("tid")
        if not subject:
            raise HTTPException(status_code=401, detail="Token missing subject claim")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Token missing tenant claim")
        if str(tenant_id).casefold() != self.tenant_id.casefold():
            raise HTTPException(status_code=401, detail="Token tenant mismatch")
        email = (
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("upn")
            or ""
        )
        name = claims.get("name") or email or subject
        return User(
            user_id=f"{tenant_id}:{subject}",
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


class AgentTokenValidator:
    """Validate application-only tokens presented by the Hosted Agent identity."""

    def __init__(self) -> None:
        settings = get_settings()
        self.tenant_id = settings.entra_tenant_id
        self.audience = settings.agent_gateway_audience
        self.required_role = settings.agent_gateway_required_role
        self.allowed_principals = set(settings.hosted_agent_principal_ids)
        self.allowed_issuers = {
            f"https://login.microsoftonline.com/{self.tenant_id}/v2.0",
            f"https://sts.windows.net/{self.tenant_id}/",
        }
        jwks_uri = (
            settings.entra_jwks_uri
            or f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"
        )
        self._jwk_client = PyJWKClient(jwks_uri, cache_keys=True)

    def validate(self, authorization: str | None) -> AgentCaller:
        token = _extract_bearer_token(authorization)
        claims = _decode_rs256_token(
            token,
            self._jwk_client,
            audience=self.audience,
            required_claims=("exp", "iat", "aud", "iss", "tid"),
            verify_issuer=False,
            invalid_log_message=(
                "[auth] agent token validation failed: %s"
            ),
            jwks_log_message="[auth] agent token JWKS validation error",
        )

        if claims.get("iss") not in self.allowed_issuers:
            raise HTTPException(status_code=401, detail="Token issuer mismatch")
        if str(claims.get("tid", "")).casefold() != self.tenant_id.casefold():
            raise HTTPException(status_code=401, detail="Token tenant mismatch")
        if claims.get("scp"):
            raise HTTPException(status_code=403, detail="Delegated tokens are not accepted")
        roles = set(claims.get("roles") or [])
        if self.required_role not in roles:
            raise HTTPException(status_code=403, detail="Missing required application role")
        principal_id = str(claims.get("oid") or claims.get("sub") or "")
        if not principal_id:
            raise HTTPException(status_code=401, detail="Token missing principal")
        if principal_id not in self.allowed_principals:
            raise HTTPException(status_code=403, detail="Agent principal is not allowed")
        return AgentCaller(
            principal_id=principal_id, tenant_id=str(claims["tid"])
        )


_agent_token_validator: AgentTokenValidator | None = None


def _get_agent_token_validator() -> AgentTokenValidator:
    global _agent_token_validator
    if _agent_token_validator is None:
        settings = get_settings()
        if not (
            settings.entra_tenant_id
            and settings.agent_gateway_audience
            and settings.hosted_agent_principal_ids
        ):
            raise HTTPException(
                status_code=503, detail="Hosted Agent gateway is not configured"
            )
        _agent_token_validator = AgentTokenValidator()
    return _agent_token_validator


def validate_agent_token(authorization: str | None) -> AgentCaller:
    """Validate a Hosted Agent application token for non-FastAPI callers."""
    return _get_agent_token_validator().validate(authorization)


async def get_current_user(
    x_mock_user_id: str | None = Header(default=None, alias="X-Mock-User-ID"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> User:
    """FastAPI dependency resolving the authenticated user."""
    auth_mode = get_settings().auth_mode.lower()
    if auth_mode == "mock":
        return _resolve_mock_user(x_mock_user_id)
    if auth_mode == "entra":
        return await asyncio.to_thread(_resolve_entra_user, authorization)
    raise HTTPException(status_code=500, detail=f"Invalid AUTH_MODE: {auth_mode}")


async def get_agent_caller(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> AgentCaller:
    return await asyncio.to_thread(validate_agent_token, authorization)
