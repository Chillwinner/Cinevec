from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain.errors import AppError
from app.infra.clock import now


def _jwt():
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover
        raise AppError("dependency_error", "未安装 PyJWT，无法签发登录令牌。", 503) from exc
    return jwt


def issue_token(
    *,
    secret: str,
    subject: str,
    username: str,
    token_version: int,
    expire_seconds: int,
    issuer: str = "pica",
) -> str:
    jwt = _jwt()
    issued = now()
    if issued.tzinfo is None:
        issued = issued.replace(tzinfo=timezone.utc)
    payload = {
        "sub": subject,
        "username": username,
        "tv": int(token_version),
        "iat": issued,
        "exp": issued + timedelta(seconds=int(expire_seconds)),
        "iss": issuer,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_token(token: str, secret: str, *, issuer: str = "pica") -> dict[str, Any]:
    jwt = _jwt()
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=issuer,
            options={"require": ["sub", "exp", "iat", "tv"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AppError("token_expired", "登录已过期，请重新登录。", 401) from exc
    except jwt.InvalidTokenError as exc:
        raise AppError("token_invalid", "登录令牌无效。", 401) from exc
