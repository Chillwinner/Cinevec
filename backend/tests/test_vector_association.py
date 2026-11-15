from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.application.index_write_service import IndexWriteService, entity_id_for_unit, vector_ref
from app.application.lifecycle_service import LifecycleService
from app.domain.lifecycle import VectorLocator, as_vector_locators
from app.domain.models import Asset, Job, Source
from app.infra.clock import now
from app.infra.db import connect, init_db
from app.infra.file_store import LocalFileStore
from app.infra.milvus_rest import collection_name
from app.infra.sqlite_repos import SqliteMetadataRepository, SqliteTaskRepository
from app.infra.vector_association import plan_remote_id_backfill
from tests.fakes import MemoryEncoder, MemoryVectorIndex


CLIP_COL = collection_name("dev", "chinese-clip-vit-b-16", "int8_dynamic", 512, "frame", "legacy_center_crop_v0")
GEMMA_COL = collection_name("dev", "embeddinggemma-2", "float32", 768, "frame", "full_frame_passthrough_v1")
SHORT = "asset-1:1:frame:0"


def _source_and_asset(meta, *, version: int = 1):
    source = meta.add_source(
        Source(
            id="src-1",
            name="s",
            endpoint="http://x",
            bucket="b",
            region="",
            role="input",
            is_platform_storage=False,
            access_key_configured=True,
            secret_configured=True,
            last_probe_status="ok",
            last_probe_message="ok",
            created_at=now(),
            updated_at=now(),
        ),
        "k",
        "s",
    )
    asset = meta.upsert_asset(
        Asset(
            id="asset-1",
            source_id=source.id,
            object_key="source/demo/ball.jpg",
            kind="image",
            vectorize=True,
            status="ready",
            created_at=now(),
            updated_at=now(),
            lifecycle="active",
            index_state="ready",
            version=version,
            ready_version=version,
        )
    )
    return source, asset


def _job(tasks, source_id, asset_id, job_id="job-write"):
    return tasks.add_job(
        Job(
            id=job_id,
            job_type="index_asset",
            status="waiting",
            source_id=source_id,
            asset_id=asset_id,
            stage="embedding",
            step="test",
            progress=0,
            queue_position=None,
            error_code=None,
            error_message=None,
        )
    )


def test_backfill_uses_only_matching_real_entities():
    local = [
        {
            "vector_id": "clip-local",
            "asset_id": "asset-1",
            "version": 1,
            "unit_index": 0,
            "collection": CLIP_COL,
            "remote_vector_id": "",
        },
        {
            "vector_id": f"{SHORT}:dev:embeddinggemma-2:float32:768:full_frame_passthrough_v1",
            "asset_id": "asset-1",
            "version": 1,
            "unit_index": 0,
            "collection": GEMMA_COL,
            "remote_vector_id": "",
        },
        {
            "vector_id": "orphan",
            "asset_id": "asset-2",
            "version": 1,
            "unit_index": 0,
            "collection": GEMMA_COL,
            "remote_vector_id": "",
        },
    ]
    remote = [
        {"collection": CLIP_COL, "id": SHORT, "asset_id": "asset-1", "version": 1, "unit_index": 0},
        {"collection": GEMMA_COL, "id": SHORT, "asset_id": "asset-1", "version": 1, "unit_index": 0},
    ]
    plan = plan_remote_id_backfill(local, remote)
    by_vid = {u["vector_id"]: u["remote_vector_id"] for u in plan["updates"]}
    assert by_vid["clip-local"] == SHORT
    assert by_vid[f"{SHORT}:dev:embeddinggemma-2:float32:768:full_frame_passthrough_v1"] == SHORT
    assert "orphan" not in by_vid
    assert any(item["vector_id"] == "orphan" and item["reason"] == "missing" for item in plan["unmatched"])
    assert plan["conflicts"] == []


def test_backfill_skips_ambiguous_incomplete_and_existing_conflict():
    local = [
        {"vector_id": "a", "asset_id": "asset-1", "version": 1, "unit_index": 0, "collection": GEMMA_COL, "remote_vector_id": ""},
        {"vector_id": "b", "asset_id": "asset-1", "version": 1, "unit_index": 1, "collection": GEMMA_COL, "remote_vector_id": "already"},
        {"vector_id": "c", "asset_id": "asset-1", "version": 1, "unit_index": 2, "collection": "", "remote_vector_id": ""},
    ]
    remote = [
        {"collection": GEMMA_COL, "id": "id-one", "asset_id": "asset-1", "version": 1, "unit_index": 0},
        {"collection": GEMMA_COL, "id": "id-two", "asset_id": "asset-1", "version": 1, "unit_index": 0},
        {"collection": GEMMA_COL, "id": "other", "asset_id": "asset-1", "version": 1, "unit_index": 1},
        {"collection": GEMMA_COL, "id": "", "asset_id": "asset-1", "version": 1, "unit_index": 2},
    ]
    plan = plan_remote_id_backfill(local, remote)
    assert plan["updates"] == []
    assert any(c["vector_id"] == "b" and c["existing"] == "already" and c["observed"] == "other" for c in plan["conflicts"])
    reasons = {item["vector_id"]: item["reason"] for item in plan["unmatched"]}
    assert reasons["a"] == "ambiguous"
    assert reasons["c"] == "incomplete_local"


def test_as_vector_locators_rejects_bare_ids():
    try:
        as_vector_locators([SHORT])
    except ValueError as exc:
        assert "collection" in str(exc)
    else:
        raise AssertionError("bare ids must be rejected")
    loc = as_vector_locators([{"collection": GEMMA_COL, "remote_id": SHORT, "modality": "frame"}])[0]
    assert loc.collection == GEMMA_COL
    assert loc.remote_id == SHORT


def test_same_profile_reprocess_reuses_remote_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PICA_LOAD_DOTENV", "0")
    conn = connect(tmp_path / "pica.db")
    init_db(conn)
    meta = SqliteMetadataRepository(conn)
    tasks = SqliteTaskRepository(conn)
    store = LocalFileStore(tmp_path / "store", label="pytest")
    source, asset = _source_and_asset(meta)
    key = "model-inputs/asset-1/full.png"
    store.put_bytes(key, b"FULL", "image/png")
    meta.upsert_frame(
        asset.id,
        "job-frame",
        SimpleNamespace(
            index=0,
            time_seconds=0.0,
            kept=True,
            drop_reason=None,
            preview_key="previews/asset-1/source.jpg",
            model_input_key=key,
            extra={"model_inputs_by_preprocess": {"full_frame_passthrough_v1": key}},
        ),
    )
    index = MemoryVectorIndex(profile="dev", model_id="embeddinggemma-2", model_version="float32", dim=768)
    writer = IndexWriteService(
        meta,
        tasks,
        store,
        MemoryEncoder(dim=768),
        index,
        profile="dev",
        model_id="embeddinggemma-2",
        model_version="float32",
        dim=768,
        preprocess_version="full_frame_passthrough_v1",
    )
    first = _job(tasks, source.id, asset.id, "job-1")
    assert writer.process_frames(first) == "completed"
    second = _job(tasks, source.id, asset.id, "job-2")
    assert writer.process_frames(second) == "completed"

    units = meta.list_vector_units(asset.id)
    assert len(units) == 1
    unit = units[0]
    expected_local = vector_ref(
        asset.id,
        1,
        "frame",
        0,
        profile="dev",
        model_id="embeddinggemma-2",
        model_version="float32",
        dim=768,
        preprocess_version="full_frame_passthrough_v1",
    )
    expected_remote = entity_id_for_unit(asset.id, 1, "frame", 0)
    assert unit["vector_id"] == expected_local
    assert unit["remote_vector_id"] == expected_remote
    assert unit["vector_id"] != unit["remote_vector_id"]
    assert unit["collection"] == GEMMA_COL
    rows = index.collections[index.name_for("frame")]["rows"]
    assert list(rows) == [expected_remote]
    assert len(rows) == 1


def test_delete_and_cleanup_do_not_cross_collections(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PICA_LOAD_DOTENV", "0")
    conn = connect(tmp_path / "pica.db")
    init_db(conn)
    meta = SqliteMetadataRepository(conn)
    tasks = SqliteTaskRepository(conn)
    store = LocalFileStore(tmp_path / "store", label="pytest")
    source, asset = _source_and_asset(meta)
    index = MemoryVectorIndex(profile="dev", model_id="embeddinggemma-2", model_version="float32", dim=768)
    # Same short id in two collections: a naive id-only delete would wipe both.
    index.seed_collection(CLIP_COL, 512, {SHORT: {"id": SHORT, "vector": [0.0] * 512, "asset_id": asset.id, "version": 1, "unit_index": 0}})
    index.seed_collection(GEMMA_COL, 768, {SHORT: {"id": SHORT, "vector": [0.0] * 768, "asset_id": asset.id, "version": 1, "unit_index": 0}})
    index.seed_collection(
        GEMMA_COL,
        768,
        {"asset-1:0:frame:0": {"id": "asset-1:0:frame:0", "vector": [0.0] * 768, "asset_id": asset.id, "version": 0, "unit_index": 0}},
    )
    meta.upsert_vector_unit(
        {
            "vector_id": SHORT,
            "asset_id": asset.id,
            "version": 1,
            "modality": "frame",
            "unit_index": 0,
            "collection": CLIP_COL,
            "profile": "dev",
            "model_id": "chinese-clip-vit-b-16",
            "model_version": "int8_dynamic",
            "dim": 512,
            "preprocess_version": "legacy_center_crop_v0",
            "remote_vector_id": SHORT,
        }
    )
    gemma_local = vector_ref(
        asset.id, 1, "frame", 0,
        profile="dev", model_id="embeddinggemma-2", model_version="float32", dim=768,
        preprocess_version="full_frame_passthrough_v1",
    )
    meta.upsert_vector_unit(
        {
            "vector_id": gemma_local,
            "asset_id": asset.id,
            "version": 1,
            "modality": "frame",
            "unit_index": 0,
            "collection": GEMMA_COL,
            "profile": "dev",
            "model_id": "embeddinggemma-2",
            "model_version": "float32",
            "dim": 768,
            "preprocess_version": "full_frame_passthrough_v1",
            "remote_vector_id": SHORT,
        }
    )
    stale_local = vector_ref(
        asset.id, 0, "frame", 0,
        profile="dev", model_id="embeddinggemma-2", model_version="float32", dim=768,
        preprocess_version="full_frame_passthrough_v1",
    )
    meta.upsert_vector_unit(
        {
            "vector_id": stale_local,
            "asset_id": asset.id,
            "version": 0,
            "modality": "frame",
            "unit_index": 0,
            "collection": GEMMA_COL,
            "profile": "dev",
            "model_id": "embeddinggemma-2",
            "model_version": "float32",
            "dim": 768,
            "preprocess_version": "full_frame_passthrough_v1",
            "remote_vector_id": "asset-1:0:frame:0",
        }
    )

    writer = IndexWriteService(
        meta, tasks, store, MemoryEncoder(dim=768), index,
        profile="dev", model_id="embeddinggemma-2", model_version="float32", dim=768,
        preprocess_version="full_frame_passthrough_v1",
    )
    result = writer.cleanup_old_versions(asset.id, keep_version=1)
    assert result["succeeded"] is True
    assert SHORT in index.collections[CLIP_COL]["rows"]
    assert SHORT in index.collections[GEMMA_COL]["rows"]
    assert "asset-1:0:frame:0" not in index.collections[GEMMA_COL]["rows"]
    remaining = meta.list_vector_units(asset.id)
    assert {u["vector_id"] for u in remaining} == {SHORT, gemma_local}
    assert all(u["collection"] != GEMMA_COL or int(u["version"]) == 1 for u in remaining)

    # Targeted delete of the current-space current id must not touch the old collection.
    index.delete_vectors([VectorLocator(collection=GEMMA_COL, remote_id=SHORT, modality="frame")])
    assert SHORT not in index.collections[GEMMA_COL]["rows"]
    assert SHORT in index.collections[CLIP_COL]["rows"]


def test_purge_locates_each_space_by_collection_and_remote_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PICA_LOAD_DOTENV", "0")
    conn = connect(tmp_path / "pica.db")
    init_db(conn)
    meta = SqliteMetadataRepository(conn)
    tasks = SqliteTaskRepository(conn)
    source, asset = _source_and_asset(meta)
    index = MemoryVectorIndex(profile="dev", model_id="embeddinggemma-2", model_version="float32", dim=768)
    index.seed_collection(CLIP_COL, 512, {SHORT: {"id": SHORT, "vector": [0.0] * 512}})
    index.seed_collection(GEMMA_COL, 768, {SHORT: {"id": SHORT, "vector": [0.0] * 768}})
    meta.upsert_vector_unit(
        {
            "vector_id": SHORT,
            "asset_id": asset.id,
            "version": 1,
            "modality": "frame",
            "unit_index": 0,
            "collection": CLIP_COL,
            "profile": "dev",
            "model_id": "chinese-clip-vit-b-16",
            "model_version": "int8_dynamic",
            "dim": 512,
            "preprocess_version": "legacy_center_crop_v0",
            "remote_vector_id": SHORT,
        }
    )
    gemma_local = vector_ref(
        asset.id, 1, "frame", 0,
        profile="dev", model_id="embeddinggemma-2", model_version="float32", dim=768,
        preprocess_version="full_frame_passthrough_v1",
    )
    meta.upsert_vector_unit(
        {
            "vector_id": gemma_local,
            "asset_id": asset.id,
            "version": 1,
            "modality": "frame",
            "unit_index": 0,
            "collection": GEMMA_COL,
            "profile": "dev",
            "model_id": "embeddinggemma-2",
            "model_version": "float32",
            "dim": 768,
            "preprocess_version": "full_frame_passthrough_v1",
            "remote_vector_id": SHORT,
        }
    )
    life = LifecycleService(meta, tasks, index)
    job = life.exclude_asset(asset.id, "test")[1]
    status = life.process_purge(job)
    assert status == "completed"
    calls = index.delete_calls
    assert calls
    keys = {(loc.collection, loc.remote_id) for loc in calls[-1]}
    assert keys == {(CLIP_COL, SHORT), (GEMMA_COL, SHORT)}
    assert SHORT not in index.collections[CLIP_COL]["rows"]
    assert SHORT not in index.collections[GEMMA_COL]["rows"]


def test_model_input_falls_back_when_preprocess_map_missing():
    writer = IndexWriteService(
        None,
        None,
        None,
        MemoryEncoder(dim=768),
        MemoryVectorIndex(profile="dev", model_id="embeddinggemma-2", model_version="float32", dim=768),
        profile="dev",
        model_id="embeddinggemma-2",
        model_version="float32",
        dim=768,
        preprocess_version="full_frame_passthrough_v1",
    )
    assert writer._model_input_key({"model_input_key": "model-inputs/a/source.png", "extra": {}}) == "model-inputs/a/source.png"
    assert writer._model_input_key({
        "model_input_key": "old.png",
        "extra": {"model_inputs_by_preprocess": {"other": "other.png"}},
    }) is None
    assert writer._model_input_key({
        "model_input_key": "old.png",
        "extra": {"model_inputs_by_preprocess": {"full_frame_passthrough_v1": "new.png"}},
    }) == "new.png"
