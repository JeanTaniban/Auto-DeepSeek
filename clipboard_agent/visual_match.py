from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np


class TemplateMatchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TemplateMatch:
    confidence: float
    left: int
    top: int
    width: int
    height: int
    scale: float = 1.0

    @property
    def center_x(self) -> int:
        return self.left + self.width // 2

    @property
    def center_y(self) -> int:
        return self.top + self.height // 2

    def screen_center(self, origin_left: int, origin_top: int) -> tuple[int, int]:
        """Convert the match center from image-local pixels to desktop pixels."""
        return int(origin_left) + self.center_x, int(origin_top) + self.center_y


def load_template(path: str | Path) -> np.ndarray:
    """Load a template image robustly, including Unicode Windows paths."""
    target = Path(path).expanduser()
    if not target.exists() or not target.is_file():
        raise TemplateMatchError(f"Image de référence Copier introuvable : {target}")
    try:
        raw = np.fromfile(str(target), dtype=np.uint8)
        image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    except Exception as exc:  # pragma: no cover - defensive wrapper around native code
        raise TemplateMatchError(f"Impossible de lire l'image de référence Copier : {exc}") from exc
    if image is None or image.size == 0:
        raise TemplateMatchError("L'image de référence Copier est illisible ou dans un format non supporté.")
    height, width = image.shape[:2]
    if width < 8 or height < 8:
        raise TemplateMatchError("L'image de référence Copier est trop petite (minimum 8 × 8 px).")
    return image


def bgra_bytes_to_bgr(pixels: bytes, width: int, height: int) -> np.ndarray:
    expected = int(width) * int(height) * 4
    if width <= 0 or height <= 0 or len(pixels) != expected:
        raise TemplateMatchError("Capture écran BGRA invalide pour la détection du bouton Copier.")
    frame = np.frombuffer(pixels, dtype=np.uint8).reshape((height, width, 4))
    return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


def _validate_images(scene_bgr: np.ndarray, template_bgr: np.ndarray) -> tuple[int, int, int, int]:
    if scene_bgr is None or template_bgr is None or scene_bgr.size == 0 or template_bgr.size == 0:
        raise TemplateMatchError("Image écran ou image de référence vide.")
    if scene_bgr.ndim != 3 or template_bgr.ndim != 3:
        raise TemplateMatchError("Format d'image incompatible avec la détection du bouton Copier.")
    scene_h, scene_w = scene_bgr.shape[:2]
    template_h, template_w = template_bgr.shape[:2]
    if template_w > scene_w or template_h > scene_h:
        raise TemplateMatchError("L'image de référence Copier est plus grande que l'écran de recherche.")
    return scene_h, scene_w, template_h, template_w


def best_template_match_exact(
    scene_bgr: np.ndarray,
    template_bgr: np.ndarray,
    *,
    near_best_margin: float = 0.015,
) -> TemplateMatch:
    """Return the best native-scale screenshot match without resizing the template.

    The reference image is treated as a literal screen crop.  We therefore use
    the original BGR pixels and ``TM_SQDIFF_NORMED`` at exactly scale 1.0.  A
    normalized squared-difference score is converted to an intuitive confidence
    with ``1 - score``.  If several almost-equivalent Copy buttons are visible,
    the lowest one is selected because it normally belongs to the latest reply.
    """
    _scene_h, _scene_w, template_h, template_w = _validate_images(scene_bgr, template_bgr)
    margin = max(0.0, min(0.10, float(near_best_margin)))

    # Exact BGR comparison.  No grayscale conversion and no resizing: the file
    # selected by the user is compared as the exact screenshot reference.
    result = cv2.matchTemplate(scene_bgr, template_bgr, cv2.TM_SQDIFF_NORMED)
    result = np.nan_to_num(result, nan=1.0, posinf=1.0, neginf=0.0)
    min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(result)
    best_confidence = max(0.0, min(1.0, 1.0 - float(min_val)))

    # SQDIFF: smaller is better.  Convert the near-best confidence margin back
    # into a maximum accepted squared-difference value.
    max_diff = min(1.0, float(min_val) + margin)
    ys, xs = np.where(result <= max_diff)
    candidates: list[TemplateMatch] = []
    for y, x in zip(ys.tolist(), xs.tolist()):
        confidence = max(0.0, min(1.0, 1.0 - float(result[y, x])))
        candidates.append(
            TemplateMatch(
                confidence=confidence,
                left=int(x),
                top=int(y),
                width=int(template_w),
                height=int(template_h),
                scale=1.0,
            )
        )

    if not candidates:
        # Defensive fallback; minLoc always exists when validation succeeded.
        return TemplateMatch(
            confidence=best_confidence,
            left=int(min_loc[0]),
            top=int(min_loc[1]),
            width=int(template_w),
            height=int(template_h),
            scale=1.0,
        )

    # Among essentially equal candidates, prefer the lowest visible Copy button.
    # Confidence then x position provide deterministic tie-breakers.
    return max(candidates, key=lambda item: (item.top + item.height, item.confidence, item.left))


def find_template_exact(
    scene_bgr: np.ndarray,
    template_bgr: np.ndarray,
    *,
    threshold: float = 0.86,
    near_best_margin: float = 0.015,
) -> TemplateMatch:
    """Find the exact-scale template or raise when the confidence is too low."""
    threshold = max(0.50, min(0.99, float(threshold)))
    match = best_template_match_exact(scene_bgr, template_bgr, near_best_margin=near_best_margin)
    if match.confidence < threshold:
        raise TemplateMatchError(
            "Aucun bouton Copier détecté avec une confiance suffisante "
            f"(meilleur score {match.confidence:.1%}, seuil {threshold:.1%}, échelle 1.00)."
        )
    return match


def _normalized_scales(scales: Iterable[float] | None) -> tuple[float, ...]:
    """Legacy helper kept for compatibility with tests/tools from V2.4-V2.7."""
    if scales is None:
        scales = (1.0,)
    values = sorted({round(float(value), 4) for value in scales if 0.5 <= float(value) <= 1.75})
    return tuple(values) or (1.0,)


def find_template(
    scene_bgr: np.ndarray,
    template_bgr: np.ndarray,
    *,
    threshold: float = 0.86,
    scales: Iterable[float] | None = None,
    near_best_margin: float = 0.015,
) -> TemplateMatch:
    """Compatibility wrapper.

    V2.8 intentionally uses exact-scale matching.  Passing scales other than
    1.0 is rejected so the configured screenshot is never silently deformed.
    """
    normalized = _normalized_scales(scales)
    if any(abs(scale - 1.0) > 1e-6 for scale in normalized):
        raise TemplateMatchError(
            "La détection Copier V2.8 utilise uniquement l'échelle native 1.00 ; "
            "le template n'est jamais redimensionné."
        )
    return find_template_exact(
        scene_bgr,
        template_bgr,
        threshold=threshold,
        near_best_margin=near_best_margin,
    )


def write_image(path: str | Path, image_bgr: np.ndarray) -> None:
    """Write a BGR image to a Unicode-safe path."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower() or ".png"
    if suffix not in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
        suffix = ".png"
        target = target.with_suffix(suffix)
    ok, encoded = cv2.imencode(suffix, image_bgr)
    if not ok:
        raise TemplateMatchError(f"Impossible d'encoder l'image diagnostic : {target}")
    encoded.tofile(str(target))
