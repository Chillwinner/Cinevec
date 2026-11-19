from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.domain.errors import DependencyError, NotConfiguredError
from app.domain.lifecycle import VectorDeleteResult, as_vector_locators, serialize_vector_refs
from app.infra.milvus_rest import collection_name
from app.infra.redact import redact


class MilvusLiteIndex:
    """Embedded Milvus Lite vector index backed by a local file (zero external Milvus container needed)."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        profile: str = "default",
        model_id: str = "unset",
        model_version: str = "unset",
        dim: int | None = None,
        preprocess_version: str = "",
        auto_create: bool = True,
        client: Any = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._profile = profile
        self._model_id = model_id
        self._model_version = model_version
        self._dim = dim
        self._preprocess_version = preprocess_version or ""
        self._auto_create = auto_create
        self._client = client
        self._lock = threading.RLock()
        self._probed = False
        self._probe_error: str | None = None

    def available(self) -> bool:
        return bool(str(self._db_path).strip()) and self._dim is not None and int(self._dim) > 0

    def status_message(self) -> str:
        if self._dim is None:
            return "已启用 Milvus Lite，但未提供向量维度（PICA_MODEL_DIM），不会擅自假设维度或写入。"
        if self._probe_error:
            return f"Milvus Lite 初始化失败：{self._probe_error}"
        return f"已启用本地 Milvus Lite 嵌入式向量库（{self._db_path}），无需额外部署 Milvus 集群。"

    def public_status(self) -> dict[str, Any]:
        return {
            "status": "error" if self._probe_error else ("ok" if self.available() else "configured"),
            "backend": "milvus_lite",
            "message": self.status_message(),
            "uri_configured": True,
            "token_configured": False,
            "db": str(self._db_path),
            "profile": self._profile,
            "model_id": self._model_id,
            "model_version": self._model_version,
            "dim": self._dim,
            "preprocess_version": self._preprocess_version,
            "auto_create": self._auto_create,
            "metric": "COSINE",
        }

    def name_for(self, modality: str) -> str:
        if not self._dim:
            raise NotConfiguredError("未配置向量维度，无法确定集合名称。")
        return collection_name(
            self._profile,
            self._model_id,
            self._model_version,
            int(self._dim),
            modality,
            preprocess_version=self._preprocess_version,
        )

    def _ensure_client(self) -> Any:
        with self._lock:
            if self._client is not None:
                return self._client
            try:
                from pymilvus import MilvusClient
            except ImportError as exc:
                msg = "未安装 pymilvus / milvus-lite，请运行 pip install 'pymilvus[milvus_lite]' 或使用 Docker Compose 部署。"
                self._probe_error = msg
                raise DependencyError(msg) from exc
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._client = MilvusClient(str(self._db_path))
                self._probe_error = None
            except Exception as exc:
                self._probe_error = redact(str(exc))
                raise DependencyError(f"无法打开 Milvus Lite 文件 {self._db_path}：{self._probe_error}") from exc
            return self._client

    def probe(self) -> dict[str, Any]:
        with self._lock:
            try:
                client = self._ensure_client()
                names = client.list_collections() or []
                self._probed = True
                self._probe_error = None
                return {
                    "ok": True,
                    "message": f"Milvus Lite 本地向量库已就绪（{self._db_path}）。",
                    "collection_count": len(names) if isinstance(names, list) else None,
                }
            except Exception as exc:
                self._probed = True
                self._probe_error = redact(str(getattr(exc, "message", exc)))
                raise

    def ensure_collection(self, modality: str) -> str:
        if not self._dim:
            raise NotConfiguredError("未配置向量维度，拒绝创建集合。")
        name = self.name_for(modality)
        with self._lock:
            client = self._ensure_client()
            if client.has_collection(name):
                return name
            if not self._auto_create:
                raise NotConfiguredError(
                    f"集合 {name} 不存在，且当前模型配置禁止自动创建集合。",
                    code="milvus_auto_create_disabled",
                )
            dim = int(self._dim)
            try:
                from pymilvus import DataType

                schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
                schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=128)
                schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dim)
                schema.add_field(field_name="asset_id", datatype=DataType.VARCHAR, max_length=64)
                schema.add_field(field_name="version", datatype=DataType.INT64)
                schema.add_field(field_name="unit_index", datatype=DataType.INT64)
                schema.add_field(field_name="time_seconds", datatype=DataType.FLOAT)
                schema.add_field(field_name="preview_key", datatype=DataType.VARCHAR, max_length=512)
                index_params = client.prepare_index_params()
                index_params.add_index(
                    field_name="vector",
                    index_type="FLAT",
                    metric_type="COSINE",
                    index_name="vector_cosine",
                )
                client.create_collection(collection_name=name, schema=schema, index_params=index_params)
            except ImportError:
                # Fallback path for injected fake clients in unit tests without pymilvus installed
                client.create_collection(
                    collection_name=name,
                    dimension=dim,
                    primary_field_name="id",
                    id_type="string",
                    vector_field_name="vector",
                    metric_type="COSINE",
                    auto_id=False,
                )
            return name

    def upsert_records(self, modality: str, records: list[dict[str, Any]]) -> int:
        name = self.ensure_collection(modality)
        if not records:
            return 0
        data = []
        for item in records:
            vec = list(item["vector"])
            if self._dim and len(vec) != int(self._dim):
                raise DependencyError(f"向量维度 {len(vec)} 与配置 {self._dim} 不一致，拒绝写入。")
            data.append(
                {
                    "id": str(item["id"]),
                    "vector": vec,
                    "asset_id": str(item["asset_id"]),
                    "version": int(item["version"]),
                    "unit_index": int(item["unit_index"]),
                    "time_seconds": float(item.get("time_seconds") or 0),
                    "preview_key": str(item.get("preview_key") or ""),
                }
            )
        with self._lock:
            client = self._ensure_client()
            client.upsert(collection_name=name, data=data)
        return len(data)

    def search(
        self,
        modality: str,
        vector: list[float],
        *,
        limit: int,
        offset: int = 0,
        filter_expr: str | None = None,
    ) -> list[dict[str, Any]]:
        name = self.ensure_collection(modality)
        kwargs: dict[str, Any] = {
            "collection_name": name,
            "data": [list(vector)],
            "anns_field": "vector",
            "limit": int(limit),
            "output_fields": ["id", "asset_id", "version", "unit_index", "time_seconds", "preview_key"],
            "search_params": {"metric_type": "COSINE"},
        }
        if offset:
            kwargs["offset"] = int(offset)
        if filter_expr:
            kwargs["filter"] = filter_expr
        with self._lock:
            client = self._ensure_client()
            rows = client.search(**kwargs) or []
        if rows and isinstance(rows[0], list):
            rows = rows[0]
        hits = []
        for row in rows:
            entity = row.get("entity") or row
            score = row.get("score")
            if score is None:
                score = row.get("distance")
            hits.append(
                {
                    "id": entity.get("id") or row.get("id"),
                    "asset_id": entity.get("asset_id"),
                    "version": entity.get("version"),
                    "unit_index": entity.get("unit_index"),
                    "time_seconds": entity.get("time_seconds"),
                    "preview_key": entity.get("preview_key") or None,
                    "score": float(score) if score is not None else 0.0,
                }
            )
        return hits

    def delete_vectors(self, refs: list) -> VectorDeleteResult:
        serialized = serialize_vector_refs(refs)
        if not self.available():
            return VectorDeleteResult(
                attempted=False,
                succeeded=False,
                pending=True,
                message="Milvus Lite 未配置，未执行向量删除。",
                refs=serialized,
            )
        if not refs:
            return VectorDeleteResult(True, True, False, "没有需要删除的向量引用。", [])
        try:
            locators = as_vector_locators(refs)
        except ValueError as exc:
            return VectorDeleteResult(False, False, True, str(exc), serialized)
        try:
            by_collection: dict[str, list[str]] = {}
            assets_by_collection: dict[str, list[str]] = {}
            for loc in locators:
                by_collection.setdefault(loc.collection, []).append(loc.remote_id)
                if getattr(loc, "asset_id", ""):
                    lst = assets_by_collection.setdefault(loc.collection, [])
                    if loc.asset_id not in lst:
                        lst.append(loc.asset_id)
            touched: list[str] = []
            with self._lock:
                client = self._ensure_client()
                for name, ids in by_collection.items():
                    if not client.has_collection(name):
                        continue
                    quoted = ", ".join(json.dumps(item) for item in ids)
                    expr = f"id in [{quoted}]"
                    asset_ids = assets_by_collection.get(name) or []
                    if asset_ids:
                        quoted_assets = ", ".join(json.dumps(item) for item in asset_ids)
                        expr = f"{expr} or asset_id in [{quoted_assets}]"
                    client.delete(collection_name=name, filter=expr)
                    touched.append(name)
            return VectorDeleteResult(
                attempted=True,
                succeeded=True,
                pending=False,
                message=f"已在 Milvus Lite 集合 {', '.join(touched) or '（集合不存在）'} 删除向量。",
                refs=[loc.key() for loc in locators],
            )
        except Exception as exc:
            return VectorDeleteResult(
                attempted=True,
                succeeded=False,
                pending=True,
                message=redact(f"Milvus Lite 删除向量失败：{getattr(exc, 'message', exc)}"),
                refs=serialized,
            )

    def delete_by_asset(self, asset_id: str, collections: list[str] | None = None) -> VectorDeleteResult:
        clean_asset = (asset_id or "").strip()
        if not clean_asset:
            return VectorDeleteResult(True, True, False, "缺少资产 ID，无需删除向量。", [])
        if not self.available():
            return VectorDeleteResult(False, False, True, "Milvus Lite 未配置。", [clean_asset])
        targets = list(dict.fromkeys(c.strip() for c in (collections or []) if c and c.strip()))
        if not targets and self._dim:
            targets = [self.name_for("frame"), self.name_for("subtitle")]
        if not targets:
            return VectorDeleteResult(True, True, False, "没有可清理的向量集合。", [])
        try:
            touched: list[str] = []
            expr = f"asset_id == {json.dumps(clean_asset)}"
            with self._lock:
                client = self._ensure_client()
                for name in targets:
                    if not client.has_collection(name):
                        continue
                    client.delete(collection_name=name, filter=expr)
                    touched.append(name)
            return VectorDeleteResult(
                attempted=True,
                succeeded=True,
                pending=False,
                message=f"已在 Milvus Lite 集合 {', '.join(touched) or '（集合不存在）'} 按 asset_id={clean_asset} 删除向量。",
                refs=[f"{name}::asset:{clean_asset}" for name in targets],
            )
        except Exception as exc:
            return VectorDeleteResult(
                attempted=True,
                succeeded=False,
                pending=True,
                message=redact(f"Milvus Lite 按 asset_id 删除向量失败：{getattr(exc, 'message', exc)}"),
                refs=[f"{name}::asset:{clean_asset}" for name in targets],
            )
