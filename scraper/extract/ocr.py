"""OCR of product photographs.

Nutrition panels are printed on the package, and for several retailers the
packaging photo is the only place the numbers exist at all. Reading them with
OCR plus the regex parser in `nutrients` keeps the common case off any model:
local, offline, and roughly a second per image.

Default engine is RapidOCR (ONNX runtime, pip-installable, no system binary).
Tesseract is supported as an alternative for anyone who already has it.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .. import config

_engine = None
_engine_name: str | None = None

# Panels are often a small region of a large photo; upscaling short edges helps
# the detector find the thin print.
_MIN_WIDTH = 1200


class OcrUnavailable(RuntimeError):
    """No OCR engine is installed."""


def _load_engine(name: str):
    if name == "rapidocr":
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # pragma: no cover
            raise OcrUnavailable(
                "RapidOCR is not installed. Run: pip install rapidocr-onnxruntime"
            ) from exc
        return RapidOCR()
    if name == "tesseract":
        try:
            import pytesseract  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise OcrUnavailable(
                "pytesseract is not installed. Run: pip install pytesseract "
                "(and install the Tesseract binary)"
            ) from exc
        return "tesseract"
    raise OcrUnavailable(f"unknown ocr_engine '{name}'")


def engine():
    """Lazily build and cache the configured OCR engine."""
    global _engine, _engine_name
    name = config.nutrition_config()["ocr_engine"]
    if _engine is None or _engine_name != name:
        _engine = _load_engine(name)
        _engine_name = name
    return _engine


def available() -> bool:
    try:
        engine()
        return True
    except OcrUnavailable:
        return False


def _prepare(path: Path) -> Path:
    """Upscale a small image into a temp file; return the path to OCR."""
    try:
        from PIL import Image
    except ImportError:
        return path
    try:
        with Image.open(path) as img:
            if img.width >= _MIN_WIDTH:
                return path
            scale = _MIN_WIDTH / max(img.width, 1)
            resized = img.convert("RGB").resize(
                (int(img.width * scale), int(img.height * scale)), Image.LANCZOS
            )
            out = path.with_name(f"{path.stem}.ocr{path.suffix or '.jpg'}")
            resized.save(out, quality=92)
            return out
    except Exception:
        return path


def _run_sync(path: Path) -> str:
    eng = engine()
    target = _prepare(path)
    try:
        if eng == "tesseract":
            import pytesseract
            from PIL import Image

            with Image.open(target) as img:
                return pytesseract.image_to_string(img)
        result, _elapsed = eng(str(target))
        if not result:
            return ""
        # RapidOCR returns [box, text, confidence] per detected line.
        return " ".join(str(line[1]) for line in result if len(line) > 1)
    finally:
        if target != path:
            try:
                target.unlink()
            except OSError:
                pass


async def ocr_image(path: str | Path) -> str:
    """OCR one image, off the event loop. Returns '' when it cannot be read."""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        return await asyncio.to_thread(_run_sync, p)
    except OcrUnavailable:
        raise
    except Exception:
        return ""


__all__ = ["OcrUnavailable", "available", "engine", "ocr_image"]
