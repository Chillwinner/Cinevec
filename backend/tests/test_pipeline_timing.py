from io import BytesIO

from PIL import Image

from app.infra.pipeline_timing import end, extract_remote_timings, image_bytes_info, note, start


def test_timing_records_seconds():
    ck: dict = {}
    start(ck, "decode")
    end(ck, "decode")
    item = ck["timings"]["decode"]
    assert "started_at" in item
    assert "ended_at" in item
    assert item["seconds"] >= 0


def test_extract_remote_timings_empty_without_fields():
    assert extract_remote_timings({"dimension": 768, "vector": [0.1]}) == {}


def test_extract_remote_timings_seconds_and_ms():
    got = extract_remote_timings({"queue_ms": 120, "preprocess": 0.4, "inference_ms": 21000})
    assert got["queue_seconds"] == 0.12
    assert got["preprocess_seconds"] == 0.4
    assert got["inference_seconds"] == 21.0


def test_image_bytes_info_png():
    buf = BytesIO()
    Image.new("RGB", (32, 48), (1, 2, 3)).save(buf, format="PNG")
    data = buf.getvalue()
    info = image_bytes_info(data)
    assert info["width"] == 32
    assert info["height"] == 48
    assert info["bytes"] == len(data)
    ck: dict = {}
    note(ck, "model_input_file", **info)
    assert ck["timings"]["model_input_file"]["width"] == 32
