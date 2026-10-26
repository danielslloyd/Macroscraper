"""Work out which product photo is the nutrition panel.

No model needed: OCR each candidate and score the text with
`nutrients.looks_like_panel`. A front-of-pack shot yields a brand name and a
weight; the panel yields "Nutrition Facts", "% Daily Value" and a dozen
nutrient names, which is unmistakable.

Candidates are tried in a sensible order (URLs and alt text that mention the
back of the pack or nutrition first, then later gallery positions, since the
hero image is almost never the panel) and the search stops at the first
confident hit, so a typical product costs one or two OCR passes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from . import nutrients, ocr

_HINT_STRONG = re.compile(r"nutrition|nutrient|facts[-_ ]?panel|supplement", re.I)
_HINT_BACK = re.compile(r"\bback\b|reverse|rear|label|_b\d|ingredient", re.I)


@dataclass
class LabelCandidate:
    index: int
    local_path: str
    public_url: str = ""
    alt: str = ""
    priority: int = 0


@dataclass
class LabelResult:
    """The best panel image found, plus every OCR pass we ran."""

    index: int | None = None
    local_path: str | None = None
    public_url: str | None = None
    text: str = ""
    likeness: float = 0.0
    ocr_text_by_index: dict[int, str] = field(default_factory=dict)
    likeness_by_index: dict[int, float] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return self.index is not None


def _priority(candidate: LabelCandidate) -> int:
    """Lower sorts earlier."""
    haystack = f"{candidate.public_url} {candidate.alt} {candidate.local_path}"
    if _HINT_STRONG.search(haystack):
        return 0
    if _HINT_BACK.search(haystack):
        return 1
    # The hero shot is the least likely to be the panel.
    return 3 if candidate.index == 0 else 2


def candidates_from_images(images: list[dict], root: Path) -> list[LabelCandidate]:
    """Build an ordered candidate list from a product record's `images[]`."""
    out: list[LabelCandidate] = []
    for i, image in enumerate(images or []):
        rel = image.get("local_path")
        if not rel:
            continue
        path = root / rel
        if not path.exists():
            continue
        candidate = LabelCandidate(
            index=i,
            local_path=rel,
            public_url=image.get("public_url") or "",
            alt=image.get("alt") or image.get("role") or "",
        )
        candidate.priority = _priority(candidate)
        out.append(candidate)
    out.sort(key=lambda c: (c.priority, c.index))
    return out


async def find_label(
    images: list[dict],
    root: Path,
    *,
    max_images: int | None = None,
    threshold: float | None = None,
) -> LabelResult:
    """OCR candidate photos until one clearly looks like a Nutrition Facts panel."""
    cfg = config.nutrition_config()
    limit = cfg["max_images_ocr"] if max_images is None else max_images
    floor = cfg["panel_likeness_threshold"] if threshold is None else threshold

    result = LabelResult()
    if not ocr.available():
        return result

    for candidate in candidates_from_images(images, root)[:limit]:
        text = await ocr.ocr_image(root / candidate.local_path)
        likeness = nutrients.looks_like_panel(text)
        result.ocr_text_by_index[candidate.index] = text
        result.likeness_by_index[candidate.index] = round(likeness, 3)
        if likeness > result.likeness:
            result.index = candidate.index
            result.local_path = candidate.local_path
            result.public_url = candidate.public_url
            result.text = text
            result.likeness = likeness
        # A panel scores well above this; stop early rather than OCR the rest.
        if likeness >= 0.7:
            break

    if result.likeness < floor:
        # Keep the OCR text for the record, but do not claim we found a panel.
        return LabelResult(
            ocr_text_by_index=result.ocr_text_by_index,
            likeness_by_index=result.likeness_by_index,
            text=result.text,
            likeness=result.likeness,
        )
    return result


__all__ = ["LabelCandidate", "LabelResult", "candidates_from_images", "find_label"]
