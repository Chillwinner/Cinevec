from app.domain.errors import NotConfiguredError
from app.domain.lifecycle import VectorDeleteResult, serialize_vector_refs


class UnconfiguredVectorIndex:
    def available(self) -> bool:
        return False

    def status_message(self) -> str:
        return "Milvus 尚未配置连接信息，向量检索不可用。不会返回假结果。"

    def public_status(self) -> dict:
        return {
            "status": "unconfigured",
            "message": self.status_message(),
            "uri_configured": False,
            "token_configured": False,
            "metric": "COSINE",
        }

    def search(self, *_args, **_kwargs):
        raise NotConfiguredError(self.status_message(), code="milvus_unconfigured")

    def upsert_records(self, modality: str, records: list[dict]) -> int:
        raise NotConfiguredError(self.status_message(), code="milvus_unconfigured")

    def delete_vectors(self, refs: list) -> VectorDeleteResult:
        return VectorDeleteResult(
            attempted=False,
            succeeded=False,
            pending=True,
            message="Milvus 未配置，未执行向量删除，不会假装删除成功。元数据排除可以先生效，向量清理将作为延迟任务保留。",
            refs=serialize_vector_refs(refs),
        )

    def delete_by_asset(self, asset_id: str, collections: list[str] | None = None) -> VectorDeleteResult:
        return VectorDeleteResult(
            attempted=False,
            succeeded=False,
            pending=True,
            message="Milvus 未配置，未执行按资产删除向量。",
            refs=[asset_id] if asset_id else [],
        )


class UnconfiguredImageTextEncoder:
    def available(self) -> bool:
        return False

    def status_message(self) -> str:
        return "图文向量模型尚未接入。Chinese-CLIP ViT-B/16 INT8 暂无现成确定文件，本阶段未下载大模型，也不会生成假向量。"

    def public_status(self) -> dict:
        return {
            "status": "unconfigured",
            "message": self.status_message(),
            "path_configured": False,
            "inference_ready": False,
        }

    def encode_text(self, text: str) -> list[float]:
        raise NotConfiguredError(self.status_message(), code="encoder_unconfigured")

    def encode_image(self, image_bytes: bytes) -> list[float]:
        raise NotConfiguredError(self.status_message(), code="encoder_unconfigured")


class UnconfiguredSubtitleEncoder:
    def available(self) -> bool:
        return False

    def status_message(self) -> str:
        return "字幕文本向量接口尚未接入，不会生成假向量。"

    def public_status(self) -> dict:
        return {
            "status": "unconfigured",
            "message": self.status_message(),
            "path_configured": False,
            "inference_ready": False,
        }

    def encode_text(self, text: str) -> list[float]:
        raise NotConfiguredError(self.status_message(), code="encoder_unconfigured")
