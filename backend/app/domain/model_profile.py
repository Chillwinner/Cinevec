from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# 预处理版本与向量空间绑定。旧的短边+中心裁剪不得被静默标为与新版本兼容。
PREPROCESS_LEGACY_CENTER_CROP = "legacy_center_crop_v0"
PREPROCESS_CLIP_OFFICIAL_SQUARE = "chinese_clip_official_square_v1"
PREPROCESS_FULL_FRAME = "full_frame_passthrough_v1"

FAMILY_CHINESE_CLIP = "chinese_clip"
FAMILY_EMBEDDINGGEMMA2 = "embeddinggemma2"

PREFIX_BY_UNVERIFIED = "unverified"
PREFIX_BY_CLIENT = "client"
PREFIX_BY_SERVER = "server"

CONTRACT_PENDING = "pending_verification"
CONTRACT_VERIFIED = "verified"

# Official EmbeddingGemma 2 text task prompts, from
# https://ai.google.dev/gemma/docs/embeddinggemma and the model card
# (ai.google.dev/gemma/docs/embeddinggemma/model_card_2, 2026-10).
# Images, video and audio must not receive these text prefixes.
EG2_SEARCH_QUERY_TEMPLATE = "task: search result | query: {query}"
EG2_DOCUMENT_TEMPLATE = "title: {title} | text: {content}"
EG2_DOCUMENT_TITLE_NONE = "none"
# 已核实的远端服务把 instruction 与 text 分开，并默认使用下列前缀（含末尾空格）。
EG2_SEARCH_QUERY_INSTRUCTION = "task: search result | query: "
EG2_DOCUMENT_INSTRUCTION = "title: {title} | text: "

TaskRole = Literal["query", "document"]
PrefixOwner = Literal["unverified", "client", "server"]


def format_embeddinggemma2_query(query: str) -> str:
    return EG2_SEARCH_QUERY_TEMPLATE.format(query=query)


def format_embeddinggemma2_document(content: str, title: str | None = None) -> str:
    resolved = title if title not in (None, "") else EG2_DOCUMENT_TITLE_NONE
    return EG2_DOCUMENT_TEMPLATE.format(title=resolved, content=content)


def embeddinggemma2_instruction(role: TaskRole, title: str | None = None) -> str:
    if role == "query":
        return EG2_SEARCH_QUERY_INSTRUCTION
    resolved = title if title not in (None, "") else EG2_DOCUMENT_TITLE_NONE
    return EG2_DOCUMENT_INSTRUCTION.format(title=resolved)


def prepare_embeddinggemma2_text(
    text: str,
    *,
    role: TaskRole,
    applied_by: str,
    title: str | None = None,
) -> dict[str, Any]:
    """Build the HTTP text payload so a prefix is applied exactly once.

    server: raw text + official instruction field (verified 8001 contract).
    client: official full string in text, instruction cleared to avoid double-add.
    unverified: raw text, instruction omitted from the payload.
    """
    owner = (applied_by or PREFIX_BY_UNVERIFIED).strip() or PREFIX_BY_UNVERIFIED
    if owner not in {PREFIX_BY_UNVERIFIED, PREFIX_BY_CLIENT, PREFIX_BY_SERVER}:
        raise ValueError(f"不支持的 task_prefix_applied_by: {owner}")
    if owner == PREFIX_BY_SERVER:
        instruction = embeddinggemma2_instruction(role, title=title)
        return {
            "text": text,
            "instruction": instruction,
            "prefix_applied": False,
            "applied_by": owner,
            "role": role,
            "template": instruction,
            "reason": "server_instruction_field",
        }
    if owner == PREFIX_BY_CLIENT:
        if role == "query":
            rendered = format_embeddinggemma2_query(text)
            template = EG2_SEARCH_QUERY_TEMPLATE
        else:
            rendered = format_embeddinggemma2_document(text, title=title)
            template = EG2_DOCUMENT_TEMPLATE
        return {
            "text": rendered,
            "instruction": "",
            "prefix_applied": True,
            "applied_by": owner,
            "role": role,
            "template": template,
            "reason": "client_applied_official_template_instruction_cleared",
        }
    return {
        "text": text,
        "instruction": None,
        "omit_instruction": True,
        "prefix_applied": False,
        "applied_by": owner,
        "role": role,
        "template": None,
        "reason": "prefix_not_applied_pending_contract",
    }


@dataclass(frozen=True)
class ModelSpace:
    """图文共用同一 profile。维度、模型修订与预处理版本共同隔离空间。"""

    profile: str
    family: str
    model_id: str
    model_version: str
    dim: int
    preprocess_version: str
    metric: str = "cosine"
    native_dim: int | None = None

    def key(self) -> tuple[str, str, str, int, str]:
        return (self.profile, self.model_id, self.model_version, int(self.dim), self.preprocess_version)

    def collection_parts(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "dim": int(self.dim),
            "preprocess_version": self.preprocess_version,
        }


@dataclass(frozen=True)
class ModelProfileSpec:
    catalog_id: str
    family: str
    label: str
    native_dim: int
    default_model_id: str
    default_model_version: str
    default_architecture: str
    preprocess_version: str
    preprocess_strategy: str
    contract_status: str
    task_prefix_applied_by: str
    supports_image: bool
    supports_text: bool
    notes: str
    http_paths_unverified: bool = False

    def space(self, *, profile: str, model_id: str | None = None, model_version: str | None = None, dim: int | None = None) -> ModelSpace:
        return ModelSpace(
            profile=profile or "default",
            family=self.family,
            model_id=model_id or self.default_model_id,
            model_version=model_version or self.default_model_version,
            dim=int(dim if dim is not None else self.native_dim),
            preprocess_version=self.preprocess_version,
            native_dim=self.native_dim,
        )


CHINESE_CLIP_SPEC = ModelProfileSpec(
    catalog_id="chinese_clip_vit_b_16",
    family=FAMILY_CHINESE_CLIP,
    label="Chinese-CLIP ViT-B/16",
    native_dim=512,
    default_model_id="chinese-clip-vit-b-16",
    default_model_version="int8_dynamic",
    default_architecture="ViT-B-16",
    preprocess_version=PREPROCESS_CLIP_OFFICIAL_SQUARE,
    preprocess_strategy=PREPROCESS_CLIP_OFFICIAL_SQUARE,
    # 既有 8001 服务契约此前已核对；不表示当前端口上仍是该模型，也不表示效果已验证。
    contract_status=CONTRACT_VERIFIED,
    task_prefix_applied_by=PREFIX_BY_SERVER,
    supports_image=True,
    supports_text=True,
    notes="本地官方预处理为方形 BICUBIC resize，无额外中心裁剪。远程服务优先送完整画面由服务端处理。已有中心裁剪索引属于 legacy_center_crop_v0，不得标为与官方方形版本兼容。",
    http_paths_unverified=False,
)

EMBEDDINGGEMMA2_SPEC = ModelProfileSpec(
    catalog_id="embeddinggemma2",
    family=FAMILY_EMBEDDINGGEMMA2,
    label="EmbeddingGemma 2（原生 768）",
    native_dim=768,
    default_model_id="embeddinggemma-2",
    default_model_version="unverified",
    default_architecture="embeddinggemma-2",
    preprocess_version=PREPROCESS_FULL_FRAME,
    preprocess_strategy=PREPROCESS_FULL_FRAME,
    contract_status=CONTRACT_PENDING,
    task_prefix_applied_by=PREFIX_BY_SERVER,
    supports_image=True,
    supports_text=True,
    notes=(
        "原生 768 维。图片完整传给远端，不在本地中心裁剪。"
        "文字任务前缀由服务端加，客户端不得重复添加。"
        "HTTP 路由默认仅沿用既有 API 形状；保存配置不等于健康检查通过。"
        "更换模型使用独立索引空间，保留业务数据与旧空间。"
    ),
    http_paths_unverified=True,
)

CATALOG: dict[str, ModelProfileSpec] = {
    CHINESE_CLIP_SPEC.catalog_id: CHINESE_CLIP_SPEC,
    CHINESE_CLIP_SPEC.family: CHINESE_CLIP_SPEC,
    EMBEDDINGGEMMA2_SPEC.catalog_id: EMBEDDINGGEMMA2_SPEC,
    EMBEDDINGGEMMA2_SPEC.family: EMBEDDINGGEMMA2_SPEC,
    "embeddinggemma-2": EMBEDDINGGEMMA2_SPEC,
    "embedding_gemma_2": EMBEDDINGGEMMA2_SPEC,
    "EmbeddingGemma2": EMBEDDINGGEMMA2_SPEC,
}


def normalize_family(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    spec = CATALOG.get(raw) or CATALOG.get(raw.lower())
    return spec.family if spec else raw.lower()


def resolve_spec(family_or_id: str | None) -> ModelProfileSpec | None:
    raw = (family_or_id or "").strip()
    if not raw:
        return None
    return CATALOG.get(raw) or CATALOG.get(raw.lower())


def spaces_compatible(left: ModelSpace, right: ModelSpace) -> bool:
    return left.key() == right.key() and left.family == right.family


def reject_dimension_mismatch(configured_dim: int, observed_dim: int, *, configured_family: str = "") -> str | None:
    if int(configured_dim) == int(observed_dim):
        return None
    family = f"{configured_family} " if configured_family else ""
    return (
        f"{family}配置维度 {int(configured_dim)} 与服务声明/返回维度 {int(observed_dim)} 不一致，"
        "拒绝混用，不会自动改用另一空间。"
    )


def apply_text_task_prefix(
    text: str,
    *,
    role: TaskRole,
    applied_by: str,
    family: str,
    title: str | None = None,
) -> dict[str, Any]:
    """Return the payload text and whether a prefix was applied.

    unverified / server: do not add a prefix (避免在职责未核实时双加).
    client + embeddinggemma2: apply official templates only.
    Images must never call this helper.
    """
    owner = (applied_by or PREFIX_BY_UNVERIFIED).strip() or PREFIX_BY_UNVERIFIED
    if owner not in {PREFIX_BY_UNVERIFIED, PREFIX_BY_CLIENT, PREFIX_BY_SERVER}:
        raise ValueError(f"不支持的 task_prefix_applied_by: {owner}")
    if owner != PREFIX_BY_CLIENT or normalize_family(family) != FAMILY_EMBEDDINGGEMMA2:
        return {
            "text": text,
            "prefix_applied": False,
            "applied_by": owner,
            "role": role,
            "template": None,
            "reason": "prefix_not_applied_pending_contract"
            if owner == PREFIX_BY_UNVERIFIED
            else "prefix_deferred_to_server",
        }
    if role == "query":
        rendered = format_embeddinggemma2_query(text)
        template = EG2_SEARCH_QUERY_TEMPLATE
    else:
        rendered = format_embeddinggemma2_document(text, title=title)
        template = EG2_DOCUMENT_TEMPLATE
    return {
        "text": rendered,
        "prefix_applied": True,
        "applied_by": owner,
        "role": role,
        "template": template,
        "reason": "client_applied_official_template",
    }


def catalog_public_view(
    *,
    active_family: str,
    active_space: ModelSpace | None,
    contract_override: str | None = None,
    prefix_override: str | None = None,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for spec in (CHINESE_CLIP_SPEC, EMBEDDINGGEMMA2_SPEC):
        if spec.catalog_id in seen:
            continue
        seen.add(spec.catalog_id)
        contract = contract_override if spec.family == normalize_family(active_family) and contract_override else spec.contract_status
        prefix = prefix_override if spec.family == normalize_family(active_family) and prefix_override else spec.task_prefix_applied_by
        active = bool(active_space and active_space.family == spec.family)
        rows.append(
            {
                "id": spec.catalog_id,
                "family": spec.family,
                "label": spec.label,
                "native_dim": spec.native_dim,
                "preprocess_version": spec.preprocess_version,
                "contract_status": contract,
                "task_prefix_applied_by": prefix,
                "active": active,
                "verified": contract == CONTRACT_VERIFIED,
                "http_paths_unverified": spec.http_paths_unverified,
                "notes": spec.notes,
                "save_is_not_health": True,
            }
        )
    return rows
