from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from app.domain.errors import DependencyError, NotConfiguredError
from app.domain.lifecycle import VectorDeleteResult, as_vector_locators, serialize_vector_refs
from app.infra.redact import redact

# 官方 RESTful API v2（https://milvus.io/api-reference/restful/v2.6.x/About.md）
LIST_PATH = "/v2/vectordb/collections/list"
CREATE_PATH = "/v2/vectordb/collections/create"
DESCRIBE_PATH = "/v2/vectordb/collections/describe"
LOAD_PATH = "/v2/vectordb/collections/load"
UPSERT_PATH = "/v2/vectordb/entities/upsert"
SEARCH_PATH = "/v2/vectordb/entities/search"
DELETE_PATH = "/v2/vectordb/entities/delete"

SAFE_NAME = re.compile(r"[^a-zA-Z0-9_]+")


def collection_name(
    profile: str,
    model_id: str,
    model_version: str,
    dim: int,
    modality: str,
    preprocess_version: str = "",
) -> str:
    parts = ["pica", profile, model_id, model_version, str(dim)]
    if (preprocess_version or "").strip():
        parts.append(preprocess_version)
    parts.append(modality)
    cleaned = [SAFE_NAME.sub("_", (p or "unset").strip())[:40] or "unset" for p in parts]
    name = "_".join(cleaned)
    if not name[0].isalpha():
        name = "p_" + name
    return name[:255]


def _params_map(params: Any) -> dict[str, Any]:
    """Normalize field params from REST describe (object or [{key,value}] lists)."""
    if not params:
        return {}
    if isinstance(params, dict):
        return params
    if isinstance(params, list):
        out: dict[str, Any] = {}
        for item in params:
            if isinstance(item, dict) and item.get("key") is not None:
                out[str(item["key"])] = item.get("value")
        return out
    return {}


class MilvusRestIndex:
    """按需连接的官方 REST 适配器。不删除他人集合，不覆盖字段不兼容的同名集合。"""

    def __init__(
        self,
        uri: str | None,
        token: str | None = None,
        db_name: str = "_default",
        profile: str = "default",
        model_id: str = "unset",
        model_version: str = "unset",
        dim: int | None = None,
        timeout: float = 15.0,
        transport=None,
        preprocess_version: str = "",
        auto_create: bool = True,
    ) -> None:
        self._uri = (uri or "").rstrip("/")
        self._token = token or ""
        self._db = db_name or "_default"
        self._profile = profile
        self._model_id = model_id
        self._model_version = model_version
        self._dim = dim
        self._timeout = timeout
        self._transport = transport
        self._preprocess_version = preprocess_version or ""
        self._auto_create = auto_create
        self._probed = False
        self._probe_error: str | None = None
        self._verified_collections: set[str] = set()

    def available(self) -> bool:
        return bool(self._uri) and self._dim is not None and int(self._dim) > 0

    def status_message(self) -> str:
        if not self._uri:
            return "Milvus 尚未配置连接 URI，向量检索不可用。不会返回假结果。"
        if self._dim is None:
            return "已配置 Milvus URI，但未提供向量维度（PICA_MODEL_DIM），不会擅自假设维度或写入。"
        if self._probe_error:
            return f"Milvus 已配置，但最近一次按需探测失败：{self._probe_error}"
        return "已配置 Milvus URI，将在写入或查询时按需连接并隔离集合。凭证不回显。"

    def public_status(self) -> dict:
        return {
            "status": "ok" if self._probed and not self._probe_error else ("configured" if self._uri else "unconfigured"),
            "message": self.status_message(),
            "uri_configured": bool(self._uri),
            "token_configured": bool(self._token),
            "db": self._db,
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
            self._profile, self._model_id, self._model_version, int(self._dim), modality,
            preprocess_version=self._preprocess_version,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        if self._db and "dbName" not in body:
            body["dbName"] = self._db
        if self._transport is not None:
            data = self._transport(path, body, dict(self._headers()))
        else:
            if not self._uri:
                raise NotConfiguredError("Milvus 尚未配置连接 URI。", code="milvus_unconfigured")
            req = urllib.request.Request(
                self._uri + path,
                data=json.dumps(body).encode("utf-8"),
                headers=self._headers(),
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    raw = resp.read().decode("utf-8") or "{}"
                    data = json.loads(raw)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "ignore") if exc.fp else str(exc)
                raise DependencyError(redact(f"Milvus HTTP {exc.code}：{detail[:300]}")) from exc
            except urllib.error.URLError as exc:
                raise DependencyError(redact(f"无法连接 Milvus：{exc.reason}")) from exc
            except json.JSONDecodeError as exc:
                raise DependencyError("Milvus 返回了无法解析的响应。") from exc
        code = data.get("code")
        if code not in (0, None):
            raise DependencyError(redact(f"Milvus 返回错误：{data.get('message') or data}"))
        return data

    def probe(self) -> dict[str, Any]:
        try:
            data = self._post(LIST_PATH, {})
            self._probed = True
            self._probe_error = None
            names = data.get("data") or []
            if isinstance(names, dict):
                names = names.get("collection_names") or names.get("collections") or []
            return {
                "ok": True,
                "message": "已按需连通 Milvus（list collections）。未修改任何集合。",
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
        if name in self._verified_collections:
            return name
        existing = self._describe(name)
        if existing is not None:
            self._assert_compatible(existing, name)
            self._verified_collections.add(name)
            return name
        if not self._auto_create:
            raise NotConfiguredError(
                f"集合 {name} 不存在，且当前模型配置禁止自动创建远端集合。等待联调后再创建。",
                code="milvus_auto_create_disabled",
            )
        dim = int(self._dim)
        payload = {
            "collectionName": name,
            "schema": {
                "autoID": False,
                "enableDynamicField": False,
                "fields": [
                    {"fieldName": "id", "dataType": "VarChar", "isPrimary": True, "elementTypeParams": {"max_length": "128"}},
                    {"fieldName": "vector", "dataType": "FloatVector", "elementTypeParams": {"dim": str(dim)}},
                    {"fieldName": "asset_id", "dataType": "VarChar", "elementTypeParams": {"max_length": "64"}},
                    {"fieldName": "version", "dataType": "Int64"},
                    {"fieldName": "unit_index", "dataType": "Int64"},
                    {"fieldName": "time_seconds", "dataType": "Float"},
                    {"fieldName": "preview_key", "dataType": "VarChar", "elementTypeParams": {"max_length": "512"}},
                ],
            },
            "indexParams": [
                {"fieldName": "vector", "indexName": "vector_cosine", "metricType": "COSINE"},
            ],
        }
        self._post(CREATE_PATH, payload)
        try:
            self._post(LOAD_PATH, {"collectionName": name})
        except DependencyError:
            # load 失败不删除刚创建的集合，交由下次按需重试
            raise
        self._verified_collections.add(name)
        return name

    def _describe(self, name: str) -> dict[str, Any] | None:
        try:
            data = self._post(DESCRIBE_PATH, {"collectionName": name})
            return data.get("data") or data
        except DependencyError as exc:
            msg = (exc.message or "").lower()
            if any(s in msg for s in ("not found", "doesn't exist", "does not exist", "can't find", "cannot find", "doesn't have")):
                return None
            # 不确定时不要当成可覆盖
            raise

    def _assert_compatible(self, info: dict[str, Any], name: str) -> None:
        dim = int(self._dim or 0)
        fields = info.get("fields") or info.get("schema", {}).get("fields") or []
        vector_dim = None
        names = set()
        for field in fields:
            fname = field.get("fieldName") or field.get("name") or ""
            names.add(fname)
            ftype = str(field.get("type") or field.get("dataType") or "")
            params = _params_map(field.get("params") or field.get("elementTypeParams") or {})
            if "vector" in ftype.lower() or fname == "vector":
                raw = params.get("dim") or params.get("dimension")
                if raw is not None:
                    vector_dim = int(raw)
        if vector_dim is not None and vector_dim != dim:
            raise DependencyError(
                f"集合 {name} 已存在但向量维度为 {vector_dim}，与配置维度 {dim} 不兼容。不会覆盖或删除该集合。"
            )
        indexes = info.get("indexes") or []
        for index in indexes:
            metric = (index.get("metricType") or index.get("metric_type") or "").upper()
            if metric and metric != "COSINE":
                raise DependencyError(
                    f"集合 {name} 已存在但度量是 {metric}，与本项目默认 COSINE 不兼容。不会覆盖或删除该集合。"
                )
        required = {"id", "vector"}
        if names and not required.issubset(names):
            raise DependencyError(
                f"集合 {name} 已存在但缺少必要字段 {sorted(required - names)}。不会覆盖或删除该集合。"
            )

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
                    "id": item["id"],
                    "vector": vec,
                    "asset_id": item["asset_id"],
                    "version": int(item["version"]),
                    "unit_index": int(item["unit_index"]),
                    "time_seconds": float(item.get("time_seconds") or 0),
                    "preview_key": item.get("preview_key") or "",
                }
            )
        self._post(UPSERT_PATH, {"collectionName": name, "data": data})
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
        payload: dict[str, Any] = {
            "collectionName": name,
            "data": [list(vector)],
            "annsField": "vector",
            "limit": int(limit),
            "offset": int(offset),
            "outputFields": ["id", "asset_id", "version", "unit_index", "time_seconds", "preview_key"],
            "searchParams": {"metricType": "COSINE"},
        }
        if filter_expr:
            payload["filter"] = filter_expr
        try:
            data = self._post(SEARCH_PATH, payload)
        except DependencyError as exc:
            msg = str(exc).lower()
            if "not found" in msg or "not exist" in msg:
                self._verified_collections.discard(name)
                name = self.ensure_collection(modality)
                payload["collectionName"] = name
                data = self._post(SEARCH_PATH, payload)
            else:
                raise
        rows = data.get("data") or []
        # 官方响应可能是 [{id, distance/score, entity: {...}}] 或嵌套 list
        if rows and isinstance(rows[0], list):
            rows = rows[0]
        hits = []
        for row in rows:
            entity = row.get("entity") or row
            score = row.get("score")
            if score is None:
                # COSINE 相似度；若只给 distance，按文档不把它改成概率
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
                message="Milvus 未配置，未执行向量删除，不会假装删除成功。",
                refs=serialized,
            )
        if not refs:
            return VectorDeleteResult(True, True, False, "没有需要删除的向量引用。", [])
        try:
            locators = as_vector_locators(refs)
        except ValueError as exc:
            return VectorDeleteResult(
                attempted=False,
                succeeded=False,
                pending=True,
                message=str(exc),
                refs=serialized,
            )
        try:
            # 只在调用方给出的 collection 中按实际远端 id（及可选 asset_id）删除，不扩散到当前空间其它集合。
            by_collection: dict[str, list[str]] = {}
            assets_by_collection: dict[str, list[str]] = {}
            for loc in locators:
                by_collection.setdefault(loc.collection, []).append(loc.remote_id)
                if getattr(loc, "asset_id", ""):
                    lst = assets_by_collection.setdefault(loc.collection, [])
                    if loc.asset_id not in lst:
                        lst.append(loc.asset_id)
            touched: list[str] = []
            for name, ids in by_collection.items():
                quoted = ", ".join(json.dumps(item) for item in ids)
                expr = f"id in [{quoted}]"
                asset_ids = assets_by_collection.get(name) or []
                if asset_ids:
                    quoted_assets = ", ".join(json.dumps(item) for item in asset_ids)
                    expr = f"{expr} or asset_id in [{quoted_assets}]"
                try:
                    self._post(DELETE_PATH, {"collectionName": name, "filter": expr})
                except DependencyError as exc:
                    msg = str(exc).lower()
                    if any(tok in msg for tok in ("not found", "not exist", "doesn't exist", "can't find collection")):
                        if self._describe(name) is None:
                            continue
                    raise
                touched.append(name)
            return VectorDeleteResult(
                attempted=True,
                succeeded=True,
                pending=False,
                message=f"已在集合 {', '.join(touched) or '（指定集合不存在）'} 按 collection 与远端 id 删除向量。",
                refs=[loc.key() for loc in locators],
            )
        except Exception as exc:
            return VectorDeleteResult(
                attempted=True,
                succeeded=False,
                pending=True,
                message=redact(f"向量删除未成功：{getattr(exc, 'message', exc)}。元数据排除可以保留，不会假装已删除向量。"),
                refs=serialized,
            )

    def delete_by_asset(self, asset_id: str, collections: list[str] | None = None) -> VectorDeleteResult:
        clean_asset = (asset_id or "").strip()
        if not clean_asset:
            return VectorDeleteResult(True, True, False, "缺少资产 ID，无需删除向量。", [])
        if not self.available():
            return VectorDeleteResult(
                attempted=False,
                succeeded=False,
                pending=True,
                message="Milvus 未配置，未执行向量删除，不会假装删除成功。",
                refs=[clean_asset],
            )
        targets = list(dict.fromkeys(c.strip() for c in (collections or []) if c and c.strip()))
        if not targets and self._dim:
            targets = [self.name_for("frame"), self.name_for("subtitle")]
        if not targets:
            return VectorDeleteResult(True, True, False, "没有可清理的向量集合。", [])
        try:
            touched: list[str] = []
            expr = f"asset_id == {json.dumps(clean_asset)}"
            for name in targets:
                try:
                    self._post(DELETE_PATH, {"collectionName": name, "filter": expr})
                except DependencyError as exc:
                    msg = str(exc).lower()
                    if any(tok in msg for tok in ("not found", "not exist", "doesn't exist", "can't find collection")):
                        if self._describe(name) is None:
                            continue
                    raise
                touched.append(name)
            return VectorDeleteResult(
                attempted=True,
                succeeded=True,
                pending=False,
                message=f"已在集合 {', '.join(touched) or '（指定集合不存在）'} 按 asset_id={clean_asset} 删除向量。",
                refs=[f"{name}::asset:{clean_asset}" for name in targets],
            )
        except Exception as exc:
            return VectorDeleteResult(
                attempted=True,
                succeeded=False,
                pending=True,
                message=redact(f"按 asset_id 删除向量失败：{getattr(exc, 'message', exc)}"),
                refs=[f"{name}::asset:{clean_asset}" for name in targets],
            )
