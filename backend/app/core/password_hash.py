"""
Owner password hashing (stdlib scrypt — no extra dependencies).

Stored format:  scrypt$<N>$<r>$<p>$<salt_b64url>$<hash_b64url>

Generate a hash for the OWNER_PASSWORD_HASH environment variable with:

    python -m app.core.password_hash          (run from the backend/ directory)

The plaintext password is never written to disk or logged.
"""
import base64
import hashlib
import hmac
import os
from typing import Optional

_N, _R, _P = 2 ** 14, 8, 1
_DKLEN = 32
_MAXMEM = 64 * 1024 * 1024
MIN_PASSWORD_LENGTH = 12


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def hash_password(password: str, *, salt: Optional[bytes] = None) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    salt = salt or os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def _parse(stored: str):
    parts = (stored or "").split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        raise ValueError("unsupported hash format")
    n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
    salt, expected = _unb64(parts[4]), _unb64(parts[5])
    if n < 2 ** 14 or n > 2 ** 20 or r < 1 or r > 32 or p < 1 or p > 4 or len(salt) < 8 or len(expected) < 16:
        raise ValueError("hash parameters out of accepted range")
    return n, r, p, salt, expected


def is_valid_hash_format(stored: str) -> bool:
    try:
        _parse(stored)
        return True
    except Exception:
        return False


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification. Returns False (never raises) on any malformed input."""
    try:
        n, r, p, salt, expected = _parse(stored)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected), maxmem=_MAXMEM)
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def _main() -> None:  # pragma: no cover - interactive helper
    import getpass
    pw = getpass.getpass("New owner password (min 12 chars): ")
    if pw != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    print("\nSet this as the OWNER_PASSWORD_HASH environment variable:\n")
    print(hash_password(pw))
    print("\nAlso set AUTH_JWT_SECRET to a random value, e.g.:  python -c \"import secrets; print(secrets.token_urlsafe(48))\"")


if __name__ == "__main__":  # pragma: no cover
    _main()
