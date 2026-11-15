from __future__ import annotations

import json

import pytest

from app.domain.errors import DependencyError, NotConfiguredError
from app.infra.milvus_rest import CREATE_PATH, DELETE_PATH, DESCRIBE_PATH, LIST_PATH, LOAD_PATH, SEARCH_PATH, UPSERT_PATH, MilvusRestIndex, collection_name
from app.infra.redact import redact


def test_collection_names_are_namespaced_and_isolated():
    a = collection_name("home", "clip", "v1", 512, "frame")
    b = collection_name("home", "clip", "v1", 512, "subtitle")
    c = collection_name("home", "clip", "v2", 512, "frame")
    d = collection_name("home", "clip", "v1", 768, "frame")
    e = collection_name("other", "clip", "v1", 512, "frame")
    assert a.startswith("pica_")
    assert len({a, b, c, d, e}) == 5
    assert "512" in a and "frame" in a


def test_unconfigured_refuses_fake_search():
    idx = MilvusRestIndex(uri=None, dim=512)
    assert not idx.available()
    with pytest.raises(NotConfiguredError):
        idx.search("frame", [0.0] * 512, limit=10)


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.collections = {}
        self.dropped = []

    def __call__(self, path, body, headers):
        self.calls.append((path, body, headers))
        # 凭证只在 header，测试里可以断言但不要打印
        if path == LIST_PATH:
            return {"code": 0, "data": list(self.collections)}
        if path == DESCRIBE_PATH:
            name = body["collectionName"]
            if name not in self.collections:
                return {"code": 100, "message": "collection not found"}
            return {"code": 0, "data": self.collections[name]}
        if path == CREATE_PATH:
            name = body["collectionName"]
            fields = body["schema"]["fields"]
            dim = None
            for field in fields:
                if field["fieldName"] == "vector":
                    dim = int(field["elementTypeParams"]["dim"])
            self.collections[name] = {
                "fields": fields,
                "indexes": body.get("indexParams") or [],
                "dim": dim,
                "rows": [],
            }
            return {"code": 0, "data": {}}
        if path == LOAD_PATH:
            return {"code": 0, "data": {}}
        if path == UPSERT_PATH:
            self.collections[body["collectionName"]]["rows"].extend(body["data"])
            return {"code": 0, "data": {"upsertCount": len(body["data"])}}
        if path == SEARCH_PATH:
            rows = self.collections.get(body["collectionName"], {}).get("rows") or []
            hits = [{"id": r["id"], "score": 0.5, "entity": r} for r in rows[: body.get("limit", 10)]]
            return {"code": 0, "data": hits}
        if path == DELETE_PATH:
            return {"code": 0, "data": {}}
        if "drop" in path:
            raise AssertionError("不得调用 drop")
        return {"code": 1, "message": f"unexpected {path}"}


def test_ensure_creates_on_demand_and_does_not_drop():
    transport = FakeTransport()
    idx = MilvusRestIndex("http://milvus.example:19530", token="user:s3cret-token", dim=8, transport=transport)
    name = idx.ensure_collection("frame")
    assert name == collection_name("default", "unset", "unset", 8, "frame")
    assert any(c[0] == DESCRIBE_PATH for c in transport.calls)
    assert any(c[0] == CREATE_PATH for c in transport.calls)
    assert all("drop" not in c[0] for c in transport.calls)
    # 再次 ensure 不重复创建
    idx.ensure_collection("frame")
    creates = [c for c in transport.calls if c[0] == CREATE_PATH]
    assert len(creates) == 1


def test_refuses_incompatible_existing_collection():
    transport = FakeTransport()
    transport.collections["pica_default_unset_unset_8_frame"] = {
        "fields": [
            {"fieldName": "id", "dataType": "VarChar"},
            {"fieldName": "vector", "dataType": "FloatVector", "elementTypeParams": {"dim": "512"}},
        ],
        "indexes": [{"metricType": "L2"}],
    }
    idx = MilvusRestIndex("http://milvus.example:19530", dim=8, transport=transport)
    with pytest.raises(DependencyError) as exc:
        idx.ensure_collection("frame")
    assert "不会覆盖或删除" in exc.value.message
    assert not any(c[0] == CREATE_PATH for c in transport.calls)


def test_upsert_search_and_error_redacts_token():
    transport = FakeTransport()
    idx = MilvusRestIndex("http://milvus.example:19530", token="user:s3cret-token", dim=2, transport=transport)
    idx.upsert_records("frame", [{
        "id": "a:1:frame:0",
        "vector": [1.0, 0.0],
        "asset_id": "a",
        "version": 1,
        "unit_index": 0,
        "time_seconds": 1.2,
        "preview_key": "previews/a/0.jpg",
    }])
    hits = idx.search("frame", [1.0, 0.0], limit=5)
    assert hits[0]["asset_id"] == "a"
    assert hits[0]["score"] == 0.5

    def leaking(_path, _body, _headers):
        raise DependencyError("Authorization: Bearer user:s3cret-token failed")

    # redact helper 本身必须挡住 token
    hidden = redact("Authorization: Bearer user:s3cret-token failed")
    assert "s3cret-token" not in hidden
    assert "<redacted>" in hidden

    status = idx.public_status()
    dumped = json.dumps(status)
    assert "s3cret-token" not in dumped
    assert status["token_configured"] is True
    assert status["uri_configured"] is True


def test_delete_vectors_targets_only_named_collection():
    from app.domain.lifecycle import VectorLocator

    transport = FakeTransport()
    clip = collection_name("dev", "chinese-clip-vit-b-16", "int8_dynamic", 512, "frame", "legacy_center_crop_v0")
    gemma = collection_name("dev", "embeddinggemma-2", "float32", 768, "frame", "full_frame_passthrough_v1")
    shared = "asset-1:1:frame:0"
    for name, dim in ((clip, 512), (gemma, 768)):
        transport.collections[name] = {
            "fields": [
                {"fieldName": "id", "dataType": "VarChar"},
                {"fieldName": "vector", "dataType": "FloatVector", "elementTypeParams": {"dim": str(dim)}},
            ],
            "indexes": [{"metricType": "COSINE"}],
            "rows": [{"id": shared, "asset_id": "asset-1", "version": 1, "unit_index": 0}],
        }
    idx = MilvusRestIndex(
        "http://milvus.example:19530",
        dim=768,
        profile="dev",
        model_id="embeddinggemma-2",
        model_version="float32",
        preprocess_version="full_frame_passthrough_v1",
        transport=transport,
    )
    result = idx.delete_vectors([VectorLocator(collection=gemma, remote_id=shared, modality="frame")])
    assert result.succeeded is True
    deletes = [c for c in transport.calls if c[0] == DELETE_PATH]
    assert len(deletes) == 1
    assert deletes[0][1]["collectionName"] == gemma
    assert shared in deletes[0][1]["filter"]
    assert all(c[1]["collectionName"] != clip for c in deletes)
    assert all("drop" not in c[0] for c in transport.calls)

    bare = idx.delete_vectors([shared])
    assert bare.succeeded is False
    assert bare.attempted is False
    assert len([c for c in transport.calls if c[0] == DELETE_PATH]) == 1
