from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.domain.errors import NotConfiguredError
from app.infra.chinese_clip import (
    DEFAULT_CONTEXT_LENGTH,
    FAMILY,
    OFFICIAL_PROFILES,
    BertWordPieceTokenizer,
    ChineseClipSubtitleEncoder,
    ChineseClipVisualEncoder,
    ConfigBlockedEncoder,
    get_runtime,
    l2_normalize,
    preprocess_image,
    reset_runtime_cache,
    tokenize_ids,
    validate_spec_fields,
)
from app.infra.config import Settings
from app.main import build_encoders


def _write_vocab(path: Path, extra: list[str] | None = None) -> Path:
    tokens = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "你", "好", "a", "##b", ",", "."]
    if extra:
        tokens.extend(extra)
    path.write_text("\n".join(tokens) + "\n", encoding="utf-8")
    return path


def _png_bytes(color=(128, 64, 32), size=(12, 8)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_validate_rejects_unknown_family_and_architecture(tmp_path: Path):
    vocab = _write_vocab(tmp_path / "vocab.txt")
    dummy = tmp_path / "x.onnx"
    dummy.write_bytes(b"not-a-model")
    spec, problems = validate_spec_fields(
        family="generic_onnx",
        architecture="ViT-B-16",
        embed_dim=512,
        image_onnx=str(dummy),
        text_onnx=str(dummy),
        vocab=str(vocab),
        image_input_name="image",
        image_output_name="unnorm_image_features",
        text_input_name="text",
        text_output_name="unnorm_text_features",
        context_length=52,
    )
    assert spec is None
    assert any("不支持的 model family" in p for p in problems)

    spec, problems = validate_spec_fields(
        family=FAMILY,
        architecture="unknown-arch",
        embed_dim=512,
        image_onnx=str(dummy),
        text_onnx=str(dummy),
        vocab=str(vocab),
        image_input_name="image",
        image_output_name="unnorm_image_features",
        text_input_name="text",
        text_output_name="unnorm_text_features",
        context_length=52,
    )
    assert spec is None
    assert any("不支持的 architecture" in p for p in problems)


def test_validate_reports_missing_files_and_dim_mismatch(tmp_path: Path):
    spec, problems = validate_spec_fields(
        family=FAMILY,
        architecture="ViT-B-16",
        embed_dim=768,
        image_onnx=str(tmp_path / "missing.img.fp32.onnx"),
        text_onnx=str(tmp_path / "missing.txt.fp32.onnx"),
        vocab=str(tmp_path / "missing-vocab.txt"),
        image_input_name="image",
        image_output_name="unnorm_image_features",
        text_input_name="text",
        text_output_name="unnorm_text_features",
        context_length=DEFAULT_CONTEXT_LENGTH,
    )
    assert spec is None
    joined = "；".join(problems)
    assert "model_dim=768" in joined and "512" in joined
    assert "图像 ONNX文件不存在" in joined
    assert "文本 ONNX文件不存在" in joined
    assert "tokenizer 词表文件不存在" in joined


def test_official_profile_table_matches_published_resolutions():
    assert OFFICIAL_PROFILES["ViT-B-16"] == {"input_resolution": 224, "embed_dim": 512}
    assert OFFICIAL_PROFILES["ViT-L-14"]["input_resolution"] == 224
    assert OFFICIAL_PROFILES["ViT-L-14-336"] == {"input_resolution": 336, "embed_dim": 768}
    assert OFFICIAL_PROFILES["ViT-H-14"]["embed_dim"] == 1024
    assert OFFICIAL_PROFILES["RN50"]["input_resolution"] == 224


def test_preprocess_shape_and_clip_normalization():
    arr = preprocess_image(_png_bytes((255, 0, 0), (20, 10)), resolution=8)
    assert arr.shape == (1, 3, 8, 8)
    assert arr.dtype == np.float32
    # Official: RGB 1.0,0,0 then (x-mean)/std
    mean = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
    std = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
    expected = (np.array([1.0, 0.0, 0.0], dtype=np.float32) - mean) / std
    np.testing.assert_allclose(arr[0, :, 0, 0], expected, rtol=1e-5, atol=1e-5)


def test_tokenizer_official_wrap_and_chinese_split(tmp_path: Path):
    vocab = _write_vocab(tmp_path / "vocab.txt")
    tok = BertWordPieceTokenizer(vocab, do_lower_case=True)
    ids = tokenize_ids(tok, "你好", context_length=8)
    assert ids.shape == (1, 8)
    assert ids.dtype == np.int64
    assert ids[0, 0] == tok.vocab["[CLS]"]
    assert ids[0, 1] == tok.vocab["你"]
    assert ids[0, 2] == tok.vocab["好"]
    assert ids[0, 3] == tok.vocab["[SEP]"]
    assert list(ids[0, 4:]) == [0, 0, 0, 0]


def test_build_encoders_blocks_incomplete_config_without_fake_vectors(tmp_path: Path):
    settings = Settings(
        sqlite_path=tmp_path / "db.sqlite",
        worker_enabled=False,
        model_family="chinese_clip",
        model_architecture="ViT-B-16",
        model_dim=512,
        image_onnx_path=str(tmp_path / "no-img.onnx"),
        text_onnx_path=str(tmp_path / "no-txt.onnx"),
        tokenizer_vocab_path=str(tmp_path / "no-vocab.txt"),
    )
    visual, sub = build_encoders(settings)
    assert visual.available() is False
    assert sub.available() is False
    assert visual.public_status()["inference_ready"] is False
    assert "文件不存在" in visual.status_message()
    assert "待验证" in sub.status_message()
    with pytest.raises(NotConfiguredError, match="文件不存在"):
        visual.encode_text("你好")
    with pytest.raises(NotConfiguredError):
        visual.encode_image(_png_bytes())


def test_config_blocked_encoder_never_returns_vectors():
    enc = ConfigBlockedEncoder(["词表文件不存在：/no/vocab.txt"], "visual")
    with pytest.raises(NotConfiguredError, match="词表文件不存在"):
        enc.encode_text("x")


def _maybe_onnx():
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    return onnx, helper, TensorProto, numpy_helper


def _save_gemm_onnx(path: Path, input_name: str, output_name: str, input_shape, in_dtype, dim: int, flatten_first: bool):
    onnx, helper, TensorProto, numpy_helper = _maybe_onnx()
    x = helper.make_tensor_value_info(input_name, in_dtype, list(input_shape))
    y = helper.make_tensor_value_info(output_name, TensorProto.FLOAT, [1, dim])
    nodes = []
    current = input_name
    if in_dtype != TensorProto.FLOAT:
        nodes.append(helper.make_node("Cast", [current], ["casted"], to=TensorProto.FLOAT))
        current = "casted"
    nodes.append(helper.make_node("Flatten", [current], ["flat"], axis=1))
    n = int(np.prod(input_shape[1:]))
    weight = np.full((n, dim), 0.01, dtype=np.float32)
    nodes.append(helper.make_node("Gemm", ["flat", "W", "B"], [output_name]))
    bias = np.full((dim,), 0.5, dtype=np.float32)
    initializers = [
        numpy_helper.from_array(weight, name="W"),
        numpy_helper.from_array(bias, name="B"),
    ]
    graph = helper.make_graph(nodes, "synthetic", [x], [y], initializers)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 10
    onnx.checker.check_model(model)
    onnx.save(model, path)


def _synthetic_spec(tmp_path: Path, *, image_in="image", image_out="unnorm_image_features", text_in="text", text_out="unnorm_text_features", res=8, dim=4, ctx=8):
    vocab = _write_vocab(tmp_path / "vocab.txt")
    img = tmp_path / "toy.img.fp32.onnx"
    txt = tmp_path / "toy.txt.fp32.onnx"
    onnx, helper, TensorProto, _ = _maybe_onnx()
    _save_gemm_onnx(img, image_in, image_out, (1, 3, res, res), TensorProto.FLOAT, dim, True)
    _save_gemm_onnx(txt, text_in, text_out, (1, ctx), TensorProto.INT64, dim, False)
    spec, problems = validate_spec_fields(
        family=FAMILY,
        architecture="synthetic-test",
        embed_dim=dim,
        image_onnx=str(img),
        text_onnx=str(txt),
        vocab=str(vocab),
        image_input_name=image_in,
        image_output_name=image_out,
        text_input_name=text_in,
        text_output_name=text_out,
        context_length=ctx,
        allow_unofficial_profile=True,
        unofficial_resolution=res,
    )
    assert problems == [], problems
    assert spec is not None
    return spec


def test_synthetic_onnx_load_shape_and_l2_norm(tmp_path: Path):
    reset_runtime_cache()
    spec = _synthetic_spec(tmp_path)
    visual = ChineseClipVisualEncoder(spec)
    sub = ChineseClipSubtitleEncoder(visual)
    assert visual.available() is True
    assert sub.available() is True
    status = visual.public_status()
    assert status["inference_ready"] is True
    assert status["quality_verified"] is False
    assert "不表示检索效果已验证" in status["message"]
    assert sub.public_status()["quality_unverified"] is True
    assert "待验证" in sub.status_message()

    text_vec = visual.encode_text("你好")
    image_vec = visual.encode_image(_png_bytes())
    assert len(text_vec) == spec.embed_dim
    assert len(image_vec) == spec.embed_dim
    assert abs(float(np.linalg.norm(text_vec)) - 1.0) < 1e-6
    assert abs(float(np.linalg.norm(image_vec)) - 1.0) < 1e-6
    # subtitle port shares the same text path (quality unverified)
    assert sub.encode_text("你好") == text_vec

    # process-level singleton
    assert get_runtime(spec) is get_runtime(spec)


def test_unsupported_checkpoint_signatures_are_blocked(tmp_path: Path):
    reset_runtime_cache()
    spec = _synthetic_spec(tmp_path, image_in="not_image", text_in="not_text")
    # Spec claims official names, but the files were exported with different names.
    patched = _synthetic_spec(tmp_path, image_in="image", text_in="text")
    # Rebuild files with wrong names then wrap an official-named spec pointing at them.
    onnx, helper, TensorProto, _ = _maybe_onnx()
    img = tmp_path / "bad.img.onnx"
    txt = tmp_path / "bad.txt.onnx"
    _save_gemm_onnx(img, "pixels", "feat", (1, 3, 8, 8), TensorProto.FLOAT, 4, True)
    _save_gemm_onnx(txt, "tokens", "feat", (1, 8), TensorProto.INT64, 4, False)
    vocab = tmp_path / "vocab.txt"
    spec, problems = validate_spec_fields(
        family=FAMILY,
        architecture="synthetic-test",
        embed_dim=4,
        image_onnx=str(img),
        text_onnx=str(txt),
        vocab=str(vocab),
        image_input_name="image",
        image_output_name="unnorm_image_features",
        text_input_name="text",
        text_output_name="unnorm_text_features",
        context_length=8,
        allow_unofficial_profile=True,
        unofficial_resolution=8,
    )
    assert problems == []
    reset_runtime_cache()
    enc = ChineseClipVisualEncoder(spec)
    assert enc.available() is False
    assert "不支持的 checkpoint 签名" in enc.status_message()
    with pytest.raises(NotConfiguredError, match="不支持的 checkpoint 签名"):
        enc.encode_text("你好")
