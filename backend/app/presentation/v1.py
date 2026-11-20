from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.application.plugin_progress import (
    batch_counts,
    plugin_asset_out,
    plugin_job_out,
)
from app.domain.errors import AppError, NotFoundError
from app.presentation.deps import Container
from app.presentation.helpers import (
    apply_asset_patch,
    cover_image_response,
    ingest_multipart_upload,
    media_range_response,
    parse_bool_param,
    preview_image_response,
)

v1_router = APIRouter(prefix="/api/v1")


def _c(request: Request) -> Container:
    return request.app.state.container


def _rewrite_v1_locators(obj: Any) -> Any:
    if isinstance(obj, str):
        if obj.startswith("/api/previews/"):
            return "/api/v1/previews/" + obj[len("/api/previews/"):]
        if obj.startswith("/api/assets/"):
            return "/api/v1/assets/" + obj[len("/api/assets/"):]
        if obj.startswith("/api/faces/"):
            return "/api/v1/faces/" + obj[len("/api/faces/"):]
        return obj
    if isinstance(obj, list):
        return [_rewrite_v1_locators(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _rewrite_v1_locators(val) for key, val in obj.items()}
    return obj


class V1AssetPatchIn(BaseModel):
    description: str | None = None
    display_name: str | None = None
    source_url: str | None = None
    tags: list[str] | None = None
    lifecycle: str | None = None


class V1TagIn(BaseModel):
    tag: str


class V1TextSearchIn(BaseModel):
    query: str
    top_n: int | None = None
    score_threshold: float | None = None


class V1MetadataSearchIn(BaseModel):
    query: str
    source_id: str | None = None


@v1_router.get("/health")
def v1_health() -> dict:
    return {"status": "ok", "service": "pica", "api": "v1"}


@v1_router.post("/imports", status_code=201)
async def v1_imports(
    request: Request,
    files: list[UploadFile] = File(...),
    meta: str = Form("[]"),
    batch_id: str | None = Form(None),
    relative_paths: list[str] | None = Form(None),
    cover: UploadFile | None = File(None),
) -> dict:
    result = await ingest_multipart_upload(
        _c(request),
        files=files,
        meta=meta,
        relative_paths=relative_paths,
        batch_id=batch_id,
        cover=cover,
        plugin=True,
    )
    assets = result["assets"]
    jobs = result["jobs"]
    by_id = {asset.id: asset for asset in assets}
    tags_svc = _c(request).tags
    asset_views = []
    for asset in assets:
        tags = tags_svc.list_for_asset(asset.id) if tags_svc else []
        asset_views.append(plugin_asset_out(asset, tags))
    job_views = [plugin_job_out(job, by_id.get(job.asset_id or "")) for job in jobs]
    return {
        "batch_id": result["batch_id"],
        "prefix": result.get("prefix"),
        "origin": result.get("origin"),
        "message": result.get("message"),
        "assets": asset_views,
        "jobs": job_views,
    }


@v1_router.get("/jobs/{job_id}")
def v1_job(request: Request, job_id: str) -> dict:
    job = _c(request).jobs.get(job_id)
    asset = _c(request).imports._metadata.get_asset(job.asset_id) if job.asset_id else None
    return plugin_job_out(job, asset)


@v1_router.get("/batches/{batch_id}")
def v1_batch(request: Request, batch_id: str) -> dict:
    tasks = _c(request).jobs._tasks
    finder = getattr(tasks, "list_jobs_by_batch", None)
    if not callable(finder):
        raise AppError("invalid", "任务仓储不支持按批次查询。")
    jobs = finder(batch_id)
    if not jobs:
        raise NotFoundError("没有这个批次。")
    metadata = _c(request).imports._metadata
    preferred: dict[str, object] = {}
    for job in jobs:
        aid = job.asset_id or job.id
        current = preferred.get(aid)
        if current is None or (getattr(current, "job_type", "") != "index_asset" and job.job_type == "index_asset"):
            preferred[aid] = job
    items = []
    for job in preferred.values():
        asset = metadata.get_asset(job.asset_id) if job.asset_id else None
        items.append(plugin_job_out(job, asset))
    return {"batch_id": batch_id, "items": items, "counts": batch_counts(items)}


@v1_router.get("/assets")
def v1_list_assets(
    request: Request,
    source_id: str | None = None,
    kind: str | None = None,
    excluded: str | None = None,
    pending: str | None = None,
    indexed: str | None = None,
    q: str | None = None,
    ref_id: str | None = None,
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    parsed_excluded = parse_bool_param(excluded)
    if parsed_excluded is None and excluded is None:
        parsed_excluded = False
    filters = {
        "kind": kind,
        "excluded": parsed_excluded,
        "pending": parse_bool_param(pending),
        "indexed": parse_bool_param(indexed),
        "query": q,
    }
    if ref_id:
        filters["ref_id"] = ref_id
    imports = _c(request).imports
    assets = imports.list_assets(source_id, limit=limit, offset=offset, **filters)
    total = imports.count_assets(source_id, **filters)
    tags_svc = _c(request).tags
    tags_map = tags_svc.tags_by_assets([a.id for a in assets]) if tags_svc else {}
    return {
        "items": [plugin_asset_out(a, tags_map.get(a.id) or []) for a in assets],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@v1_router.get("/assets/{asset_id}")
def v1_asset(request: Request, asset_id: str) -> dict:
    asset = _c(request).imports._metadata.get_asset(asset_id)
    if asset is None:
        raise NotFoundError("资源不存在")
    tags = _c(request).tags.list_for_asset(asset_id) if _c(request).tags else []
    return plugin_asset_out(asset, tags)


@v1_router.patch("/assets/{asset_id}")
def v1_patch_asset(request: Request, asset_id: str, body: V1AssetPatchIn) -> dict:
    asset, tags = apply_asset_patch(_c(request), asset_id, body.model_dump(exclude_unset=True))
    return plugin_asset_out(asset, tags)


@v1_router.get("/assets/{asset_id}/cover")
def v1_asset_cover(request: Request, asset_id: str):
    return cover_image_response(_c(request), asset_id)


@v1_router.post("/assets/{asset_id}/cover")
async def v1_upload_cover(request: Request, asset_id: str, cover: UploadFile = File(...)) -> dict:
    uploads = _c(request).uploads
    if uploads is None:
        raise AppError("invalid", "上传服务尚未装配。")
    data = await cover.read()
    asset = uploads.set_asset_cover(asset_id, data)
    tags = _c(request).tags.list_for_asset(asset_id) if _c(request).tags else []
    return plugin_asset_out(asset, tags)


@v1_router.get("/assets/{asset_id}/media")
def v1_asset_media(request: Request, asset_id: str):
    return media_range_response(_c(request), asset_id, request.headers.get("range"))


@v1_router.get("/previews/{asset_id}/{kind}/{unit_index}")
def v1_preview(request: Request, asset_id: str, kind: str, unit_index: int):
    return preview_image_response(_c(request), asset_id, kind, unit_index)


@v1_router.post("/assets/{asset_id}/tags")
def v1_add_tag(request: Request, asset_id: str, body: V1TagIn) -> dict:
    tags = _c(request).tags
    if tags is None:
        raise AppError("invalid", "标签服务尚未装配。")
    return {"asset_id": asset_id, "items": tags.add(asset_id, body.tag)}


@v1_router.delete("/assets/{asset_id}/tags/{tag}")
def v1_delete_tag(request: Request, asset_id: str, tag: str) -> dict:
    tags = _c(request).tags
    if tags is None:
        raise AppError("invalid", "标签服务尚未装配。")
    return {"asset_id": asset_id, "items": tags.remove(asset_id, tag)}


@v1_router.post("/assets/{asset_id}/exclude")
def v1_exclude_asset(request: Request, asset_id: str) -> dict:
    asset, job = _c(request).lifecycle.exclude_asset(asset_id)
    tags = _c(request).tags.list_for_asset(asset_id) if _c(request).tags else []
    return {"asset": plugin_asset_out(asset, tags), "job": plugin_job_out(job, asset)}


@v1_router.post("/assets/{asset_id}/restore")
def v1_restore_asset(request: Request, asset_id: str) -> dict:
    asset = _c(request).lifecycle.restore_asset(asset_id)
    tags = _c(request).tags.list_for_asset(asset_id) if _c(request).tags else []
    return {"asset": plugin_asset_out(asset, tags)}


@v1_router.post("/search/text")
def v1_search_text(request: Request, body: V1TextSearchIn) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if body.top_n is not None:
        overrides["top_n"] = body.top_n
    if body.score_threshold is not None:
        overrides["score_threshold"] = body.score_threshold
    return _rewrite_v1_locators(_c(request).search.search(body.query, has_image=False, overrides=overrides or None))


@v1_router.post("/search/image")
async def v1_search_image(
    request: Request,
    top_n: int | None = Form(None),
    score_threshold: float | None = Form(None),
    image: UploadFile = File(...),
) -> dict[str, Any]:
    data = await image.read()
    if not data:
        raise AppError("invalid", "图片查询需要非空图片文件。")
    overrides: dict[str, Any] = {}
    if top_n is not None:
        overrides["top_n"] = top_n
    if score_threshold is not None:
        overrides["score_threshold"] = score_threshold
    return _rewrite_v1_locators(
        _c(request).search.search("", has_image=True, image_bytes=data, overrides=overrides or None)
    )


def _v1_metadata_search(request: Request, query: str, source_id: str | None) -> dict[str, Any]:
    lexical = _c(request).lexical
    if lexical is None:
        raise AppError("invalid", "元数据搜索尚未装配。")
    return _rewrite_v1_locators(lexical.search(query, source_id))


@v1_router.get("/search/metadata")
def v1_search_metadata_get(request: Request, q: str = Query(...), source_id: str | None = None) -> dict[str, Any]:
    return _v1_metadata_search(request, q, source_id)


@v1_router.post("/search/metadata")
def v1_search_metadata_post(request: Request, body: V1MetadataSearchIn) -> dict[str, Any]:
    return _v1_metadata_search(request, body.query, body.source_id)


@v1_router.post("/search/face")
async def v1_search_face(
    request: Request,
    image: UploadFile = File(...),
    face_index: int | None = Form(None),
    top_n: int | None = Form(None),
    score_threshold: float | None = Form(None),
) -> dict[str, Any]:
    data = await image.read()
    if not data:
        raise AppError("invalid", "请上传非空人像图片。")
    svc = getattr(_c(request), "faces", None)
    if svc is None:
        raise AppError("face_unavailable", "人脸服务尚未装配。")
    res = svc.search_by_image(
        data,
        face_index=face_index,
        top_n=top_n if top_n is not None else 20,
        score_threshold=score_threshold,
    )
    return _rewrite_v1_locators(res)


@v1_router.get("/faces/{face_id}/crop")
def v1_face_crop(request: Request, face_id: str):
    svc = getattr(_c(request), "faces", None)
    if svc is None:
        raise AppError("face_unavailable", "人脸服务尚未装配。")
    data, media_type = svc.crop_bytes(face_id)
    return Response(content=data, media_type=media_type)


@v1_router.get("/people")
def v1_list_people(
    request: Request,
    include_hidden: bool = False,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    svc = getattr(_c(request), "faces", None)
    if svc is None:
        raise AppError("face_unavailable", "人脸服务尚未装配。")
    return _rewrite_v1_locators(svc.list_people(include_hidden=include_hidden, q=q, limit=limit, offset=offset))
