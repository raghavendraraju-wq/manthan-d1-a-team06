"""Tokens and passwords.

Staff sign in with email + password; a customer proves order ownership with the
one-time code. Both get a short-lived JWT that the SPA keeps in memory only.
Every API route checks the role against returnright.access.can_access, so the
screen rules hold on the server, not just in the browser.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request

from returnright.access import can_access
from returnright.contracts import Role, Screen

_hasher = PasswordHasher()
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


@dataclass(frozen=True)
class Principal:
    role: Role
    subject: str          # staff email, or order number for a customer
    name: str = ""
    email: str = ""


def issue_token(secret: str, principal: Principal, minutes: int) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": principal.subject, "role": principal.role.value, "name": principal.name,
              "email": principal.email, "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def current_principal(request: Request) -> Principal:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    try:
        claims = jwt.decode(header[7:], request.app.state.settings.jwt_secret, algorithms=[ALGORITHM])
        role = Role(claims["role"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Your session has ended. Sign in again.") from None
    return Principal(role, claims["sub"], claims.get("name", ""), claims.get("email", ""))


def require_screen(screen: Screen):
    def guard(principal: Principal = Depends(current_principal)) -> Principal:
        if not can_access(principal.role, screen):
            raise HTTPException(status_code=403, detail="Your role can't use this screen.")
        return principal
    return guard


def staff_or_customer(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.role is Role.SYSTEM:
        raise HTTPException(status_code=403, detail="Not available to this role.")
    return principal
