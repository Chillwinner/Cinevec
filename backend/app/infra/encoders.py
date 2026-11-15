from __future__ import annotations

from pathlib import Path

from app.domain.errors import NotConfiguredError


class PathBlockedEncoder:
    """可注入本地模型路径的运行时适配，但未实现真实推理时保持阻断。

    不下载大模型，不把路径存在当成可推理，不生成假向量。
    INT8 现成包尚未确认，因此即使路径存在也不得假装已加载。
    """

    def __init__(
        self,
        kind: str,
        model_path: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        dim: int | None = None,
    ) -> None:
        self.kind = kind
        self._path = (model_path or "").strip() or None
        self._model_id = model_id
        self._model_version = model_version
        self._dim = dim

    def available(self) -> bool:
        return False

    def _kind_label(self) -> str:
        return "图文" if self.kind == "image_text" else "字幕"

    def status_message(self) -> str:
        if not self._path:
            if self.kind == "image_text":
                return "图文向量模型尚未接入。Chinese-CLIP ViT-B/16 INT8 暂无现成确定文件，本阶段未下载大模型，也不会生成假向量。"
            return "字幕文本向量接口尚未接入，不会生成假向量。"
        exists = Path(self._path).exists()
        return (
            f"已配置{self._kind_label()}本地模型路径（存在={exists}），"
            "但本阶段尚未实现真实推理加载。不会下载大模型，也不会生成假向量。接口保持阻断。"
        )

    def public_status(self) -> dict:
        return {
            "status": "blocked" if self._path else "unconfigured",
            "message": self.status_message(),
            "path_configured": bool(self._path),
            "path_exists": bool(self._path and Path(self._path).exists()),
            "inference_ready": False,
            "model_id": self._model_id,
            "model_version": self._model_version,
            "dim": self._dim,
        }

    def encode_text(self, text: str) -> list[float]:
        raise NotConfiguredError(self.status_message(), code="encoder_unconfigured")

    def encode_image(self, image_bytes: bytes) -> list[float]:
        raise NotConfiguredError(self.status_message(), code="encoder_unconfigured")
