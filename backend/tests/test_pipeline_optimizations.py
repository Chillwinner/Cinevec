from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.application.face_service import FaceService
from app.application.index_write_service import IndexWriteService
from app.application.job_service import JobService
from app.domain.face import DetectedFace, FACE_DIM, FaceSettings
from app.domain.models import Asset, Job
from app.infra.clock import now


@dataclass
class _StubIndex:
    calls: list[tuple[str, list[dict]]] = field(default_factory=list)

    def available(self) -> bool:
        return True

    def name_for(self, modality: str) -> str:
        return f"test_{modality}"

    def upsert_records(self, modality: str, records: list[dict]) -> None:
        self.calls.append((modality, list(records)))


@dataclass
class _StubEncoder:
    def available(self) -> bool:
        return True

    def encode_image(self, data: bytes) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]

    def encode_text(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]


class _StubStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def available(self) -> bool:
        return True

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.objects[key] = data

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]


class _StubMetadata:
    def __init__(self, asset: Asset, frames: list[dict]) -> None:
        self._asset = asset
        self._frames = frames
        self.vector_units: list[dict] = []
        self.activated: list[tuple[str, int]] = []
        self.faces: list[dict] = []
        self.people: list[dict] = []

    def get_asset(self, asset_id: str):
        return self._asset if asset_id == self._asset.id else None

    def list_frames(self, asset_id: str) -> list[dict]:
        return list(self._frames) if asset_id == self._asset.id else []

    def upsert_vector_unit(self, unit: dict) -> None:
        self.vector_units.append(unit)

    def activate_asset_version(self, asset_id: str, version: int) -> None:
        self.activated.append((asset_id, version))

    def update_asset(self, asset_id: str, **fields) -> None:
        for k, v in fields.items():
            setattr(self._asset, k, v)

    def delete_faces_by_asset(self, asset_id: str) -> list[dict]:
        return []

    def list_people(self, **kwargs) -> list[dict]:
        return list(self.people)

    def create_person(self, **kwargs) -> dict:
        person = {"id": f"p-{len(self.people) + 1}", **kwargs}
        self.people.append(person)
        return person

    def insert_face(self, face: dict) -> None:
        self.faces.append(face)

    def recompute_person_stats(self, person_id: str) -> dict | None:
        return next((p for p in self.people if p["id"] == person_id), None)


class _StubTasks:
    def __init__(self, job: Job) -> None:
        self.jobs: dict[str, Job] = {job.id: job}

    def get_job(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def create_job(self, job: Job) -> None:
        self.jobs[job.id] = job

    def find_active_jobs(self, job_type: str, source_id: str | None = None, asset_id: str | None = None) -> list[Job]:
        return [
            j
            for j in self.jobs.values()
            if j.job_type == job_type
            and (asset_id is None or j.asset_id == asset_id)
            and j.status in ("waiting", "running")
        ]

    def update_job(self, job_id: str, **fields) -> Job:
        job = self.jobs[job_id]
        for k, v in fields.items():
            setattr(job, k, v)
        return job


class _StubFaceService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def is_enabled(self) -> bool:
        return True

    def index_asset_faces(self, asset_id: str) -> dict:
        self.calls.append(asset_id)
        return {"faces_indexed": 0}


class _StubRecognizer:
    def __init__(self) -> None:
        self.calls = 0

    def available(self) -> bool:
        return True

    def detect_and_encode(self, image_bytes: bytes) -> list[DetectedFace]:
        self.calls += 1
        vec = [0.0] * FACE_DIM
        vec[0] = 1.0
        return [
            DetectedFace(
                bbox=(0.1, 0.1, 0.5, 0.5),
                det_score=0.99,
                embedding=vec,
                crop_jpeg=b"fake-crop",
            )
        ]


def test_batch_index_write_and_async_face_queue(tmp_path: Path) -> None:
    ts = now()
    asset = Asset(
        id="asset-1",
        source_id="src-1",
        object_key="k",
        kind="video",
        vectorize=True,
        status="media_ready",
        created_at=ts,
        updated_at=ts,
        version=1,
        ready_version=0,
    )
    store = _StubStore()
    frames = []
    for i in range(5):
        key = f"model/frame_{i}.png"
        store.put_bytes(key, b"png-bytes")
        frames.append({"index": i, "time_seconds": float(i * 5), "kept": True, "preview_key": f"prev/{i}.jpg", "model_input_key": key})

    meta = _StubMetadata(asset, frames)
    job = Job(
        id="job-1",
        job_type="index_asset",
        status="running",
        source_id="src-1",
        asset_id="asset-1",
        stage="embedding",
        step="",
        progress=0.0,
        queue_position=None,
        error_code=None,
        error_message=None,
    )
    tasks = _StubTasks(job)
    index = _StubIndex()
    face_stub = _StubFaceService()

    writer = IndexWriteService(
        metadata=meta,
        tasks=tasks,
        file_store=store,
        encoder=_StubEncoder(),
        index=index,
        face_service=face_stub,
    )
    outcome = writer.process_frames(job)

    assert outcome == "completed"
    # 5 frames written in a single batch upsert call
    assert len(index.calls) == 1
    assert index.calls[0][0] == "frame"
    assert len(index.calls[0][1]) == 5
    assert len(meta.vector_units) == 5
    # Face recognition was NOT called synchronously; instead an async job was queued
    assert face_stub.calls == []
    face_jobs = [j for j in tasks.jobs.values() if j.job_type == "index_faces"]
    assert len(face_jobs) == 1
    assert face_jobs[0].status == "waiting"

    # JobService executes the queued index_faces job asynchronously
    job_svc = JobService(tasks=tasks, encoder=_StubEncoder(), index=index, face_service=face_stub)
    done_job = job_svc.run_claimed(face_jobs[0], worker_id="w1")
    assert done_job.status == "completed"
    assert face_stub.calls == ["asset-1"]


def test_face_service_samples_video_frames_and_uploads_concurrently() -> None:
    ts = now()
    asset = Asset(
        id="asset-vid",
        source_id="src-1",
        object_key="k",
        kind="video",
        vectorize=True,
        status="ready",
        created_at=ts,
        updated_at=ts,
        version=1,
        ready_version=1,
    )
    store = _StubStore()
    frames = []
    for i in range(25):
        key = f"prev/frame_{i}.jpg"
        store.put_bytes(key, f"img-{i}".encode())
        frames.append({"index": i, "time_seconds": float(i), "kept": True, "preview_key": key})

    meta = _StubMetadata(asset, frames)
    recognizer = _StubRecognizer()
    face_svc = FaceService(
        metadata=meta,
        file_store=store,
        recognizer=recognizer,
        face_index=_StubIndex(),
        settings_getter=lambda: FaceSettings(enabled=True),
    )

    res = face_svc.index_asset_faces("asset-vid", max_video_frames=16)
    assert res["faces_indexed"] == 16
    assert recognizer.calls == 16
    assert len(meta.faces) == 16
    # First and last frames must be included in uniform sampling
    indexed_frame_indices = [f["frame_index"] for f in meta.faces]
    assert indexed_frame_indices[0] == 0
    assert indexed_frame_indices[-1] == 24
    # All 16 face crops uploaded to store
    assert all(f["crop_key"] in store.objects for f in meta.faces)
