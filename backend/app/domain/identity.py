from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

SigningKeyMode = Literal["preset", "custom"]


@dataclass
class Identity:
    """第一阶段单用户身份。RBAC 预留 roles；资源所有权预留 owner_id 比较点。"""

    id: str
    username: str
    token_version: int = 1
    roles: tuple[str, ...] = ("owner",)


@dataclass
class ResourceRef:
    kind: str
    id: str | None = None
    owner_id: str | None = None  # 预留，第一阶段不强制填充


@dataclass
class AuthPublicStatus:
    enabled: bool
    setup_required: bool
    user_configured: bool
    username: str | None
    secret_configured: bool
    preset_available: bool
    preset_pending_verification: bool
    signing_key_source: str
    token_version: int
    expire_seconds: int
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "setup_required": self.setup_required,
            "user_configured": self.user_configured,
            "username": self.username,
            "secret_configured": self.secret_configured,
            "preset_available": self.preset_available,
            "preset_pending_verification": self.preset_pending_verification,
            "signing_key_source": self.signing_key_source,
            "token_version": self.token_version,
            "expire_seconds": self.expire_seconds,
            "message": self.message,
        }
