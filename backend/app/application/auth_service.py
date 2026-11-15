from __future__ import annotations

import re
import uuid
from typing import Any

from app.domain.errors import AppError, ConflictError, NotConfiguredError
from app.domain.identity import AuthPublicStatus, Identity
from app.infra.config import Settings
from app.infra.jwt_tokens import issue_token, verify_token
from app.infra.passwords import hash_password, verify_password

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-\.]{1,64}$")
MIN_PASSWORD = 8
MIN_CUSTOM_KEY = 16
PRESET_NOTE = (
    "签名密钥预设通过 gitignored 本地开发配置（PICA_JWT_SECRET）注入，"
    "不写入源代码、仓库或接口响应。"
)


class AuthService:
    def __init__(self, metadata, settings: Settings) -> None:
        self._metadata = metadata
        self._settings = settings

    def public_status(self) -> AuthPublicStatus:
        runtime = self._metadata.get_auth_runtime()
        user = self._metadata.get_user()
        preset = self._preset_secret()
        stored = (runtime.get("signing_key") or "").strip()
        source = runtime.get("signing_key_source") or "unset"
        secret_configured = bool(stored) or (source == "preset" and bool(preset))
        enabled = bool(runtime.get("enabled"))
        if enabled:
            message = "登录验证已开启。业务接口与预览需要有效令牌。"
        elif user:
            message = "已配置账号，但登录验证仍关闭。访问暂按未认证放行。"
        else:
            message = "登录验证默认关闭。可在设置中手动开启（需完整账号并确认密钥选择）。"
        message = message + " " + PRESET_NOTE
        return AuthPublicStatus(
            enabled=enabled,
            setup_required=user is None,
            user_configured=user is not None,
            username=user["username"] if user else None,
            secret_configured=secret_configured,
            preset_available=bool(preset),
            preset_pending_verification=False,
            signing_key_source=source,
            token_version=int(runtime.get("token_version") or 1),
            expire_seconds=int(runtime.get("expire_seconds") or 86400),
            message=message,
        )

    def is_enabled(self) -> bool:
        return bool(self._metadata.get_auth_runtime().get("enabled"))

    def _preset_secret(self) -> str:
        return (self._settings.jwt_secret or "").strip()

    def resolve_signing_key(self) -> str | None:
        runtime = self._metadata.get_auth_runtime()
        source = runtime.get("signing_key_source") or "unset"
        stored = (runtime.get("signing_key") or "").strip()
        if source == "custom" and stored:
            return stored
        if source == "preset":
            return self._preset_secret() or stored or None
        return stored or self._preset_secret() or None

    def enable(self, payload: dict[str, Any]) -> dict[str, Any]:
        confirm = payload.get("confirm_customization")
        if confirm is not True:
            raise AppError(
                "confirm_required",
                "开启登录必须明确确认密钥选择（沿用预设或自定义）。未确认不能启用。",
                400,
            )
        mode = payload.get("signing_key_mode")
        if mode not in {"preset", "custom"}:
            raise AppError("invalid", "请选择沿用预设或自定义签名密钥。")
        username = (payload.get("username") or "").strip()
        password = payload.get("password") or ""
        confirm_pw = payload.get("password_confirm")
        if confirm_pw is not None and confirm_pw != password:
            raise AppError("invalid", "两次输入的密码不一致。")
        if not username or not password:
            raise AppError("invalid", "开启登录需要完整的账号与密码。")
        if not USERNAME_RE.match(username):
            raise AppError("invalid", "用户名仅支持字母、数字、点、下划线和短横线，最长 64 位。")
        if len(password) < MIN_PASSWORD:
            raise AppError("invalid", f"密码至少 {MIN_PASSWORD} 位。")

        runtime = self._metadata.get_auth_runtime()
        if runtime.get("enabled"):
            raise ConflictError("登录验证已经开启。")

        user = self._metadata.get_user()
        if user is None:
            user = self._metadata.create_user(str(uuid.uuid4()), username, hash_password(password), token_version=1)
        else:
            if user["username"] != username or not verify_password(password, user["password_hash"]):
                raise AppError("invalid", "账号或密码不正确，无法开启登录。", 401)
            # 不在成功路径回显或记录密码

        secret, source = self._select_secret(mode, payload.get("custom_signing_key"))
        expire = int(payload.get("expire_seconds") or runtime.get("expire_seconds") or 86400)
        if expire < 60 or expire > 60 * 60 * 24 * 30:
            raise AppError("invalid", "令牌有效期须在 60 秒到 30 天之间。")
        token_version = int(user.get("token_version") or 1)
        # 自定义密钥不经过环境预设；预设密钥不写入自定义列，避免把预设值扩散到可导出备份以外的新位置……但仍需能签发。
        # 沿用预设时只记录 source=preset，密钥继续从配置接口读取。
        self._metadata.update_auth_runtime(
            enabled=True,
            signing_key_source=source,
            signing_key=secret if source == "custom" else None,
            token_version=token_version,
            expire_seconds=expire,
        )
        token = issue_token(
            secret=secret,
            subject=user["id"],
            username=user["username"],
            token_version=token_version,
            expire_seconds=expire,
        )
        status = self.public_status()
        return {
            "ok": True,
            "auth": status.as_dict(),
            "token": token,
            "token_type": "bearer",
            "expires_in": expire,
            "username": user["username"],
        }

    def disable(self, identity: Identity | None) -> dict[str, Any]:
        if not self.is_enabled():
            raise AppError("invalid", "登录验证已关闭。")
        # 关闭时必须已通过鉴权依赖（identity 非空）
        if identity is None:
            raise AppError("unauthorized", "关闭登录需要有效令牌。", 401)
        self._metadata.update_auth_runtime(enabled=False)
        return {"ok": True, "auth": self.public_status().as_dict()}

    def setup_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._metadata.get_user() is not None:
            raise ConflictError("账号已配置，不能再走首次设置入口。")
        if self.is_enabled():
            raise ConflictError("登录已开启，不能再走首次设置入口。")
        username = (payload.get("username") or "").strip()
        password = payload.get("password") or ""
        if payload.get("password_confirm") not in (None, password):
            raise AppError("invalid", "两次输入的密码不一致。")
        if not username or not password:
            raise AppError("invalid", "请提供完整的账号与密码。")
        if not USERNAME_RE.match(username):
            raise AppError("invalid", "用户名仅支持字母、数字、点、下划线和短横线，最长 64 位。")
        if len(password) < MIN_PASSWORD:
            raise AppError("invalid", f"密码至少 {MIN_PASSWORD} 位。")
        self._metadata.create_user(str(uuid.uuid4()), username, hash_password(password), token_version=1)
        return {"ok": True, "auth": self.public_status().as_dict()}

    def login(self, username: str, password: str) -> dict[str, Any]:
        user = self._metadata.get_user_by_username((username or "").strip())
        if user is None or not verify_password(password or "", user["password_hash"]):
            raise AppError("invalid_credentials", "账号或密码不正确。", 401)
        secret = self.resolve_signing_key()
        if not secret:
            raise NotConfiguredError(
                "尚未配置可用的签名密钥，无法签发令牌。请先在开启登录时选择沿用已配置的预设或提供自定义密钥。",
                code="signing_key_missing",
            )
        if not self.is_enabled():
            raise AppError("auth_disabled", "登录验证仍关闭，无需签发令牌。可在设置中开启。", 400)
        runtime = self._metadata.get_auth_runtime()
        expire = int(runtime.get("expire_seconds") or 86400)
        token = issue_token(
            secret=secret,
            subject=user["id"],
            username=user["username"],
            token_version=int(user.get("token_version") or runtime.get("token_version") or 1),
            expire_seconds=expire,
        )
        # 令牌只在此返回给客户端，不写入日志或状态接口。
        return {
            "token": token,
            "token_type": "bearer",
            "expires_in": expire,
            "username": user["username"],
        }

    def update_signing_key(self, payload: dict[str, Any], identity: Identity | None) -> dict[str, Any]:
        if self.is_enabled() and identity is None:
            raise AppError("unauthorized", "更新签名密钥需要有效令牌。", 401)
        if payload.get("confirm_customization") is not True:
            raise AppError("confirm_required", "更新签名密钥必须明确确认选择（沿用预设或自定义）。", 400)
        mode = payload.get("signing_key_mode")
        if mode not in {"preset", "custom"}:
            raise AppError("invalid", "请选择沿用预设或自定义签名密钥。")
        secret, source = self._select_secret(mode, payload.get("custom_signing_key"))
        runtime = self._metadata.get_auth_runtime()
        new_version = int(runtime.get("token_version") or 1) + 1
        user = self._metadata.get_user()
        self._metadata.update_auth_runtime(
            signing_key_source=source,
            signing_key=secret if source == "custom" else None,
            token_version=new_version,
        )
        if user:
            self._metadata.update_user(user["id"], token_version=new_version)
        return {"ok": True, "auth": self.public_status().as_dict()}

    def authenticate_bearer(self, authorization: str | None) -> Identity:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise AppError("unauthorized", "请先登录。", 401)
        token = authorization.split(" ", 1)[1].strip()
        if not token:
            raise AppError("unauthorized", "请先登录。", 401)
        secret = self.resolve_signing_key()
        if not secret:
            raise AppError("unauthorized", "签名密钥不可用，令牌无法校验。", 401)
        claims = verify_token(token, secret)
        user = self._metadata.get_user()
        if user is None or user["id"] != str(claims.get("sub")):
            raise AppError("unauthorized", "登录令牌无效。", 401)
        tv = int(claims.get("tv") or 0)
        current = int(user.get("token_version") or 1)
        runtime_tv = int(self._metadata.get_auth_runtime().get("token_version") or 1)
        if tv != current or tv != runtime_tv:
            raise AppError("token_revoked", "登录令牌已失效，请重新登录。", 401)
        return Identity(id=user["id"], username=user["username"], token_version=current)

    def _select_secret(self, mode: str, custom: str | None) -> tuple[str, str]:
        if mode == "custom":
            value = (custom or "").strip()
            if len(value) < MIN_CUSTOM_KEY:
                raise AppError("invalid", f"自定义签名密钥至少 {MIN_CUSTOM_KEY} 位。")
            return value, "custom"
        preset = self._preset_secret()
        if not preset:
            raise AppError(
                "preset_unavailable",
                "沿用预设需要本地开发配置中已有可用的 PICA_JWT_SECRET。接口不会回显该值。请选择自定义密钥，或先写入 gitignored 本地配置后再试。",
            )
        return preset, "preset"
