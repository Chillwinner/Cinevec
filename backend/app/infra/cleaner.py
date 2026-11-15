from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageFilter, ImageStat

from app.domain.media import SampleSettings


def _grayscale(image: Image.Image) -> Image.Image:
    return image.convert("L")

def _pixel_values(image: Image.Image) -> list[int]:
    # Pillow 13 起 getdata 已弃用，14 将移除；3.14 环境优先用新接口。
    getter = getattr(image, "get_flattened_data", None)
    if callable(getter):
        return list(getter())
    return list(image.getdata())




def luminance_mean(image: Image.Image) -> float:
    stats = ImageStat.Stat(_grayscale(image))
    return float(stats.mean[0])


def laplacian_variance(image: Image.Image) -> float:
    gray = _grayscale(image).resize((128, 128))
    edges = gray.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
    pixels = _pixel_values(edges)
    if not pixels:
        return 0.0
    mean = sum(pixels) / len(pixels)
    return sum((p - mean) ** 2 for p in pixels) / len(pixels)


def signature(image: Image.Image, size: int = 32) -> list[float]:
    small = _grayscale(image).resize((size, size))
    return [float(p) for p in _pixel_values(small)]


def mean_abs_diff(sig_a: list[float], sig_b: list[float]) -> float:
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 255.0
    return sum(abs(a - b) for a, b in zip(sig_a, sig_b, strict=True)) / len(sig_a)


class DefaultFrameCleaner:
    def inspect(
        self,
        image_bytes: bytes,
        settings: SampleSettings,
        last_kept_signature: list[float] | None,
    ) -> dict:
        try:
            image = Image.open(BytesIO(image_bytes))
            image.load()
            image = image.convert("RGB")
        except Exception as exc:
            return {
                "kept": False,
                "drop_reason": "decode_failed",
                "detail": str(exc)[:200],
                "signature": None,
            }
        mean = luminance_mean(image)
        if mean <= settings.black_max_mean:
            return {"kept": False, "drop_reason": "all_black", "detail": f"mean={mean:.1f}", "signature": None}
        if mean >= settings.white_min_mean:
            return {"kept": False, "drop_reason": "all_white", "detail": f"mean={mean:.1f}", "signature": None}
        blur = laplacian_variance(image)
        if blur <= settings.blur_max_laplacian_var:
            return {"kept": False, "drop_reason": "severe_blur", "detail": f"lap_var={blur:.1f}", "signature": None}
        sig = signature(image)
        if last_kept_signature is not None:
            mad = mean_abs_diff(sig, last_kept_signature)
            if mad <= settings.similar_max_mad:
                return {
                    "kept": False,
                    "drop_reason": "consecutive_similar",
                    "detail": f"mad={mad:.2f}",
                    "signature": None,
                }
        return {"kept": True, "drop_reason": None, "detail": "", "signature": sig, "image": image}
