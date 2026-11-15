from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.application.lifecycle_service import lifecycle_flags
from app.application.search_aggregate import preview_locator
from app.domain.errors import AppError, NotFoundError
from app.domain.model_profile import ModelSpace, resolve_spec
from app.infra.model_space import space_from_settings
from app.domain.models import Asset, Job, ObjectEntry, Source, asset_source_url
from app.presentation.deps import Container
from app.presentation.helpers import (
    apply_asset_patch,
    cover_image_response,
    cover_locator_for,
    ingest_multipart_upload,
    media_range_response,
    parse_bool_param,
    preview_image_response,
)


router = APIRouter(prefix="/api")


def _c(request: Request) -> Container:
    return request.app.state.container


def source_out(s: Source) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "endpoint": s.endpoint,
        "bucket": s.bucket,
        "region": s.region,
        "role": s.role,
        "is_platform_storage": s.is_platform_storage,
        "access_key_configured": s.access_key_configured,
        "secret_configured": s.secret_configured,
        "last_probe_status": s.last_probe_status,
        "last_probe_message": s.last_probe_message,
        "deleted": bool(getattr(s, "deleted", False)),
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def asset_out(a: Asset, tags: list[str] | None = None) -> dict:
    labels = {
        "active": "有效",
        "pending_update": "待更新",
        "stale": "过期",
        "missing": "来源中未再发现（仍可查，已标失效）",
        "source_invalid": "来源已失效",
        "excluded": "已排除",
    }
    lifecycle = getattr(a, "lifecycle", "active")
    version = getattr(a, "version", 1)
    ready = getattr(a, "ready_version", 0)
    label = labels.get(lifecycle, lifecycle)
    if version > ready and lifecycle == "active":
        label = "待更新"
    flags = lifecycle_flags(a)
    return {
        "id": a.id,
        "source_id": a.source_id,
        "object_key": a.object_key,
        "kind": a.kind,
        "vectorize": a.vectorize,
        "status": a.status,
        "lifecycle": lifecycle,
        "lifecycle_label": label,
        "index_state": getattr(a, "index_state", "none"),
        "version": version,
        "ready_version": ready,
        "excluded": flags["excluded"],
        "source_deleted": bool(getattr(a, "source_deleted", False)),
        "vector_purge_status": flags["vector_purge_status"],
        "vector_purge_message": flags["vector_purge_message"],
        "artifact_purge_status": flags["artifact_purge_status"],
        "artifact_purge_message": flags["artifact_purge_message"],
        "fully_deleted": flags["fully_deleted"],
        "s3_original_untouched": flags["s3_original_untouched"],
        "can_restore": flags["can_restore"],
        "size": getattr(a, "size", None),
        "etag": getattr(a, "etag", None),
        "last_modified": getattr(a, "last_modified", None),
        "origin": getattr(a, "origin", "external"),
        "store_original": getattr(a, "origin", "external") == "hosted",
        "display_name": getattr(a, "display_name", None) or a.object_key.rsplit("/", 1)[-1],
        "description": getattr(a, "description", "") or "",
        "cover_key": getattr(a, "cover_key", None),
        "external_ref": getattr(a, "external_ref", None),
        "source_url": asset_source_url(a),
        "tags": list(tags or []),
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
    }


def job_out(j: Job) -> dict:
    return {
        "id": j.id,
        "job_type": j.job_type,
        "status": j.status,
        "source_id": j.source_id,
        "asset_id": j.asset_id,
        "stage": j.stage,
        "step": j.step,
        "progress": j.progress,
        "queue_position": j.queue_position,
        "error_code": j.error_code,
        "error_message": j.error_message,
        "checkpoint": j.checkpoint,
        "timings": (j.checkpoint or {}).get("timings") or {},
        "payload": j.payload,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "updated_at": j.updated_at.isoformat() if j.updated_at else None,
    }


class SourceIn(BaseModel):
    name: str
    endpoint: str = ""
    bucket: str = ""
    region: str = ""
    role: str = "input"
    is_platform_storage: bool = False
    access_key: str | None = None
    secret_key: str | None = None


class SourcePatch(BaseModel):
    name: str | None = None
    endpoint: str | None = None
    bucket: str | None = None
    region: str | None = None
    role: str | None = None
    is_platform_storage: bool | None = None
    access_key: str | None = None
    secret_key: str | None = None


class ImportItemIn(BaseModel):
    object_key: str
    vectorize: bool = False
    subtitle_object_key: str | None = None
    origin: str | None = None
    display_name: str | None = None


class ImportIn(BaseModel):
    source_id: str
    items: list[ImportItemIn] = Field(default_factory=list)


class SearchIn(BaseModel):
    query: str = ""
    has_image: bool = False
    top_n: int | None = None
    counting_mode: str | None = None
    fusion: str | None = None
    subtitle_agg: str | None = None
    score_threshold: float | None = None


class TagIn(BaseModel):
    tag: str


class AssetPatchIn(BaseModel):
    description: str | None = None
    display_name: str | None = None
    source_url: str | None = None
    tags: list[str] | None = None


class BatchAssetsIn(BaseModel):
    ids: list[str]
    action: str
    tag: str | None = None


class LexicalSearchIn(BaseModel):
    query: str
    source_id: str | None = None


class QueryPriorityIn(BaseModel):
    active: bool


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "pica"}


class AuthSetupIn(BaseModel):
    username: str
    password: str
    password_confirm: str | None = None


class AuthEnableIn(BaseModel):
    username: str
    password: str
    password_confirm: str | None = None
    signing_key_mode: str
    custom_signing_key: str | None = None
    confirm_customization: bool | None = None
    expire_seconds: int | None = None


class AuthLoginIn(BaseModel):
    username: str
    password: str


class AuthSigningKeyIn(BaseModel):
    signing_key_mode: str
    custom_signing_key: str | None = None
    confirm_customization: bool | None = None


@router.get("/auth/status")
def auth_status(request: Request) -> dict:
    return _c(request).auth.public_status().as_dict()


@router.post("/auth/setup")
def auth_setup(request: Request, body: AuthSetupIn) -> dict:
    return _c(request).auth.setup_user(body.model_dump())


@router.post("/auth/enable")
def auth_enable(request: Request, body: AuthEnableIn) -> dict:
    return _c(request).auth.enable(body.model_dump())


@router.post("/auth/disable")
def auth_disable(request: Request) -> dict:
    identity = getattr(request.state, "identity", None)
    return _c(request).auth.disable(identity)


@router.post("/auth/login")
def auth_login(request: Request, body: AuthLoginIn) -> dict:
    return _c(request).auth.login(body.username, body.password)


@router.post("/auth/signing-key")
def auth_signing_key(request: Request, body: AuthSigningKeyIn) -> dict:
    identity = getattr(request.state, "identity", None)
    return _c(request).auth.update_signing_key(body.model_dump(), identity)


class ApiTokenCreateIn(BaseModel):
    name: str | None = None


@router.get("/settings/api-tokens")
def list_api_tokens(request: Request) -> dict:
    tokens = _c(request).api_tokens
    if tokens is None:
        raise AppError("invalid", "API Token 服务尚未装配。")
    return {"items": tokens.list_tokens()}


@router.post("/settings/api-tokens", status_code=201)
def create_api_token(request: Request, body: ApiTokenCreateIn | None = None) -> dict:
    tokens = _c(request).api_tokens
    if tokens is None:
        raise AppError("invalid", "API Token 服务尚未装配。")
    return tokens.create((body.name if body else None))


@router.delete("/settings/api-tokens/{token_id}")
def revoke_api_token(request: Request, token_id: str) -> dict:
    tokens = _c(request).api_tokens
    if tokens is None:
        raise AppError("invalid", "API Token 服务尚未装配。")
    return {"ok": True, "item": tokens.revoke(token_id)}


@router.get("/auth/me")
def auth_me(request: Request) -> dict:
    identity = getattr(request.state, "identity", None)
    auth = _c(request).auth.public_status().as_dict()
    if identity is None:
        return {"identity": None, "auth": auth}
    return {
        "identity": {"id": identity.id, "username": identity.username, "roles": list(identity.roles)},
        "auth": auth,
    }


@router.get("/status")
def status(request: Request) -> dict:
    data = _c(request).status.snapshot()
    view = _c(request).app_settings.public_view()
    data["sample_interval_seconds"] = view["sample"]["interval_seconds"]
    data["ffmpeg"] = {"status": view["ffmpeg"]["status"], "message": view["ffmpeg"]["message"]}
    data["file_store"] = {"status": view["file_store"]["status"], "message": view["file_store"]["message"]}
    data["model"] = view.get("model")
    return data


@router.get("/sources")
def list_sources(request: Request) -> dict:
    return {"items": [source_out(s) for s in _c(request).sources.list_sources()]}


@router.post("/sources", status_code=201)
def create_source(request: Request, body: SourceIn) -> dict:
    source = _c(request).sources.create(
        name=body.name,
        endpoint=body.endpoint,
        bucket=body.bucket,
        region=body.region,
        role=body.role,
        is_platform_storage=body.is_platform_storage,
        access_key=body.access_key,
        secret_key=body.secret_key,
    )
    return source_out(source)


@router.get("/sources/{source_id}")
def get_source(request: Request, source_id: str) -> dict:
    return source_out(_c(request).sources.get(source_id))


@router.patch("/sources/{source_id}")
def patch_source(request: Request, source_id: str, body: SourcePatch) -> dict:
    return source_out(_c(request).sources.update(source_id, **body.model_dump(exclude_unset=True)))


@router.delete("/sources/{source_id}")
def delete_source(request: Request, source_id: str) -> dict:
    retained = len(_c(request).imports.list_assets(source_id))
    _c(request).sources.delete(source_id)
    return {
        "ok": True,
        "assets_retained": retained,
        "message": "来源配置已停用。登记记录已保留并标记为失效，仍可在目录中查找。未删除或修改对象存储中的原文件。",
    }


@router.post("/sources/{source_id}/probe")
def probe_source(request: Request, source_id: str) -> dict:
    return source_out(_c(request).sources.probe(source_id))


@router.get("/sources/{source_id}/browse")
def browse_source(request: Request, source_id: str, prefix: str = "") -> dict:
    entries: list[ObjectEntry] = _c(request).sources.browse(source_id, prefix)
    return {
        "prefix": prefix,
        "items": [
            {
                "key": e.key,
                "is_dir": e.is_dir,
                "size": e.size,
                "last_modified": e.last_modified,
            }
            for e in entries
        ],
    }


@router.post("/imports", status_code=201)
def create_import(request: Request, body: ImportIn) -> dict:
    assets, jobs = _c(request).imports.import_items(
        body.source_id, [item.model_dump(exclude_none=True) for item in body.items]
    )
    return {"assets": _assets_with_tags(request, assets), "jobs": [job_out(j) for j in jobs]}


@router.post("/uploads", status_code=201)
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    meta: str = Form("[]"),
    relative_paths: list[str] | None = Form(None),
    cover: UploadFile | None = File(None),
) -> dict:
    result = await ingest_multipart_upload(
        _c(request),
        files=files,
        meta=meta,
        relative_paths=relative_paths,
        cover=cover,
        plugin=False,
    )
    return {
        "batch_id": result["batch_id"],
        "prefix": result["prefix"],
        "origin": result["origin"],
        "stored": result["stored"],
        "message": result["message"],
        "assets": _assets_with_tags(request, result["assets"]),
        "jobs": [job_out(j) for j in result["jobs"]],
    }


@router.post("/assets/{asset_id}/vectorize", status_code=201)
def enqueue_vectorize(request: Request, asset_id: str) -> dict:
    job = _c(request).imports.enqueue_index(asset_id)
    asset = _c(request).imports._metadata.get_asset(asset_id)
    tags = _c(request).tags.list_for_asset(asset_id) if _c(request).tags and asset else []
    return {"asset": asset_out(asset, tags) if asset else None, "job": job_out(job)}


@router.get("/assets/{asset_id}/tags")
def list_tags(request: Request, asset_id: str) -> dict:
    if _c(request).tags is None:
        raise AppError("invalid", "标签服务尚未装配。")
    return {"asset_id": asset_id, "items": _c(request).tags.list_for_asset(asset_id)}


@router.post("/assets/{asset_id}/tags")
def add_tag(request: Request, asset_id: str, body: TagIn) -> dict:
    if _c(request).tags is None:
        raise AppError("invalid", "标签服务尚未装配。")
    return {"asset_id": asset_id, "items": _c(request).tags.add(asset_id, body.tag)}


@router.delete("/assets/{asset_id}/tags/{tag}")
def delete_tag(request: Request, asset_id: str, tag: str) -> dict:
    if _c(request).tags is None:
        raise AppError("invalid", "标签服务尚未装配。")
    return {"asset_id": asset_id, "items": _c(request).tags.remove(asset_id, tag)}


def _assets_with_tags(request: Request, assets: list[Asset]) -> list[dict]:
    tags_map: dict[str, list[str]] = {}
    tags = _c(request).tags
    if tags is not None:
        tags_map = tags.tags_by_assets([a.id for a in assets])
    return [asset_out(a, tags_map.get(a.id) or []) for a in assets]


def _cover_assets(request: Request, assets: list[Asset]) -> list[dict]:
    items = _assets_with_tags(request, assets)
    covers = {}
    getter = getattr(_c(request).imports._metadata, "preview_covers", None)
    if callable(getter):
        covers = getter([asset.id for asset in assets])
    for item, asset in zip(items, assets):
        idx = covers.get(asset.id)
        custom_cover = cover_locator_for(asset, "/api")
        if custom_cover:
            item["cover_index"] = idx
            item["cover_locator"] = custom_cover
            item["video_cover_locator"] = custom_cover
            item["cover_status"] = "ready"
        elif idx is not None:
            loc = preview_locator(asset.id, idx, "frame")
            item["cover_index"] = idx
            item["cover_locator"] = loc
            item["video_cover_locator"] = loc
            item["cover_status"] = "ready"
        else:
            pending = (
                bool(asset.vectorize)
                and not bool(getattr(asset, "excluded", False))
                and not bool(getattr(asset, "source_deleted", False))
                and getattr(asset, "index_state", "") != "ready"
            )
            item["cover_index"] = None
            item["cover_locator"] = None
            item["video_cover_locator"] = None
            item["cover_status"] = "pending" if pending else "none"
    return items


@router.get("/assets/object-keys")
def list_asset_object_keys(request: Request, source_id: str | None = None) -> dict:
    return {"items": _c(request).imports.list_object_keys(source_id)}


@router.get("/assets")
def list_assets(
    request: Request,
    source_id: str | None = None,
    kind: str | None = None,
    excluded: str | None = None,
    pending: str | None = None,
    indexed: str | None = None,
    q: str | None = None,
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    filters = {
        "kind": kind,
        "excluded": parse_bool_param(excluded),
        "pending": parse_bool_param(pending),
        "indexed": parse_bool_param(indexed),
        "query": q,
    }
    items = _c(request).imports.list_assets(source_id, limit=limit, offset=offset, **filters)
    total = _c(request).imports.count_assets(source_id, **filters)
    return {"items": _cover_assets(request, items), "total": total, "limit": limit, "offset": offset}


@router.post("/assets/batch")
def batch_assets(request: Request, body: BatchAssetsIn) -> dict:
    ids = [item for item in body.ids if item]
    if not ids:
        raise AppError("invalid", "请至少选择一条资料。")
    action = (body.action or "").strip()
    results: list[dict] = []
    if action == "tag":
        if _c(request).tags is None:
            raise AppError("invalid", "标签服务尚未装配。")
        tagged = _c(request).tags.add_many(ids, body.tag or "")
        for asset_id, tags in tagged.items():
            results.append({"id": asset_id, "tags": tags})
        return {"ok": True, "action": action, "items": results}
    if action == "exclude":
        for asset_id in ids:
            asset, job = _c(request).lifecycle.exclude_asset(asset_id)
            results.append({"id": asset_id, "asset": asset_out(asset), "job": job_out(job)})
        return {"ok": True, "action": action, "items": results}
    if action == "vectorize":
        for asset_id in ids:
            job = _c(request).imports.enqueue_index(asset_id)
            results.append({"id": asset_id, "job": job_out(job)})
        return {"ok": True, "action": action, "items": results}
    raise AppError("invalid", "不支持的批量操作。")


@router.get("/assets/{asset_id}")
def get_asset(request: Request, asset_id: str) -> dict:
    asset = _c(request).imports._metadata.get_asset(asset_id)
    if asset is None:
        raise NotFoundError("资源不存在")
    return _cover_assets(request, [asset])[0]


@router.patch("/assets/{asset_id}")
def patch_asset(request: Request, asset_id: str, body: AssetPatchIn) -> dict:
    asset, _ = apply_asset_patch(_c(request), asset_id, body.model_dump(exclude_unset=True))
    return _cover_assets(request, [asset])[0]


@router.get("/assets/{asset_id}/cover")
def asset_cover(request: Request, asset_id: str):
    return cover_image_response(_c(request), asset_id)


@router.post("/assets/{asset_id}/cover")
async def upload_asset_cover(request: Request, asset_id: str, cover: UploadFile = File(...)) -> dict:
    uploads = _c(request).uploads
    if uploads is None:
        raise AppError("invalid", "上传服务尚未装配。")
    data = await cover.read()
    asset = uploads.set_asset_cover(asset_id, data)
    return _cover_assets(request, [asset])[0]


@router.get("/assets/{asset_id}/media")
def asset_media(request: Request, asset_id: str):
    return media_range_response(_c(request), asset_id, request.headers.get("range"))


@router.get("/jobs")
def list_jobs(request: Request, status: str | None = None, stage: str | None = None) -> dict:
    jobs = _c(request).jobs.list_jobs(status)  # type: ignore[arg-type]
    if stage:
        from app.application.dashboard import dashboard_stage

        jobs = [j for j in jobs if dashboard_stage(j) == stage]
    return {"items": [job_out(j) for j in jobs]}


@router.get("/jobs/dashboard")
def jobs_dashboard(request: Request, stage: str | None = None) -> dict:
    dash = _c(request).dashboard
    if dash is None:
        raise AppError("invalid", "仪表盘尚未装配。")
    return dash.snapshot(stage=stage)


@router.get("/jobs/{job_id}")
def get_job(request: Request, job_id: str) -> dict:
    return job_out(_c(request).jobs.get(job_id))


@router.post("/jobs/{job_id}/pause")
def pause_job(request: Request, job_id: str) -> dict:
    return job_out(_c(request).jobs.pause(job_id))


@router.post("/jobs/{job_id}/resume")
def resume_job(request: Request, job_id: str) -> dict:
    return job_out(_c(request).jobs.resume(job_id))


@router.post("/jobs/{job_id}/retry")
def retry_job(request: Request, job_id: str) -> dict:
    return job_out(_c(request).jobs.retry(job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: str) -> dict:
    return job_out(_c(request).jobs.cancel(job_id))


@router.delete("/jobs/{job_id}")
def delete_job(request: Request, job_id: str) -> dict:
    _c(request).jobs.delete(job_id)
    return {"ok": True}


@router.post("/jobs/query-priority")
def set_query_priority(request: Request, body: QueryPriorityIn) -> dict:
    _c(request).jobs.set_query_priority(body.active)
    return {"query_priority": body.active}


def _metadata_search(request: Request, query: str, source_id: str | None = None) -> dict:
    lexical = _c(request).lexical
    if lexical is None:
        raise AppError("invalid", "普通搜索尚未装配。")
    return lexical.search(query, source_id)


@router.post("/search/lexical")
def search_lexical(request: Request, body: LexicalSearchIn) -> dict:
    return _metadata_search(request, body.query, body.source_id)


@router.get("/search/metadata")
def search_metadata_get(request: Request, q: str = Query(...), source_id: str | None = None) -> dict:
    return _metadata_search(request, q, source_id)


@router.post("/search/metadata")
def search_metadata_post(request: Request, body: LexicalSearchIn) -> dict:
    return _metadata_search(request, body.query, body.source_id)


def _search_overrides(body: SearchIn) -> dict:
    return {
        "top_n": body.top_n,
        "counting_mode": body.counting_mode,
        "fusion": body.fusion,
        "subtitle_agg": body.subtitle_agg,
        "score_threshold": body.score_threshold,
    }


@router.post("/search")
def search(request: Request, body: SearchIn) -> dict:
    return _c(request).search.search(body.query, has_image=body.has_image, overrides=_search_overrides(body))


@router.post("/search/text")
def search_text(request: Request, body: SearchIn) -> dict:
    return _c(request).search.search(body.query, has_image=False, overrides=_search_overrides(body))


@router.post("/search/image")
async def search_image(
    request: Request,
    query: str = "",
    top_n: int | None = None,
    image: UploadFile = File(...),
    score_threshold: float | None = Form(None),
) -> dict:
    data = await image.read()
    if not data:
        raise AppError("invalid", "图片查询需要非空图片文件。")
    overrides: dict = {}
    if top_n is not None:
        overrides["top_n"] = top_n
    if score_threshold is not None:
        overrides["score_threshold"] = score_threshold
    return _c(request).search.search(query, has_image=True, image_bytes=data, overrides=overrides or None)


@router.get("/previews/{asset_id}/{kind}/{unit_index}")
def authorized_preview(request: Request, asset_id: str, kind: str, unit_index: int):
    return preview_image_response(_c(request), asset_id, kind, unit_index)


class RefreshSettingsIn(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = None


class MergeSettingsIn(BaseModel):
    max_gap_seconds: float | None = None
    max_group_duration_seconds: float | None = None
    max_group_chars: int | None = None


class LifecycleSettingsIn(BaseModel):
    preview_cleanup_on_exclude: bool | None = None


class SettingsIn(BaseModel):
    sample: dict | None = None
    subtitle_merge: dict | None = None
    refresh: dict | None = None
    lifecycle: dict | None = None
    search: dict | None = None
    upload: dict | None = None
    max_upload_files: int | None = None
    mode: str | None = None
    interval_seconds: float | None = None
    uniform_total: int | None = None
    frames_per_minute: float | None = None
    black_max_mean: float | None = None
    white_min_mean: float | None = None
    blur_max_laplacian_var: float | None = None
    similar_max_mad: float | None = None
    preview_max_edge: int | None = None
    preview_quality: int | None = None
    model_input_size: int | None = None


class AssociationIn(BaseModel):
    video_asset_id: str
    subtitle_asset_id: str
    enqueue: bool = True


class PolicyIn(BaseModel):
    source_id: str
    prefix: str = ""
    vectorize: bool = False


class RefreshIn(BaseModel):
    source_id: str
    prefix: str = ""


class CatalogIn(BaseModel):
    query: str
    source_id: str | None = None


class SampleSettingsIn(BaseModel):
    mode: str | None = None
    interval_seconds: float | None = None
    uniform_total: int | None = None
    frames_per_minute: float | None = None
    black_max_mean: float | None = None
    white_min_mean: float | None = None
    blur_max_laplacian_var: float | None = None
    similar_max_mad: float | None = None
    preview_max_edge: int | None = None
    preview_quality: int | None = None
    model_input_size: int | None = None


@router.get("/settings")
def get_settings(request: Request) -> dict:
    return _c(request).app_settings.public_view()


@router.put("/settings")
def put_settings(request: Request, body: SettingsIn) -> dict:
    return _c(request).app_settings.update_from_payload(body.model_dump(exclude_none=True))

class RebuildIn(BaseModel):
    profile: str | None = None
    family: str | None = None
    model_id: str | None = None
    model_version: str | None = None
    dim: int | None = None
    preprocess_version: str | None = None
    execute: bool = False


def _space_from_payload(settings, body: RebuildIn | None = None) -> "ModelSpace":
    if body is not None and any([body.family, body.model_id, body.dim, body.preprocess_version, body.profile]):
        spec = resolve_spec(body.family or body.model_id)
        if spec is None and not (body.model_id and body.dim and body.preprocess_version):
            raise AppError("invalid", "重建目标模型空间不完整：需要 family 或完整的 model_id/dim/preprocess_version。")
        if spec is not None:
            return ModelSpace(
                profile=body.profile or settings.vector_profile or "default",
                family=spec.family,
                model_id=body.model_id or spec.default_model_id,
                model_version=body.model_version or spec.default_model_version,
                dim=int(body.dim if body.dim is not None else spec.native_dim),
                preprocess_version=body.preprocess_version or spec.preprocess_version,
                native_dim=spec.native_dim,
            )
        return ModelSpace(
            profile=body.profile or settings.vector_profile or "default",
            family=body.family or "unknown",
            model_id=body.model_id or "unset",
            model_version=body.model_version or "unset",
            dim=int(body.dim),
            preprocess_version=body.preprocess_version or "unspecified",
        )
    space = space_from_settings(settings)
    if space is None:
        raise AppError("invalid", "当前没有可识别的模型空间，无法生成重建计划。")
    return space


@router.get("/model-profiles")
def model_profiles(request: Request) -> dict:
    return _c(request).app_settings.public_view()["model"]


@router.get("/rebuild/plan")
def rebuild_plan(request: Request) -> dict:
    container = _c(request)
    if container.rebuild is None:
        raise AppError("rebuild_unavailable", "重建服务尚未装配。")
    space = space_from_settings(container.settings)
    if space is None:
        raise AppError("invalid", "当前没有可识别的模型空间，无法生成重建计划。")
    return container.rebuild.plan(space)


@router.post("/rebuild/queue")
def rebuild_queue(request: Request, body: RebuildIn | None = None) -> dict:
    container = _c(request)
    if container.rebuild is None:
        raise AppError("rebuild_unavailable", "重建服务尚未装配。")
    payload = body or RebuildIn()
    space = _space_from_payload(container.settings, payload)
    return container.rebuild.enqueue(space, execute=bool(payload.execute))



@router.get("/assets/{asset_id}/frames")
def list_frames(request: Request, asset_id: str) -> dict:
    items = _c(request).imports.list_frames(asset_id)
    # 签名只用于去重，接口不回传完整签名以免响应膨胀
    cleaned = []
    for item in items:
        extra = dict(item.get("extra") or {})
        if "signature" in extra:
            extra["signature_present"] = True
            extra.pop("signature", None)
        cleaned.append({**item, "extra": extra})
    return {"items": cleaned}


@router.post("/subtitles/associations", status_code=201)
def create_association(request: Request, body: AssociationIn) -> dict:
    assoc, job = _c(request).subtitles.associate(body.video_asset_id, body.subtitle_asset_id, enqueue=body.enqueue)
    return {"association": assoc, "job": job_out(job) if job else None}


@router.get("/subtitles/associations")
def list_associations(request: Request, video_asset_id: str | None = None) -> dict:
    return {"items": _c(request).subtitles.list_associations(video_asset_id)}


@router.get("/subtitles/associations/{assoc_id}")
def get_association(request: Request, assoc_id: str) -> dict:
    return _c(request).subtitles.get_detail(assoc_id)


@router.delete("/subtitles/associations/{assoc_id}")
def delete_association(request: Request, assoc_id: str) -> dict:
    _c(request).subtitles.delete_association(assoc_id)
    return {"ok": True}


@router.post("/refresh", status_code=201)
def refresh_source(request: Request, body: RefreshIn) -> dict:
    job = _c(request).refresh.request(body.source_id, prefix=body.prefix, reason="manual")
    return {"job": job_out(job), "deduplicated": job.step == "等待刷新扫描" or True, "message": "已提交刷新任务（同源同目录若已有未结束任务则复用，不会重叠执行）。"}


@router.get("/scans")
def list_scans(request: Request, source_id: str | None = None) -> dict:
    return {"items": _c(request).refresh._metadata.list_scans(source_id) if hasattr(_c(request).refresh, "_metadata") else []}


@router.get("/scans/{scan_id}")
def get_scan(request: Request, scan_id: str) -> dict:
    return _c(request).refresh._metadata.get_scan(scan_id)


@router.post("/policies")
def upsert_policy(request: Request, body: PolicyIn) -> dict:
    _c(request).imports._metadata.upsert_policy(body.source_id, body.prefix, body.vectorize)
    return {"ok": True, "source_id": body.source_id, "prefix": body.prefix, "vectorize": body.vectorize}


@router.get("/policies")
def list_policies(request: Request, source_id: str) -> dict:
    return {"items": _c(request).imports._metadata.list_policies(source_id)}


@router.post("/assets/{asset_id}/exclude")
def exclude_asset(request: Request, asset_id: str) -> dict:
    asset, job = _c(request).lifecycle.exclude_asset(asset_id)
    flags = lifecycle_flags(asset)
    return {"asset": asset_out(asset), "job": job_out(job), "fully_deleted": flags["fully_deleted"]}


@router.post("/assets/{asset_id}/restore")
def restore_asset(request: Request, asset_id: str) -> dict:
    asset = _c(request).lifecycle.restore_asset(asset_id)
    return {"asset": asset_out(asset)}


@router.get("/exclusions")
def list_exclusions(request: Request, source_id: str | None = None) -> dict:
    return {"items": _c(request).imports._metadata.list_exclusions(source_id)}


@router.post("/catalog")
def catalog_search(request: Request, body: CatalogIn) -> dict:
    lexical = _c(request).lexical
    if lexical is not None:
        data = lexical.search(body.query, body.source_id)
        data["kind"] = "registry"
        data["note"] = "这是文件名与人工标签的包含匹配，不是向量检索。"
        return data
    items = _c(request).lifecycle.catalog(body.query, body.source_id)
    return {
        "kind": "registry",
        "note": "这是已登记目录的关键字查找，不是向量检索，也不会返回假的相似度结果。",
        "items": items,
    }
