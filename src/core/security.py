"""Password hashing (argon2id, library defaults follow RFC 9106 recommendations)."""

from __future__ import annotations

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
