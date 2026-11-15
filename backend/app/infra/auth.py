from __future__ import annotations

from app.domain.identity import Identity, ResourceRef


class DefaultAuthorizationPolicy:
    """第一阶段：认证关闭则放行；开启后须有身份。资源所有权与 RBAC 仅预留边界，可替换本适配。"""

    def __init__(self, enabled_getter) -> None:
        self._enabled_getter = enabled_getter

    def auth_enabled(self) -> bool:
        return bool(self._enabled_getter())

    def allow(self, identity: Identity | None, action: str, resource: ResourceRef | None = None) -> bool:
        if not self.auth_enabled():
            return True
        if identity is None:
            return False
        # 预留：未来 RBAC / 资源所有权在此替换。第一阶段单用户，有身份即放行。
        if resource is not None and resource.owner_id and resource.owner_id != identity.id:
            return False
        return True
