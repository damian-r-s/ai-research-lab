"""Security primitives: roles, password hashing (argon2id) and session tokens."""

from __future__ import annotations

import hashlib
import secrets
from enum import StrEnum

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()
# Verified against when the user does not exist, so response time does not reveal usernames.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalisation")

MIN_PASSWORD_LENGTH = 12


class WeakPassword(ValueError):
    pass


def check_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"password must have at least {MIN_PASSWORD_LENGTH} characters")
    if password.strip() != password:
        raise WeakPassword("password must not start or end with whitespace")


def hash_password(password: str) -> str:
    check_strength(password)
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


class Role(StrEnum):
    """Dashboard roles, ordered from least to most privileged."""

    viewer = "viewer"  # read-only
    researcher = "researcher"  # can start and steer research (Phase 2+)
    admin = "admin"  # user management, budgets, kill switch

    @property
    def rank(self) -> int:
        return _RANK[self]

    def satisfies(self, required: Role) -> bool:
        return self.rank >= required.rank


_RANK = {Role.viewer: 0, Role.researcher: 1, Role.admin: 2}

SESSION_TOKEN_BYTES = 32  # 256 bits of entropy


def new_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Only this digest is stored; a database leak does not expose usable session tokens."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
