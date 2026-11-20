from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.application.auth_service import AuthService
from app.application.dashboard import DashboardService
from app.application.face_service import FaceService
from app.application.import_service import ImportService
from app.application.job_service import JobService
from app.application.lexical_search import LexicalSearchService
from app.application.media_service import MediaService
from app.application.tag_service import TagService
from app.application.upload_service import UploadService
from app.application.api_token_service import ApiTokenService
from app.application.pipeline_service import PipelineService
from app.application.search_service import SearchService
from app.application.settings_service import SettingsService
from app.application.lifecycle_service import LifecycleService
from app.application.refresh_service import RefreshService
from app.application.source_service import SourceService
from app.application.status_service import StatusService
from app.application.subtitle_service import SubtitleService
from app.domain.errors import AppError
from app.domain.face import FACE_DIM
from app.infra.cleaner import DefaultFrameCleaner
from app.infra.auth import DefaultAuthorizationPolicy
from app.infra.config import Settings
from app.infra.db import connect, init_db
from app.infra.face_insightface import FaceHttpRecognizer, InsightFaceLocalRecognizer
from app.infra.file_store import PlatformResolvingFileStore, S3FileStore, UnconfiguredFileStore
from app.infra.s3_reader import S3SourceReader
from app.infra.sampler import FFmpegFrameSampler
from app.infra.sqlite_repos import SqliteMetadataRepository, SqliteTaskRepository
from app.application.index_write_service import IndexWriteService
from app.infra.encoders import PathBlockedEncoder
from app.infra.milvus_lite import MilvusLiteIndex
from app.infra.milvus_rest import MilvusRestIndex
from app.infra.model_space import space_from_settings
from app.application.rebuild_service import RebuildService
from app.domain.model_profile import (
    CONTRACT_PENDING,
    FAMILY_EMBEDDINGGEMMA2,
    PREFIX_BY_UNVERIFIED,
    normalize_family,
    resolve_spec,
)
from app.infra.stubs import (
    UnconfiguredImageTextEncoder,
    UnconfiguredSubtitleEncoder,
    UnconfiguredVectorIndex,
)
from app.infra.worker import JobDispatcher
from app.presentation.api import router
from app.presentation.v1 import v1_router
from app.presentation.deps import Container
from app.presentation.security import AuthMiddleware

CORS_ORIGIN_REGEX = (
    r"https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    r"|chrome-extension://[a-p]{32}"
    r"|moz-extension://[0-9a-f-]+"
)


def _resolve_under_backend(path: Path) -> Path:
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parent.parent / path


def _milvus_token(settings: Settings) -> str | None:
    if (settings.milvus_user or "").strip() or (settings.milvus_password or "").strip():
        return f"{settings.milvus_user or ''}:{settings.milvus_password or ''}"
    token = (settings.milvus_token or "").strip()
    return token or None


def build_vector_index(settings: Settings, override=None):
    if override is not None:
        return override
    backend_kind = (settings.milvus_backend or "").strip().lower()
    uri = (settings.milvus_uri or "").strip()
    is_lite = backend_kind == "lite" or (
        bool(uri) and not uri.startswith(("http://", "https://")) and uri.endswith(".db")
    )
    space = space_from_settings(settings)
    family = normalize_family(settings.model_family)
    contract = (settings.encoder_contract_status or "").strip()
    auto_create = True
    if family == FAMILY_EMBEDDINGGEMMA2 and contract != "verified":
        auto_create = False
    if is_lite:
        lite_raw = Path(uri) if (uri and not uri.startswith(("http://", "https://"))) else settings.milvus_lite_path
        lite_path = _resolve_under_backend(lite_raw)
        return MilvusLiteIndex(
            lite_path,
            profile=settings.vector_profile or "default",
            model_id=settings.model_id or "unset",
            model_version=settings.model_version or "unset",
            dim=settings.model_dim,
            preprocess_version=(space.preprocess_version if space else (settings.preprocess_version or "")),
            auto_create=auto_create,
        )
    if not uri:
        return UnconfiguredVectorIndex()
    return MilvusRestIndex(
        uri,
        token=_milvus_token(settings),
        db_name=settings.milvus_db,
        profile=settings.vector_profile or "default",
        model_id=settings.model_id or "unset",
        model_version=settings.model_version or "unset",
        dim=settings.model_dim,
        preprocess_version=(space.preprocess_version if space else (settings.preprocess_version or "")),
        auto_create=auto_create,
    )


def build_face_recognizer(settings: Settings, enabled_getter, override=None):
    if override is not None:
        return override
    backend = (settings.face_backend or "local_insightface").strip().lower()
    http_url = (settings.face_http_base_url or "").strip()
    if backend == "remote_http" or (http_url and backend != "local_insightface"):
        return FaceHttpRecognizer(
            http_url,
            timeout=float(settings.face_http_timeout_seconds or 30.0),
            enabled_getter=enabled_getter,
        )
    return InsightFaceLocalRecognizer(
        model_name=settings.face_model or "buffalo_sc",
        cache_dir=_resolve_under_backend(settings.face_cache_dir),
        det_threshold=float(settings.face_det_threshold or 0.55),
        min_face_px=int(settings.face_min_size_px or 24),
        flip_tta=bool(getattr(settings, "face_flip_tta", True)),
        enabled_getter=enabled_getter,
    )


def build_face_index(settings: Settings, override=None):
    if override is not None:
        return override
    backend_kind = (settings.milvus_backend or "").strip().lower()
    uri = (settings.milvus_uri or "").strip()
    is_lite = backend_kind == "lite" or (
        bool(uri) and not uri.startswith(("http://", "https://")) and uri.endswith(".db")
    )
    model_name = (settings.face_model or "buffalo_sc").strip() or "buffalo_sc"
    if is_lite:
        lite_raw = Path(uri) if (uri and not uri.startswith(("http://", "https://"))) else settings.milvus_lite_path
        lite_path = _resolve_under_backend(lite_raw)
        return MilvusLiteIndex(
            lite_path,
            profile=settings.vector_profile or "default",
            model_id=f"face_{model_name}",
            model_version="v1",
            dim=FACE_DIM,
            preprocess_version="arcface_512d_v1",
            auto_create=True,
        )
    if not uri:
        return UnconfiguredVectorIndex()
    return MilvusRestIndex(
        uri,
        token=_milvus_token(settings),
        db_name=settings.milvus_db,
        profile=settings.vector_profile or "default",
        model_id=f"face_{model_name}",
        model_version="v1",
        dim=FACE_DIM,
        preprocess_version="arcface_512d_v1",
        auto_create=True,
    )


def _legacy_blocked_encoder(kind: str, settings: Settings, model_path: str | None):
    return PathBlockedEncoder(
        kind,
        model_path=model_path,
        model_id=settings.model_id,
        model_version=settings.model_version,
        dim=settings.model_dim,
    )


def build_encoders(settings: Settings, encoder=None, subtitle_encoder=None):
    # 测试可注入假编码器；运行时绝不用随机/哈希向量作为回退。
    # 远程 HTTP 与本地 ONNX 是可替换的独立适配器，禁止混入同一空间。
    if encoder is not None or subtitle_encoder is not None:
        enc = encoder or _legacy_blocked_encoder("image_text", settings, settings.model_path)
        sub = subtitle_encoder or _legacy_blocked_encoder(
            "subtitle", settings, settings.subtitle_model_path
        )
        return enc, sub

    from app.infra.chinese_clip import (
        ConfigBlockedEncoder,
        ChineseClipSubtitleEncoder,
        ChineseClipVisualEncoder,
        validate_spec_fields,
    )
    from app.infra.clip_http import ClipHttpSubtitleEncoder, ClipHttpVisualEncoder

    backend = (settings.encoder_backend or "").strip()
    http_url = (settings.clip_http_base_url or "").strip()
    family = (settings.model_family or "").strip()
    architecture = (settings.model_architecture or "").strip()
    image_onnx = (settings.image_onnx_path or settings.model_path or "").strip()
    text_onnx = (settings.text_onnx_path or "").strip()
    vocab = (settings.tokenizer_vocab_path or "").strip()
    local_paths = any([image_onnx, text_onnx, vocab])
    share_sub = bool(settings.subtitle_share_text_encoder)

    def _subtitle_for(visual, problems=None):
        if problems:
            if share_sub:
                return ConfigBlockedEncoder(problems, "subtitle")
            return ConfigBlockedEncoder(
                ["已关闭与 text 侧共用，且未提供独立的字幕模型适配（本模块不臆造第二种通用编码器）。"],
                "subtitle",
            )
        if share_sub:
            if isinstance(visual, ClipHttpVisualEncoder):
                return ClipHttpSubtitleEncoder(visual)
            return ChineseClipSubtitleEncoder(visual)
        return ConfigBlockedEncoder(
            ["已关闭与 text 侧共用，且未提供独立的字幕模型适配（本模块不臆造第二种通用编码器）。"],
            "subtitle",
        )

    if backend not in {"", "remote_http", "local_onnx"}:
        problems = [f"不支持的 encoder_backend '{backend}'：仅接受 remote_http 或 local_onnx。"]
        return ConfigBlockedEncoder(problems, "visual"), _subtitle_for(None, problems)

    if http_url and local_paths:
        problems = [
            "已同时配置远程 HTTP 编码器与本地 ONNX 路径。二者可能不在同一可比较空间，"
            "拒绝混合使用。请只保留一种实现。"
        ]
        return ConfigBlockedEncoder(problems, "visual"), _subtitle_for(None, problems)

    family_norm = normalize_family(family)
    if family_norm == FAMILY_EMBEDDINGGEMMA2:
        from app.infra.embeddinggemma_http import (
            EmbeddingGemmaHttpSubtitleEncoder,
            EmbeddingGemmaHttpVisualEncoder,
            GemmaHttpSignature,
        )
        if backend == "local_onnx":
            problems = ["EmbeddingGemma 2 当前只提供远程 HTTP 适配，不与本地 Chinese-CLIP ONNX 混用。"]
            return ConfigBlockedEncoder(problems, "visual"), _subtitle_for(None, problems)
        if not http_url:
            problems = ["EmbeddingGemma 2 需要 clip_http_base_url（模型服务地址，不写死在业务层）。"]
            return ConfigBlockedEncoder(problems, "visual"), _subtitle_for(None, problems)
        dim = int(settings.model_dim or 0)
        if dim != 768:
            problems = [
                f"EmbeddingGemma 2 仅接受原生维度 768，当前配置为 {dim or '未设置'}。拒绝与 512 空间混用。"
            ]
            return ConfigBlockedEncoder(problems, "visual"), _subtitle_for(None, problems)
        spec = resolve_spec(FAMILY_EMBEDDINGGEMMA2)
        contract = (settings.encoder_contract_status or "").strip() or CONTRACT_PENDING
        prefix_by = (settings.task_prefix_applied_by or "").strip() or (
            spec.task_prefix_applied_by if spec else PREFIX_BY_UNVERIFIED
        )
        visual = EmbeddingGemmaHttpVisualEncoder(
            http_url,
            expected_dim=768,
            expected_model=architecture or "google/embeddinggemma-2",
            timeout=float(settings.clip_http_timeout_seconds or 30.0),
            model_id=settings.model_id,
            model_version=settings.model_version,
            contract_status=contract,
            task_prefix_applied_by=prefix_by,
            signature=GemmaHttpSignature(
                health_path=settings.embed_http_health_path or "/health",
                text_path=settings.embed_http_text_path or "/api/v1/embed/text",
                texts_path=settings.embed_http_texts_path or "/api/v1/embed/texts",
                image_path=settings.embed_http_image_path or "/api/v1/embed/image",
                paths_verified=False,
            ),
            preprocess_version=(settings.preprocess_version or "").strip() or (spec.preprocess_version if spec else ""),
        )
        if share_sub:
            return visual, EmbeddingGemmaHttpSubtitleEncoder(visual)
        return visual, _subtitle_for(visual)

    if backend == "remote_http" or (http_url and backend != "local_onnx"):
        if not http_url:
            problems = ["encoder_backend=remote_http 但未配置 clip_http_base_url。"]
            return ConfigBlockedEncoder(problems, "visual"), _subtitle_for(None, problems)
        dim = int(settings.model_dim or 0)
        if dim <= 0:
            problems = ["远程编码器需要明确的 model_dim，不会擅自假设维度。"]
            return ConfigBlockedEncoder(problems, "visual"), _subtitle_for(None, problems)
        if dim == 768 or "gemma" in (settings.model_id or "").lower():
            problems = [
                "当前远程适配仍是已核对的 Chinese-CLIP HTTP 契约。768 / EmbeddingGemma 2 必须走独立适配器与 profile，"
                "请设置 model_family=embeddinggemma2。拒绝把 768 配置接到 512 契约上。"
            ]
            return ConfigBlockedEncoder(problems, "visual"), _subtitle_for(None, problems)
        visual = ClipHttpVisualEncoder(
            http_url,
            expected_dim=dim,
            expected_model=architecture or "ViT-B-16",
            expected_precision=(settings.model_version or "int8_dynamic"),
            timeout=float(settings.clip_http_timeout_seconds or 30.0),
            model_id=settings.model_id,
            model_version=settings.model_version,
        )
        return visual, _subtitle_for(visual)

    configured_any = any([family, architecture, local_paths, backend == "local_onnx"])
    if not configured_any:
        return (
            _legacy_blocked_encoder("image_text", settings, settings.model_path),
            _legacy_blocked_encoder("subtitle", settings, settings.subtitle_model_path),
        )

    spec, problems = validate_spec_fields(
        family=family,
        architecture=architecture,
        embed_dim=int(settings.model_dim or 0),
        image_onnx=image_onnx,
        text_onnx=text_onnx,
        vocab=vocab,
        image_input_name=(settings.image_input_name or "").strip(),
        image_output_name=(settings.image_output_name or "").strip(),
        text_input_name=(settings.text_input_name or "").strip(),
        text_output_name=(settings.text_output_name or "").strip(),
        context_length=int(settings.text_context_length or 0),
    )
    if problems or spec is None:
        return ConfigBlockedEncoder(problems, "visual"), _subtitle_for(None, problems)
    visual = ChineseClipVisualEncoder(spec)
    return visual, _subtitle_for(visual)



def build_file_store(metadata: SqliteMetadataRepository, override=None):

    if override is not None:
        return override
    return PlatformResolvingFileStore(metadata)


def build_container(
    settings: Settings | None = None,
    *,
    sqlite_path: Path | None = None,
    source_reader=None,
    encoder=None,
    index=None,
    subtitle_encoder=None,
    worker_enabled: bool | None = None,
    file_store=None,
    sampler=None,
    work_root: Path | None = None,
    face_recognizer=None,
    face_index=None,
) -> tuple[Container, JobDispatcher, object]:
    settings = settings or Settings()
    db_path = sqlite_path or _resolve_under_backend(settings.sqlite_path)
    work_dir = work_root or _resolve_under_backend(settings.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    init_db(conn)
    metadata = SqliteMetadataRepository(conn)
    tasks = SqliteTaskRepository(conn)
    reader = source_reader or S3SourceReader()
    enc, sub_enc = build_encoders(settings, encoder, subtitle_encoder)
    vec = build_vector_index(settings, index)
    store = build_file_store(metadata, file_store)
    frame_sampler = sampler or FFmpegFrameSampler(settings)
    sources = SourceService(metadata, reader)
    if (
        settings.auto_init_platform_storage
        and metadata.get_platform_storage() is None
        and settings.s3_endpoint
        and settings.s3_bucket
    ):
        try:
            sources.create(
                name="MinIO Platform Storage",
                endpoint=settings.s3_endpoint,
                bucket=settings.s3_bucket,
                region=settings.s3_region or "us-east-1",
                role="storage",
                is_platform_storage=True,
                access_key=settings.s3_access_key,
                secret_key=settings.s3_secret_key,
            )
        except Exception:
            pass
    imports = ImportService(metadata, tasks)
    tags = TagService(metadata)
    app_settings = SettingsService(metadata, settings, store)

    face_rec = build_face_recognizer(settings, lambda: app_settings.get_face().enabled, face_recognizer)
    face_vec = build_face_index(settings, face_index)
    faces = FaceService(metadata, store, face_rec, face_index=face_vec, settings_getter=app_settings.get_face)

    def sample_settings():
        return app_settings.get_sample()

    current_space = space_from_settings(settings)

    def preprocess_strategy():
        if current_space is not None:
            return current_space.preprocess_version
        spec = resolve_spec(settings.model_family)
        return spec.preprocess_version if spec else None

    pipeline = PipelineService(
        metadata=metadata,
        tasks=tasks,
        reader=reader,
        file_store=store,
        sampler=frame_sampler,
        cleaner=DefaultFrameCleaner(),
        settings_getter=sample_settings,
        work_root=work_dir,
        preprocess_getter=preprocess_strategy,
    )
    subtitles = SubtitleService(
        metadata=metadata,
        tasks=tasks,
        reader=reader,
        encoder=sub_enc,
        settings_getter=app_settings.get_merge,
        work_root=work_dir,
    )
    imports._subtitles = subtitles
    uploads = UploadService(
        metadata,
        store,
        imports,
        work_root=work_dir,
        max_files_getter=lambda: app_settings.get_upload()["max_upload_files"],
        tags=tags,
    )
    api_tokens = ApiTokenService(metadata)
    refresh = RefreshService(
        metadata=metadata,
        tasks=tasks,
        reader=reader,
        settings_getter=app_settings.get_refresh,
        settings_setter=app_settings.save_refresh,
    )
    writer = IndexWriteService(
        metadata,
        tasks,
        store,
        enc,
        vec,
        subtitle_encoder=sub_enc,
        profile=settings.vector_profile or "default",
        model_id=settings.model_id or "unset",
        model_version=settings.model_version or "unset",
        dim=settings.model_dim,
        preprocess_version=(current_space.preprocess_version if current_space else (settings.preprocess_version or "")),
        space=current_space,
        face_service=faces,
    )
    lifecycle = LifecycleService(
        metadata, tasks, vec, settings_getter=app_settings.get_lifecycle, file_store=store, face_service=faces
    )
    rebuild = RebuildService(
        metadata,
        tasks=tasks,
        writer=writer,
        file_store=store,
        source_reader=reader,
        sampler=frame_sampler,
        work_root=work_dir,
        current_space=current_space,
        encoder=enc,
        index=vec,
    )
    jobs = JobService(
        tasks,
        enc,
        vec,
        pipeline=pipeline,
        lease_seconds=settings.lease_seconds,
        poll_seconds=settings.worker_poll_seconds,
        subtitle_pipeline=subtitles,
        refresh_service=refresh,
        lifecycle_service=lifecycle,
        subtitle_encoder=sub_enc,
        index_writer=writer,
        rebuild_service=rebuild,
    )
    search = SearchService(enc, vec, jobs, metadata, store, app_settings.get_search, sub_enc, current_space=current_space)
    lexical = LexicalSearchService(metadata, lifecycle)
    dashboard = DashboardService(tasks, encoder=enc)
    media = MediaService(metadata, store, source_reader=reader)
    auth = AuthService(metadata, settings)
    policy = DefaultAuthorizationPolicy(auth.is_enabled)
    status = StatusService(
        settings,
        enc,
        vec,
        db_path,
        file_store=store,
        sample_interval=app_settings.get_sample().interval_seconds,
        subtitle_encoder=sub_enc,
        auth_service=auth,
        face_service=faces,
    )
    container = Container(
        settings=settings,
        sources=sources,
        imports=imports,
        jobs=jobs,
        search=search,
        status=status,
        app_settings=app_settings,
        subtitles=subtitles,
        refresh=refresh,
        lifecycle=lifecycle,
        auth=auth,
        policy=policy,
        rebuild=rebuild,
        tags=tags,
        uploads=uploads,
        lexical=lexical,
        dashboard=dashboard,
        media=media,
        api_tokens=api_tokens,
        faces=faces,
    )
    dispatcher = JobDispatcher(jobs, settings, work_root=work_dir)
    container_extras = {
        "conn": conn,
        "worker_enabled": settings.worker_enabled if worker_enabled is None else worker_enabled,
        "file_store": store,
        "work_root": work_dir,
    }
    return container, dispatcher, container_extras


def create_app(
    settings: Settings | None = None,
    *,
    sqlite_path: Path | None = None,
    source_reader=None,
    encoder=None,
    index=None,
    subtitle_encoder=None,
    worker_enabled: bool | None = None,
    file_store=None,
    sampler=None,
    work_root: Path | None = None,
    face_recognizer=None,
    face_index=None,
) -> FastAPI:
    settings = settings or Settings()
    container, dispatcher, extras = build_container(
        settings,
        sqlite_path=sqlite_path,
        source_reader=source_reader,
        encoder=encoder,
        index=index,
        subtitle_encoder=subtitle_encoder,
        worker_enabled=worker_enabled,
        file_store=file_store,
        sampler=sampler,
        work_root=work_root,
        face_recognizer=face_recognizer,
        face_index=face_index,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if extras["worker_enabled"]:
            dispatcher.start()
        try:
            yield
        finally:
            dispatcher.stop()
            extras["conn"].close()

    app = FastAPI(title="Pica API", version="0.5.0", lifespan=lifespan)
    app.state.container = container
    app.state.dispatcher = dispatcher
    app.add_middleware(AuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_origin_regex=CORS_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(v1_router)

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    return app


app = create_app()
