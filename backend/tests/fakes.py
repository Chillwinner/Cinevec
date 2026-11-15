from __future__ import annotations

import math
from collections import defaultdict

from app.domain.errors import DependencyError, NotConfiguredError
from app.domain.lifecycle import VectorDeleteResult, as_vector_locators, serialize_vector_refs
from app.infra.milvus_rest import collection_name


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class MemoryEncoder:
    """仅测试用。运行时不得作为未配置时的回退。"""

    def __init__(self, dim: int = 8, text_map: dict[str, list[float]] | None = None) -> None:
        self.dim = dim
        self.text_map = text_map or {}
        self.calls: list[str] = []

    def available(self) -> bool:
        return True

    def status_message(self) -> str:
        return "测试用内存编码器，不是已加载的生产模型。"

    def public_status(self) -> dict:
        return {"status": "test", "message": self.status_message(), "inference_ready": True, "dim": self.dim}

    def encode_text(self, text: str) -> list[float]:
        self.calls.append(f"text:{text}")
        if text in self.text_map:
            return list(self.text_map[text])
        vec = [0.0] * self.dim
        if not text:
            return vec
        vec[0] = 1.0
        vec[1] = (sum(ord(c) for c in text) % 100) / 100.0
        return vec

    def encode_image(self, image_bytes: bytes) -> list[float]:
        self.calls.append(f"image:{len(image_bytes)}")
        vec = [0.0] * self.dim
        vec[0] = 1.0
        if image_bytes:
            vec[2] = (sum(image_bytes[:32]) % 100) / 100.0
        return vec


class MemoryVectorIndex:
    """仅测试用的隔离集合内存索引，不是运行时回退。"""

    def __init__(self, profile="default", model_id="test", model_version="v1", dim=8) -> None:
        self.profile = profile
        self.model_id = model_id
        self.model_version = model_version
        self.dim = dim
        self.collections: dict[str, dict] = {}
        self.dropped: list[str] = []

    def available(self) -> bool:
        return True

    def status_message(self) -> str:
        return "测试用内存向量索引，不是已连接的 Milvus。"

    def public_status(self) -> dict:
        return {
            "status": "test",
            "message": self.status_message(),
            "uri_configured": False,
            "token_configured": False,
            "profile": self.profile,
            "dim": self.dim,
            "metric": "COSINE",
        }

    def name_for(self, modality: str) -> str:
        return collection_name(self.profile, self.model_id, self.model_version, self.dim, modality)

    def ensure_collection(self, modality: str, dim: int | None = None, metric: str = "COSINE") -> str:
        name = self.name_for(modality)
        dim = int(dim or self.dim)
        existing = self.collections.get(name)
        if existing:
            if existing["dim"] != dim or existing["metric"] != metric:
                raise DependencyError(
                    f"集合 {name} 已存在但字段不兼容（dim={existing['dim']}, metric={existing['metric']}）。不会覆盖或删除该集合。"
                )
            return name
        self.collections[name] = {"dim": dim, "metric": metric, "rows": {}}
        return name

    def drop_collection(self, name: str) -> None:
        raise DependencyError("默认安全策略禁止删除集合。")

    def upsert_records(self, modality: str, records: list[dict]) -> int:
        name = self.ensure_collection(modality)
        store = self.collections[name]["rows"]
        for item in records:
            vec = list(item["vector"])
            if len(vec) != self.collections[name]["dim"]:
                raise DependencyError("向量维度与集合不兼容，拒绝写入。")
            store[item["id"]] = dict(item)
        return len(records)

    def search(self, modality: str, vector: list[float], *, limit: int, offset: int = 0, filter_expr: str | None = None) -> list[dict]:
        name = self.ensure_collection(modality)
        rows = list(self.collections[name]["rows"].values())
        scored = []
        for row in rows:
            score = _cosine(vector, row["vector"])
            scored.append({**{k: v for k, v in row.items() if k != "vector"}, "score": score})
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[offset: offset + limit]

    def delete_vectors(self, refs: list) -> VectorDeleteResult:
        self.delete_calls = getattr(self, "delete_calls", [])
        try:
            locators = as_vector_locators(refs)
        except ValueError as exc:
            return VectorDeleteResult(False, False, True, str(exc), serialize_vector_refs(refs))
        self.delete_calls.append(list(locators))
        removed = 0
        for loc in locators:
            store = (self.collections.get(loc.collection) or {}).get("rows")
            if store is None:
                continue
            if loc.remote_id in store:
                del store[loc.remote_id]
                removed += 1
            if loc.asset_id:
                for k in [key for key, row in store.items() if row.get("asset_id") == loc.asset_id]:
                    del store[k]
                    removed += 1
        return VectorDeleteResult(True, True, False, f"测试索引已删除 {removed} 条。", [loc.key() for loc in locators])

    def delete_by_asset(self, asset_id: str, collections: list[str] | None = None) -> VectorDeleteResult:
        targets = list(collections) if collections else list(self.collections.keys())
        removed = 0
        for name in targets:
            store = (self.collections.get(name) or {}).get("rows")
            if store is None:
                continue
            for k in [key for key, row in store.items() if row.get("asset_id") == asset_id]:
                del store[k]
                removed += 1
        return VectorDeleteResult(True, True, False, f"测试索引按 asset_id 删除 {removed} 条。", [f"{name}::asset:{asset_id}" for name in targets])

    def seed_collection(self, name: str, dim: int, rows: dict) -> None:
        existing = self.collections.get(name)
        if existing is None:
            self.collections[name] = {"dim": int(dim), "metric": "COSINE", "rows": dict(rows)}
            return
        existing["rows"].update(rows)
