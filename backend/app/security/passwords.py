"""Password hashing and strength policy (PRD 12.1).

Argon2id — the current OWASP recommendation — via ``argon2-cffi``.  Parameters
are memory-hard defaults; on an on-premises box with spare RAM, raise
``memory_cost`` in deployment configuration rather than in code.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import get_settings

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MiB
    parallelism=2,
    hash_len=32,
    salt_len=16,
)


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except Exception:  # pragma: no cover
        return False


def validate_password_strength(plain: str) -> list[str]:
    """Return a list of policy violations (empty means acceptable)."""
    settings = get_settings()
    problems: list[str] = []
    if len(plain) < settings.password_min_length:
        problems.append(
            f"Password must be at least {settings.password_min_length} characters."
        )
    if plain.isalpha() or plain.isdigit():
        problems.append("Password must mix letters and numbers or symbols.")
    if plain.lower() in {"password", "police123", "crimelink", "india123", "admin123"}:
        problems.append("Password is too common.")
    return problems
