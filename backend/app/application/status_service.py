from __future__ import annotations

from pathlib import Path

from app.domain.ports import FileStore, ImageTextEncoder, VectorIndex
from app.infra.config import Settings
from app.infra.ffmpeg_tools import ffmpeg_status
from app.infra.resources import snapshot as resource_snapshot


class StatusService:
    def __init__(
        self,
        settings: Settings,
        encoder: ImageTextEncoder,
        index: VectorIndex,
        sqlite_path: Path,
        file_store: FileStore | None = None,
        sample_interval: float | None = None,
        subtitle_encoder=None,
        auth_service=None,
        face_service=None,
    ) -> None:
        self._settings = settings
        self._encoder = encoder
        self._index = index
        self._sqlite_path = sqlite_path
        self._file_store = file_store
        self._sample_interval = sample_interval
        self._subtitle_encoder = subtitle_encoder
        self._auth = auth_service
        self._face_service = face_service

    def snapshot(self) -> dict:
        ff = ffmpeg_status(self._settings)
        store = self._file_store
        interval = (
            self._sample_interval
            if self._sample_interval is not None
            else self._settings.sample_interval_seconds
        )
        return {
            "sqlite": {
                "status": "ok",
                "message": f"业务与任务库可用：{self._sqlite_path}",
            },
            "minio_s3": {
                "status": "unconfigured"
                if not (self._settings.s3_endpoint and self._settings.s3_bucket)
                else "configured_env",
                "message": "默认使用已登记的存储源配置。全局环境变量未提供时，以各存储源为准。未配置时不会假装已接通。",
            },
            "file_store": {
                "status": "ok" if store and store.available() else "unconfigured",
                "message": store.status_message() if store else "预览存储尚未装配。",
            },
            "ffmpeg": {
                "status": ff["status"],
                "message": ff["message"],
            },
            "milvus": self._dep_status(self._index),
            "embedding_model": self._dep_status(self._encoder),
            "subtitle_encoder": self._dep_status(self._subtitle_encoder)
            if self._subtitle_encoder is not None
            else {
                "status": "unconfigured",
                "message": "字幕编码器尚未装配。",
                "inference_ready": False,
            },
            "auth": self._auth_snapshot(),
            "sample_interval_seconds": interval,
            "resources": resource_snapshot(encoder=self._encoder),
            "face_recognition": self._face_service.public_status() if self._face_service is not None else {
                "status": "disabled",
                "enabled": False,
                "available": False,
                "backend": "disabled",
                "dim": 512,
                "people_count": 0,
                "faces_count": 0,
                "message": "人脸识别默认关闭。",
            },
        }

    def _dep_status(self, dep) -> dict:
        if dep is None:
            return {"status": "unconfigured", "message": "未装配。"}
        public = getattr(dep, "public_status", None)
        if callable(public):
            data = dict(public())
            data.setdefault("message", dep.status_message() if hasattr(dep, "status_message") else "")
            return data
        available = dep.available() if hasattr(dep, "available") else False
        return {
            "status": "ok" if available else "unconfigured",
            "message": dep.status_message() if hasattr(dep, "status_message") else "",
        }

    def _auth_snapshot(self) -> dict:
        if self._auth is not None:
            data = self._auth.public_status().as_dict()
            data["status"] = "enabled" if data["enabled"] else "disabled"
            return data
        return {
            "status": "disabled",
            "enabled": False,
            "message": "登录鉴权默认关闭。",
            "secret_configured": bool((self._settings.jwt_secret or "").strip()),
        }
