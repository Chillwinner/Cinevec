from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

SCHEME = "pbkdf2_sha256"
ITERATIONS = 600_000


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("password required")
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{SCHEME}${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iter_s, salt_b64, hash_b64 = stored.split("$", 3)
        if scheme != SCHEME:
            return False
        iterations = int(iter_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False
