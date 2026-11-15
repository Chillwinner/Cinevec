from __future__ import annotations

from types import SimpleNamespace

from app.application.media_service import MediaService, parse_byte_range
from app.infra.file_store import LocalFileStore


def test_parse_byte_range_suffix_and_open_end():
    start, end, partial = parse_byte_range("bytes=0-3", 10)
    assert (start, end, partial) == (0, 3, True)
    start, end, partial = parse_byte_range("bytes=8-", 10)
    assert (start, end, partial) == (8, 9, True)
    start, end, partial = parse_byte_range("bytes=-2", 10)
    assert (start, end, partial) == (8, 9, True)
    start, end, partial = parse_byte_range(None, 10)
    assert (start, end, partial) == (0, 9, False)


def test_hosted_media_range(tmp_path):
    store = LocalFileStore(tmp_path / "store", label="pytest-temp")
    store.put_bytes("originals/a/clip.mp4", b"abcdefghij", "video/mp4")
    asset = SimpleNamespace(
        id="a1",
        source_id="s1",
        object_key="originals/a/clip.mp4",
        origin="hosted",
        excluded=False,
        source_deleted=False,
    )
    metadata = SimpleNamespace(get_asset=lambda _id: asset)
    media = MediaService(metadata, store)
    payload = media.open("a1", "bytes=2-5")
    assert payload["partial"] is True
    assert payload["data"] == b"cdef"
    assert payload["size"] == 10
