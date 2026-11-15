from __future__ import annotations

from app.domain.model_profile import (
    CHINESE_CLIP_SPEC,
    EMBEDDINGGEMMA2_SPEC,
    ModelSpace,
    normalize_family,
    resolve_spec,
)
from app.infra.config import Settings
from app.infra.milvus_rest import collection_name


def space_from_settings(settings: Settings) -> ModelSpace | None:
    family = normalize_family(settings.model_family)
    spec = resolve_spec(family) or resolve_spec(settings.model_id)
    if spec is None:
        mid = (settings.model_id or "").lower()
        if "gemma" in mid or int(settings.model_dim or 0) == 768:
            spec = EMBEDDINGGEMMA2_SPEC
        elif "chinese" in mid or "clip" in mid or int(settings.model_dim or 0) == 512:
            spec = CHINESE_CLIP_SPEC
        else:
            if not (settings.model_id and settings.model_dim):
                return None
            return ModelSpace(
                profile=settings.vector_profile or "default",
                family=family or "unknown",
                model_id=settings.model_id,
                model_version=settings.model_version or "unset",
                dim=int(settings.model_dim),
                preprocess_version=(settings.preprocess_version or "").strip() or "unspecified",
            )
    preprocess = (settings.preprocess_version or "").strip() or spec.preprocess_version
    dim = int(settings.model_dim or spec.native_dim)
    return spec.space(
        profile=settings.vector_profile or "default",
        model_id=settings.model_id,
        model_version=settings.model_version,
        dim=dim,
    ).__class__(
        profile=settings.vector_profile or "default",
        family=spec.family,
        model_id=settings.model_id or spec.default_model_id,
        model_version=settings.model_version or spec.default_model_version,
        dim=dim,
        preprocess_version=preprocess,
        native_dim=spec.native_dim,
    )


def collection_for(space: ModelSpace, modality: str) -> str:
    return collection_name(
        space.profile,
        space.model_id,
        space.model_version,
        space.dim,
        modality,
        preprocess_version=space.preprocess_version,
    )
