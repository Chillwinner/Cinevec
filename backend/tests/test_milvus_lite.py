from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.infra.config import Settings
from app.infra.file_store import LocalFileStore
from app.infra.milvus_lite import MilvusLiteIndex
from app.main import build_vector_index, create_app
from tests.conftest import FakeReader


class FakeMilvusClient:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    def list_collections(self) -> list[str]:
        return list(self.collections.keys())

    def has_collection(self, name: str) -> bool:
        return name in self.collections

    def create_collection(self, *, collection_name: str, **kwargs: Any) -> None:
        self.collections.setdefault(collection_name, {})

    def upsert(self, *, collection_name: str, data: list[dict[str, Any]]) -> None:
        col = self.collections.setdefault(collection_name, {})
        for row in data:
            col[row["id"]] = dict(row)

    def search(self, *, collection_name: str, data: list[list[float]], limit: int = 10, **kwargs: Any):
        col = self.collections.get(collection_name, {})
        hits = []
        for row in list(col.values())[:limit]:
            hits.append({"id": row["id"], "distance": 0.91, "entity": row})
        return [hits]

    def delete(self, *, collection_name: str, filter: str) -> None:
        col = self.collections.get(collection_name, {})
        for key in list(col.keys()):
            if key in filter or col[key].get("asset_id", "") in filter:
                col.pop(key, None)


def test_milvus_lite_index_crud_and_factory(tmp_path: Path):
    fake = FakeMilvusClient()
    idx = MilvusLiteIndex(
        tmp_path / "milvus_lite.db",
        profile="default",
        model_id="gemma2",
        model_version="v1",
        dim=4,
        client=fake,
    )
    assert idx.available() is True
    assert idx.public_status()["backend"] == "milvus_lite"
    assert idx.probe()["ok"] is True

    written = idx.upsert_records(
        "frame",
        [
            {
                "id": "v1",
                "vector": [1.0, 0.0, 0.0, 0.0],
                "asset_id": "asset-1",
                "version": 1,
                "unit_index": 0,
                "time_seconds": 2.5,
                "preview_key": "previews/asset-1/f0.jpg",
            }
        ],
    )
    assert written == 1
    hits = idx.search("frame", [1.0, 0.0, 0.0, 0.0], limit=5)
    assert len(hits) == 1
    assert hits[0]["asset_id"] == "asset-1"
    assert hits[0]["score"] == 0.91

    deleted = idx.delete_by_asset("asset-1")
    assert deleted.succeeded is True
    assert idx.search("frame", [1.0, 0.0, 0.0, 0.0], limit=5) == []

    # Factory routing via milvus_backend="lite"
    settings = Settings(
        milvus_backend="lite",
        milvus_lite_path=tmp_path / "m.db",
        model_dim=768,
    )
    built = build_vector_index(settings)
    assert isinstance(built, MilvusLiteIndex)
    assert built.available() is True


def test_auto_init_platform_storage_registers_default_minio(tmp_path: Path):
    settings = Settings(
        sqlite_path=tmp_path / "db.sqlite",
        worker_enabled=False,
        auto_init_platform_storage=True,
        s3_endpoint="http://minio:9000",
        s3_bucket="cinevec-media",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
    )
    app = create_app(
        settings,
        sqlite_path=tmp_path / "db.sqlite",
        source_reader=FakeReader("ok"),
        worker_enabled=False,
        file_store=LocalFileStore(tmp_path / "store", label="pytest"),
        work_root=tmp_path / "work",
    )
    with TestClient(app) as client:
        sources = client.get("/api/sources").json()["items"]
        assert len(sources) == 1
        assert sources[0]["is_platform_storage"] is True
        assert sources[0]["bucket"] == "cinevec-media"
