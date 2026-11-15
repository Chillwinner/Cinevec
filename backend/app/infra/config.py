from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PICA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    sqlite_path: Path = Field(default=Path("data/pica.db"))
    work_dir: Path = Field(default=Path("data/work"))
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = (
        "http://127.0.0.1:5173,http://localhost:5173,"
        "http://127.0.0.1:5174,http://localhost:5174"
    )
    auth_enabled: bool = False
    jwt_secret: str | None = None
    sample_interval_seconds: float = 5.0
    max_upload_files: int = 20
    worker_enabled: bool = True
    worker_poll_seconds: float = 0.4
    lease_seconds: int = 30
    ffmpeg_path: str | None = None

    s3_endpoint: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str | None = None
    s3_region: str | None = None

    milvus_uri: str | None = None
    milvus_token: str | None = None
    milvus_user: str | None = None
    milvus_password: str | None = None
    milvus_db: str = "_default"
    vector_profile: str = "default"
    # remote_http 与 local_onnx 互斥，禁止混入同一向量空间。
    encoder_backend: str = ""
    clip_http_base_url: str = ""
    clip_http_timeout_seconds: float = 30.0
    model_id: str | None = None
    model_version: str | None = None
    model_dim: int | None = None
    model_path: str | None = None
    # 仅支持明确声明的 chinese_clip 官方分路 ONNX；留空表示未配置编码器。
    model_family: str = ""
    model_architecture: str = ""
    image_onnx_path: str = ""
    text_onnx_path: str = ""
    tokenizer_vocab_path: str = ""
    image_input_name: str = "image"
    image_output_name: str = "unnorm_image_features"
    text_input_name: str = "text"
    text_output_name: str = "unnorm_text_features"
    text_context_length: int = 52
    subtitle_model_path: str | None = None
    subtitle_share_text_encoder: bool = True
    # EmbeddingGemma 2 等可选模型：契约与前缀职责默认未核实，不得据此自动探测服务。
    encoder_contract_status: str = ""
    task_prefix_applied_by: str = ""
    preprocess_version: str = ""
    embed_http_health_path: str = "/health"
    embed_http_text_path: str = "/api/v1/embed/text"
    embed_http_texts_path: str = "/api/v1/embed/texts"
    embed_http_image_path: str = "/api/v1/embed/image"

    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        # 单元测试必须与现场 .env 隔离，避免误连真实 MinIO/Milvus/编码器。
        if os.environ.get("PICA_LOAD_DOTENV", "1").lower() in {"0", "false", "no"}:
            return init_settings, env_settings, file_secret_settings
        return init_settings, env_settings, dotenv_settings, file_secret_settings
