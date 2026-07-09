"""Authentication for the vertical slice.

Mock header auth is fully implemented (local dev). The Entra JWT path is stubbed
so the contract is present; it will be filled in during the hardening phase (P9).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Header, HTTPException


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


def _resolve_entra_user(authorization: str | None) -> User:
    # Stub — full JWKS/RS256 validation lands in P9.
    raise HTTPException(status_code=501, detail="Entra auth not implemented in this build")


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
