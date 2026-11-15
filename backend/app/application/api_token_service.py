from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from app.domain.errors import AppError
from app.domain.identity import Identity

TOKEN_PREFIX = "pica_"
LOOKUP_LEN = 12


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_api_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "token_prefix": row["token_prefix"],
        "created_at": row["created_at"],
        "revoked_at": row.get("revoked_at"),
    }


class ApiTokenService:
    def __init__(self, metadata) -> None:
        self._metadata = metadata

    def list_tokens(self) -> list[dict[str, Any]]:
        return [token_public(row) for row in self._metadata.list_api_tokens()]

    def create(self, name: str | None = None) -> dict[str, Any]:
        label = (name or "").strip() or "plugin"
        if len(label) > 80:
            raise AppError("invalid", "Token 名称过长。")
        raw = generate_api_token()
        token_id = secrets.token_hex(16)
        row = self._metadata.add_api_token(token_id, label, hash_api_token(raw), raw[:LOOKUP_LEN])
        public = token_public(row)
        public["token"] = raw
        public["message"] = "请立即保存，完整 Token 只显示这一次。"
        return public

    def revoke(self, token_id: str) -> dict[str, Any]:
        return token_public(self._metadata.revoke_api_token(token_id))

    def authenticate(self, bearer: str | None) -> Identity:
        if not bearer or not bearer.lower().startswith("bearer "):
            raise AppError("unauthorized", "插件接口需要 Bearer API Token。", 401)
        token = bearer.split(" ", 1)[1].strip()
        if not token.startswith(TOKEN_PREFIX) or len(token) < LOOKUP_LEN:
            raise AppError("unauthorized", "API Token 无效。", 401)
        digest = hash_api_token(token)
        candidates = self._metadata.find_api_token_by_prefix(token[:LOOKUP_LEN])
        for row in candidates:
            stored = row.get("token_hash") or ""
            if hmac.compare_digest(stored, digest):
                return Identity(
                    id=row["id"],
                    username=f"plugin:{row.get('name') or 'token'}",
                    token_version=1,
                    roles=("plugin",),
                )
        raise AppError("unauthorized", "API Token 无效。", 401)
