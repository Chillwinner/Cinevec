from __future__ import annotations

import uuid

from app.domain.errors import AppError, NotFoundError
from app.domain.models import ObjectEntry, Source
from app.domain.ports import MetadataRepository, SourceReader
from app.infra.clock import now


class SourceService:
    def __init__(self, metadata: MetadataRepository, reader: SourceReader) -> None:
        self._metadata = metadata
        self._reader = reader

    def list_sources(self) -> list[Source]:
        return self._metadata.list_sources()

    def get(self, source_id: str) -> Source:
        source = self._metadata.get_source(source_id)
        if source is None or source.deleted:
            raise NotFoundError("存储源不存在")
        return source

    def create(
        self,
        name: str,
        endpoint: str,
        bucket: str,
        region: str,
        role: str,
        is_platform_storage: bool,
        access_key: str | None,
        secret_key: str | None,
    ) -> Source:
        if not name.strip():
            raise AppError("invalid", "请填写存储源名称")
        if role not in {"input", "storage", "both"}:
            raise AppError("invalid", "角色只能是 input、storage 或 both")
        configured = bool(endpoint.strip() and bucket.strip() and access_key and secret_key)
        source = Source(
            id=str(uuid.uuid4()),
            name=name.strip(),
            endpoint=endpoint.strip(),
            bucket=bucket.strip(),
            region=region.strip(),
            role=role,  # type: ignore[arg-type]
            is_platform_storage=is_platform_storage,
            access_key_configured=bool(access_key),
            secret_configured=bool(secret_key),
            last_probe_status="unconfigured" if not configured else "unknown",
            last_probe_message="尚未探测连接" if configured else "连接信息不完整，尚未探测",
            created_at=now(),
            updated_at=now(),
        )
        return self._metadata.add_source(source, access_key, secret_key)

    def update(self, source_id: str, **fields) -> Source:
        if self._metadata.get_source(source_id) is None:
            raise NotFoundError("存储源不存在")
        return self._metadata.update_source(source_id, **fields)

    def delete(self, source_id: str) -> None:
        self._metadata.delete_source(source_id)

    def probe(self, source_id: str) -> Source:
        source = self.get(source_id)
        access_key, secret_key = self._metadata.get_source_credentials(source_id)
        try:
            self._reader.probe(source, access_key, secret_key)
        except AppError as exc:
            return self._metadata.set_source_probe(source_id, "failed", exc.message)
        return self._metadata.set_source_probe(source_id, "ok", "连接成功")

    def browse(self, source_id: str, prefix: str = "") -> list[ObjectEntry]:
        source = self.get(source_id)
        access_key, secret_key = self._metadata.get_source_credentials(source_id)
        return self._reader.list_prefix(source, access_key, secret_key, prefix)
