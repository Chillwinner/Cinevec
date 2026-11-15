"""Chinese-CLIP official split-ONNX adapter (CPU, family-specific).

Only the documented OFA-Sys Chinese-CLIP family is supported. This module
does not invent a generic ONNX adapter, does not download model bodies or
vocab, and never falls back to random or hash embeddings.

Official sources (OFA-Sys/Chinese-CLIP):
- deployment.md: split ``{prefix}.txt.{fp32|fp16}.onnx`` / ``{prefix}.img.{fp32|fp16}.onnx``,
  batch size 1, inputs ``text`` / ``image``, outputs ``unnorm_text_features`` /
  ``unnorm_image_features``, then L2-normalize.
- utils.image_transform: Resize((res, res), BICUBIC) → RGB → [0, 1] → CLIP mean/std.
  The official function does not apply a separate CenterCrop (it resizes to a square).
- utils.tokenize: ``[CLS]`` + WordPiece ids truncated to context_length-2 + ``[SEP]``,
  zero-padded to context_length (default 52).
- bert_tokenizer.FullTokenizer(vocab_file, do_lower_case=True) (Google BERT WordPiece,
  official vocab is the Chinese BERT ``vocab.txt``; a local path must be provided).
"""

from __future__ import annotations

import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from app.domain.errors import NotConfiguredError

FAMILY = "chinese_clip"

# Official input / output names from OFA-Sys deployment.md
DEFAULT_IMAGE_INPUT = "image"
DEFAULT_IMAGE_OUTPUT = "unnorm_image_features"
DEFAULT_TEXT_INPUT = "text"
DEFAULT_TEXT_OUTPUT = "unnorm_text_features"
DEFAULT_CONTEXT_LENGTH = 52

CLIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
CLIP_STD = (0.26862954, 0.26130258, 0.27577711)

# Official _MODEL_INFO plus standard CLIP embedding widths for each architecture.
# input_resolution comes from cn_clip/clip/utils.py; embed_dim is the official
# feature width of that architecture (ViT-B-16=512, ViT-L-14(+336)=768, ViT-H-14=1024, RN50=1024).
OFFICIAL_PROFILES: dict[str, dict] = {
    "ViT-B-16": {"input_resolution": 224, "embed_dim": 512},
    "ViT-L-14": {"input_resolution": 224, "embed_dim": 768},
    "ViT-L-14-336": {"input_resolution": 336, "embed_dim": 768},
    "ViT-H-14": {"input_resolution": 224, "embed_dim": 1024},
    "RN50": {"input_resolution": 224, "embed_dim": 1024},
}


@dataclass(frozen=True)
class ChineseClipSpec:
    """Explicit, family-specific load specification. No generic guessed adapter."""

    family: str
    architecture: str
    embed_dim: int
    input_resolution: int
    image_onnx_path: Path
    text_onnx_path: Path
    vocab_path: Path
    image_input_name: str = DEFAULT_IMAGE_INPUT
    image_output_name: str = DEFAULT_IMAGE_OUTPUT
    text_input_name: str = DEFAULT_TEXT_INPUT
    text_output_name: str = DEFAULT_TEXT_OUTPUT
    context_length: int = DEFAULT_CONTEXT_LENGTH
    model_id: str = ""
    model_version: str = ""

    @property
    def cache_key(self) -> str:
        return "|".join(
            [
                self.family,
                self.architecture,
                str(self.embed_dim),
                str(self.image_onnx_path.resolve()),
                str(self.text_onnx_path.resolve()),
                str(self.vocab_path.resolve()),
                self.image_input_name,
                self.image_output_name,
                self.text_input_name,
                self.text_output_name,
                str(self.context_length),
            ]
        )


def validate_spec_fields(
    *,
    family: str,
    architecture: str,
    embed_dim: int,
    image_onnx: str,
    text_onnx: str,
    vocab: str,
    image_input_name: str,
    image_output_name: str,
    text_input_name: str,
    text_output_name: str,
    context_length: int,
    allow_unofficial_profile: bool = False,
    unofficial_resolution: int | None = None,
) -> tuple[ChineseClipSpec | None, list[str]]:
    """Validate configuration without loading weights. Returns (spec, problems)."""
    problems: list[str] = []
    fam = (family or "").strip()
    arch = (architecture or "").strip()
    if fam and fam != FAMILY:
        problems.append(
            f"不支持的 model family '{fam}'：本适配仅支持明确声明的 {FAMILY}，"
            "不会臆造通用 ONNX 适配。"
        )
    elif not fam:
        problems.append("未配置 model family（需要 model_family=chinese_clip）。")

    profile = OFFICIAL_PROFILES.get(arch) if arch else None
    resolution = 0
    expected_dim = 0
    if allow_unofficial_profile and unofficial_resolution:
        # Reserved for synthetic unit tests; never used as a production fallback.
        resolution = unofficial_resolution
        expected_dim = embed_dim
        if not arch:
            problems.append("测试用 unofficial profile 仍需提供 architecture 名称。")
    elif not arch:
        problems.append(
            "未配置模型结构 architecture（例如 ViT-B-16；须为官方 Chinese-CLIP 已公布结构）。"
        )
    elif profile is None:
        problems.append(
            f"不支持的 architecture '{arch}'：仅接受官方结构 "
            f"{', '.join(OFFICIAL_PROFILES)}，拒绝未知 checkpoint。"
        )
    else:
        resolution = int(profile["input_resolution"])
        expected_dim = int(profile["embed_dim"])
        if embed_dim and embed_dim != expected_dim:
            problems.append(
                f"配置的 model_dim={embed_dim} 与官方 {arch} 特征维 {expected_dim} 不一致。"
            )
        if not embed_dim:
            embed_dim = expected_dim

    if context_length <= 2:
        problems.append(f"context_length 须大于 2（官方默认 {DEFAULT_CONTEXT_LENGTH}）。")

    def _need(path: str, label: str) -> Path:
        raw = (path or "").strip()
        if not raw:
            problems.append(f"未配置{label}本地路径（不会下载模型本体）。")
            return Path("")
        return Path(raw)

    image_path = _need(image_onnx, "图像 ONNX（官方 *.img.fp32.onnx 或 *.img.fp16.onnx）")
    text_path = _need(text_onnx, "文本 ONNX（官方 *.txt.fp32.onnx 或 *.txt.fp16.onnx）")
    vocab_path = _need(vocab, "中文 BERT WordPiece 词表 vocab.txt（官方 cn_clip/clip/vocab.txt）")

    for p, label in (
        (image_path, "图像 ONNX"),
        (text_path, "文本 ONNX"),
        (vocab_path, "tokenizer 词表"),
    ):
        if str(p) and not p.is_file():
            problems.append(f"{label}文件不存在：{p}")

    if not image_input_name or not image_output_name or not text_input_name or not text_output_name:
        problems.append("输入输出签名不得为空：须明确声明 image/text 的 input 与 output 名称。")

    if problems:
        return None, problems

    return (
        ChineseClipSpec(
            family=FAMILY,
            architecture=arch,
            embed_dim=embed_dim or expected_dim,
            input_resolution=resolution,
            image_onnx_path=image_path,
            text_onnx_path=text_path,
            vocab_path=vocab_path,
            image_input_name=image_input_name,
            image_output_name=image_output_name,
            text_input_name=text_input_name,
            text_output_name=text_output_name,
            context_length=context_length,
        ),
        [],
    )


# ---------------------------------------------------------------------------
# Official image preprocess (utils.image_transform) without torch/torchvision
# ---------------------------------------------------------------------------

def preprocess_image(image_bytes: bytes, resolution: int) -> np.ndarray:
    """Resize to (res, res) BICUBIC, RGB, [0,1], CLIP normalize, NCHW float32.

    Matches official ``image_transform`` (Resize to a square; no extra CenterCrop).
    """
    with Image.open(_BytesIO(image_bytes)) as im:
        im = im.convert("RGB")
        # Official torchvision Resize((N, N), BICUBIC) stretches to a square.
        im = im.resize((resolution, resolution), Image.Resampling.BICUBIC)
        arr = np.asarray(im, dtype=np.float32) / 255.0
    mean = np.array(CLIP_MEAN, dtype=np.float32).reshape(1, 1, 3)
    std = np.array(CLIP_STD, dtype=np.float32).reshape(1, 1, 3)
    arr = (arr - mean) / std
    chw = np.transpose(arr, (2, 0, 1))
    return np.expand_dims(chw, 0).astype(np.float32, copy=False)


def _BytesIO(data: bytes):
    from io import BytesIO
    return BytesIO(data)


# ---------------------------------------------------------------------------
# Official BERT WordPiece FullTokenizer (do_lower_case=True) + clip.tokenize wrap
# ---------------------------------------------------------------------------

def _load_vocab(vocab_file: Path) -> dict[str, int]:
    vocab: dict[str, int] = {}
    with vocab_file.open("r", encoding="utf-8") as fh:
        for token in fh:
            token = token.rstrip("\n")
            if token not in vocab:
                vocab[token] = len(vocab)
    return vocab


def _is_whitespace(ch: str) -> bool:
    if ch in (" ", "\t", "\n", "\r"):
        return True
    return unicodedata.category(ch) == "Zs"


def _is_control(ch: str) -> bool:
    if ch in ("\t", "\n", "\r"):
        return False
    return unicodedata.category(ch) in ("Cc", "Cf")


def _is_punctuation(ch: str) -> bool:
    cp = ord(ch)
    if (33 <= cp <= 47) or (58 <= cp <= 64) or (91 <= cp <= 96) or (123 <= cp <= 126):
        return True
    return unicodedata.category(ch).startswith("P")


def _is_chinese_char(cp: int) -> bool:
    return (
        (0x4E00 <= cp <= 0x9FFF)
        or (0x3400 <= cp <= 0x4DBF)
        or (0x20000 <= cp <= 0x2A6DF)
        or (0x2A700 <= cp <= 0x2B73F)
        or (0x2B740 <= cp <= 0x2B81F)
        or (0x2B820 <= cp <= 0x2CEAF)
        or (0xF900 <= cp <= 0xFAFF)
        or (0x2F800 <= cp <= 0x2FA1F)
    )


class BertWordPieceTokenizer:
    """Google BERT FullTokenizer compatible with official Chinese-CLIP (do_lower_case=True)."""

    def __init__(self, vocab_file: Path, do_lower_case: bool = True) -> None:
        if not vocab_file.is_file():
            raise FileNotFoundError(str(vocab_file))
        self.vocab = _load_vocab(vocab_file)
        self.do_lower_case = do_lower_case
        if "[UNK]" not in self.vocab or "[CLS]" not in self.vocab or "[SEP]" not in self.vocab:
            raise ValueError("词表缺少 [UNK]/[CLS]/[SEP]，不是可用的 BERT WordPiece 词表。")

    def tokenize(self, text: str) -> list[str]:
        split_tokens: list[str] = []
        for token in self._basic_tokenize(text):
            split_tokens.extend(self._wordpiece(token))
        return split_tokens

    def convert_tokens_to_ids(self, tokens: list[str]) -> list[int]:
        unk = self.vocab["[UNK]"]
        return [self.vocab.get(t, unk) for t in tokens]

    def _basic_tokenize(self, text: str) -> list[str]:
        text = self._clean(text)
        text = self._tokenize_chinese_chars(text)
        orig = text.strip().split()
        output: list[str] = []
        for token in orig:
            if self.do_lower_case:
                token = token.lower()
                token = self._run_strip_accents(token)
            output.extend(self._run_split_on_punc(token))
        return " ".join(output).split()

    def _clean(self, text: str) -> str:
        out: list[str] = []
        for ch in text:
            cp = ord(ch)
            if cp == 0 or cp == 0xFFFD or _is_control(ch):
                continue
            out.append(" " if _is_whitespace(ch) else ch)
        return "".join(out)

    def _tokenize_chinese_chars(self, text: str) -> str:
        out: list[str] = []
        for ch in text:
            if _is_chinese_char(ord(ch)):
                out.extend((" ", ch, " "))
            else:
                out.append(ch)
        return "".join(out)

    def _run_strip_accents(self, text: str) -> str:
        text = unicodedata.normalize("NFD", text)
        return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")

    def _run_split_on_punc(self, text: str) -> list[str]:
        chars = list(text)
        start_new = True
        output: list[list[str]] = []
        for ch in chars:
            if _is_punctuation(ch):
                output.append([ch])
                start_new = True
            else:
                if start_new:
                    output.append([])
                start_new = False
                output[-1].append(ch)
        return ["".join(x) for x in output]

    def _wordpiece(self, token: str) -> list[str]:
        unk = "[UNK]"
        if len(token) > 200:
            return [unk]
        chars = list(token)
        start = 0
        sub_tokens: list[str] = []
        while start < len(chars):
            end = len(chars)
            cur = None
            while start < end:
                substr = "".join(chars[start:end])
                if start > 0:
                    substr = "##" + substr
                if substr in self.vocab:
                    cur = substr
                    break
                end -= 1
            if cur is None:
                return [unk]
            sub_tokens.append(cur)
            start = end
        return sub_tokens


def tokenize_ids(tokenizer: BertWordPieceTokenizer, text: str, context_length: int) -> np.ndarray:
    """Official utils.tokenize wrapping: [CLS] + ids[:ctx-2] + [SEP], pad zeros, shape (1, ctx)."""
    ids = (
        [tokenizer.vocab["[CLS]"]]
        + tokenizer.convert_tokens_to_ids(tokenizer.tokenize(text))[: context_length - 2]
        + [tokenizer.vocab["[SEP]"]]
    )
    if len(ids) > context_length:
        ids = ids[:context_length]
    out = np.zeros((1, context_length), dtype=np.int64)
    out[0, : len(ids)] = np.asarray(ids, dtype=np.int64)
    return out


def l2_normalize(vec: np.ndarray) -> list[float]:
    flat = np.asarray(vec, dtype=np.float64).reshape(-1)
    norm = float(np.linalg.norm(flat))
    if norm == 0.0:
        raise NotConfiguredError(
            "编码器输出了零向量，已阻断，不会用随机或哈希向量充数。",
            code="blocked:encoder_zero_vector",
        )
    return (flat / norm).astype(float).tolist()


# ---------------------------------------------------------------------------
# Process-level singleton (shared by visual + subtitle ports, loaded on demand)
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
_RUNTIMES: dict[str, "ChineseClipRuntime"] = {}


def reset_runtime_cache() -> None:
    """Test helper: drop cached sessions so a new spec is loaded fresh."""
    with _LOCK:
        _RUNTIMES.clear()


class ChineseClipRuntime:
    def __init__(self, spec: ChineseClipSpec) -> None:
        self.spec = spec
        self.tokenizer = BertWordPieceTokenizer(spec.vocab_path, do_lower_case=True)
        self._image_session = None
        self._text_session = None
        self._load_error: str | None = None
        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - depends on optional wheel
            self._load_error = (
                f"onnxruntime 不可用（{exc}）。未改动系统 Python；"
                "请在本项目 backend/.venv 中安装兼容当前解释器的可选依赖后再加载。"
            )
            return
        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        providers = ["CPUExecutionProvider"]
        try:
            self._image_session = ort.InferenceSession(
                str(spec.image_onnx_path), sess_options=sess_opts, providers=providers
            )
            self._text_session = ort.InferenceSession(
                str(spec.text_onnx_path), sess_options=sess_opts, providers=providers
            )
        except Exception as exc:  # noqa: BLE001 — surface any load/checkpoint error as blocked
            self._load_error = f"ONNX 加载失败（checkpoint 未接受）：{type(exc).__name__}: {exc}"
            self._image_session = None
            self._text_session = None
            return
        self._load_error = _signature_mismatch(self._image_session, self._text_session, spec)

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def encode_image(self, image_bytes: bytes) -> list[float]:
        self._require_ready()
        arr = preprocess_image(image_bytes, self.spec.input_resolution)
        outputs = self._image_session.run(
            [self.spec.image_output_name], {self.spec.image_input_name: arr}
        )
        return l2_normalize(outputs[0])

    def encode_text(self, text: str) -> list[float]:
        self._require_ready()
        ids = tokenize_ids(self.tokenizer, text, self.spec.context_length)
        outputs = self._text_session.run(
            [self.spec.text_output_name], {self.spec.text_input_name: ids}
        )
        return l2_normalize(outputs[0])

    def _require_ready(self) -> None:
        if self._load_error or self._image_session is None or self._text_session is None:
            raise NotConfiguredError(
                self._load_error or "编码器运行时未就绪。",
                code="blocked:encoder_not_ready",
            )


def _signature_mismatch(image_sess, text_sess, spec: ChineseClipSpec) -> str | None:
    img_in = {i.name for i in image_sess.get_inputs()}
    img_out = {o.name for o in image_sess.get_outputs()}
    txt_in = {i.name for i in text_sess.get_inputs()}
    txt_out = {o.name for o in text_sess.get_outputs()}
    problems: list[str] = []
    if spec.image_input_name not in img_in:
        problems.append(
            f"图像 ONNX 输入不包含声明的 '{spec.image_input_name}'（实际: {sorted(img_in)}）"
        )
    if spec.image_output_name not in img_out:
        problems.append(
            f"图像 ONNX 输出不包含声明的 '{spec.image_output_name}'（实际: {sorted(img_out)}）"
        )
    if spec.text_input_name not in txt_in:
        problems.append(
            f"文本 ONNX 输入不包含声明的 '{spec.text_input_name}'（实际: {sorted(txt_in)}）"
        )
    if spec.text_output_name not in txt_out:
        problems.append(
            f"文本 ONNX 输出不包含声明的 '{spec.text_output_name}'（实际: {sorted(txt_out)}）"
        )
    if problems:
        return "不支持的 checkpoint 签名：" + "；".join(problems)
    return None


def get_runtime(spec: ChineseClipSpec) -> ChineseClipRuntime:
    key = spec.cache_key
    with _LOCK:
        runtime = _RUNTIMES.get(key)
        if runtime is None:
            runtime = ChineseClipRuntime(spec)
            _RUNTIMES[key] = runtime
        return runtime


class ChineseClipVisualEncoder:
    """Image-text (visual) port. Shares the process-level runtime with the subtitle port."""

    def __init__(self, spec: ChineseClipSpec, problems: list[str] | None = None) -> None:
        self.spec = spec
        self._problems = list(problems or [])
        self._runtime: ChineseClipRuntime | None = None

    def _ensure(self) -> ChineseClipRuntime:
        if self._problems:
            raise NotConfiguredError("；".join(self._problems), code="blocked:encoder_config")
        if self._runtime is None:
            self._runtime = get_runtime(self.spec)
        if self._runtime.load_error:
            raise NotConfiguredError(self._runtime.load_error, code="blocked:encoder_load")
        return self._runtime

    def available(self) -> bool:
        if self._problems:
            return False
        try:
            runtime = self._ensure()
        except NotConfiguredError:
            return False
        return runtime.load_error is None

    def status_message(self) -> str:
        return str(self.public_status().get("message") or "encoder 未配置")

    def public_status(self) -> dict:
        ready = False
        reason = "；".join(self._problems) if self._problems else ""
        if not self._problems:
            runtime = get_runtime(self.spec)
            self._runtime = runtime
            if runtime.load_error:
                reason = runtime.load_error
            else:
                ready = True
                reason = (
                    f"Chinese-CLIP {self.spec.architecture} CPU ONNX 已加载（batch=1）。"
                    "本状态只表示加载/签名通过，不表示检索效果已验证。"
                )
        return {
            "status": "ready" if ready else ("blocked" if (self._problems or reason) else "unconfigured"),
            "message": reason or "encoder 未配置",
            "inference_ready": ready,
            "path_configured": True,
            "family": FAMILY,
            "architecture": self.spec.architecture,
            "embed_dim": self.spec.embed_dim,
            "input_resolution": self.spec.input_resolution,
            "image_input": self.spec.image_input_name,
            "image_output": self.spec.image_output_name,
            "text_input": self.spec.text_input_name,
            "text_output": self.spec.text_output_name,
            "context_length": self.spec.context_length,
            "quality_verified": False,
        }

    def encode_text(self, text: str) -> list[float]:
        return self._ensure().encode_text(text)

    def encode_image(self, image_bytes: bytes) -> list[float]:
        return self._ensure().encode_image(image_bytes)


class ChineseClipSubtitleEncoder:
    """Independent subtitle port that may share the official text ONNX.

    Semantic quality of using the visual-text tower for subtitles is 待验证.
    """

    QUALITY_NOTE = (
        "字幕编码器与图文 text 侧共用官方文本 ONNX；该路径的语义质量尚未证实（待验证）。"
        "不会用随机或哈希向量充数。"
    )

    def __init__(self, visual: ChineseClipVisualEncoder) -> None:
        self._visual = visual

    def available(self) -> bool:
        return self._visual.available()

    def status_message(self) -> str:
        return str(self.public_status().get("message") or "")

    def public_status(self) -> dict:
        status = dict(self._visual.public_status())
        status["quality_unverified"] = True
        status["quality_verified"] = False
        status["shares_text_encoder"] = True
        if status.get("inference_ready"):
            status["message"] = self.QUALITY_NOTE + " 运行时已加载。"
        else:
            status["message"] = ((status.get("message") or "") + " " + self.QUALITY_NOTE).strip()
        return status

    def encode_text(self, text: str) -> list[float]:
        return self._visual.encode_text(text)


class ConfigBlockedEncoder:
    """Returned when family/paths/signatures are missing or invalid. Never yields fake vectors."""

    def __init__(self, problems: list[str], role: str = "visual") -> None:
        self.problems = list(problems)
        self.role = role

    def available(self) -> bool:
        return False

    def status_message(self) -> str:
        return str(self.public_status().get("message") or "")

    def public_status(self) -> dict:
        prefix = "字幕编码器：" if self.role == "subtitle" else "图文编码器："
        extra = ""
        if self.role == "subtitle":
            extra = " 字幕路径为独立端口，可配置与 text 侧共用；语义质量待验证。"
        return {
            "status": "blocked",
            "message": prefix + "；".join(self.problems) + extra,
            "inference_ready": False,
            "family": FAMILY,
            "quality_verified": False,
            "quality_unverified": self.role == "subtitle",
        }

    def encode_text(self, text: str) -> list[float]:
        raise NotConfiguredError("；".join(self.problems), code="blocked:encoder_config")

    def encode_image(self, image_bytes: bytes) -> list[float]:
        raise NotConfiguredError("；".join(self.problems), code="blocked:encoder_config")
