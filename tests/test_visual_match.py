from pathlib import Path

import cv2
import numpy as np
import pytest

from clipboard_agent.visual_match import (
    TemplateMatchError,
    best_template_match_exact,
    find_template,
    find_template_exact,
    load_template,
)


def _button_template(width=90, height=28):
    img = np.full((height, width, 3), 45, dtype=np.uint8)
    cv2.rectangle(img, (1, 1), (width - 2, height - 2), (95, 95, 95), 1)
    cv2.rectangle(img, (8, 8), (18, 18), (220, 220, 220), 1)
    cv2.putText(img, "Copier", (26, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (235, 235, 235), 1, cv2.LINE_AA)
    return img


def _scene(width=500, height=360):
    return np.full((height, width, 3), 18, dtype=np.uint8)


def test_find_template_exact_match():
    template = _button_template()
    scene = _scene()
    y, x = 220, 310
    h, w = template.shape[:2]
    scene[y:y+h, x:x+w] = template

    match = find_template_exact(scene, template, threshold=0.85)

    assert match.confidence > 0.999
    assert match.scale == 1.0
    assert match.width == w
    assert match.height == h
    assert abs(match.center_x - (x + w // 2)) <= 1
    assert abs(match.center_y - (y + h // 2)) <= 1


def test_exact_match_does_not_rescale_template():
    template = _button_template()
    scene = _scene(640, 420)
    scaled = cv2.resize(template, None, fx=1.08, fy=1.08, interpolation=cv2.INTER_LINEAR)
    h, w = scaled.shape[:2]
    scene[250:250+h, 380:380+w] = scaled

    best = best_template_match_exact(scene, template)
    assert best.scale == 1.0
    assert best.width == template.shape[1]
    assert best.height == template.shape[0]
    assert best.confidence < 0.99


def test_compatibility_wrapper_rejects_multiscale_request():
    template = _button_template()
    scene = _scene()
    with pytest.raises(TemplateMatchError, match="échelle native 1.00"):
        find_template(scene, template, threshold=0.70, scales=(0.95, 1.0, 1.05))


def test_find_template_prefers_lower_near_equal_match():
    template = _button_template()
    scene = _scene(500, 500)
    h, w = template.shape[:2]
    scene[80:80+h, 300:300+w] = template
    scene[360:360+h, 280:280+w] = template

    match = find_template_exact(scene, template, threshold=0.90)

    assert match.center_y > 350
    assert match.scale == 1.0


def test_find_template_rejects_below_threshold_but_diagnostic_returns_best():
    template = _button_template()
    scene = _scene()
    best = best_template_match_exact(scene, template)
    assert 0.0 <= best.confidence < 0.95
    with pytest.raises(TemplateMatchError, match="Aucun bouton Copier"):
        find_template_exact(scene, template, threshold=0.95)


def test_load_template_rejects_missing_file(tmp_path):
    with pytest.raises(TemplateMatchError, match="introuvable"):
        load_template(tmp_path / "missing.png")


def test_load_template_reads_unicode_path(tmp_path):
    path = Path(tmp_path) / "bouton_copié.png"
    template = _button_template()
    ok, encoded = cv2.imencode(".png", template)
    assert ok
    encoded.tofile(str(path))

    loaded = load_template(path)
    assert loaded.shape == template.shape
    assert np.array_equal(loaded, template)


def test_match_screen_center_adds_virtual_screen_origin_exactly():
    from clipboard_agent.visual_match import TemplateMatch

    match = TemplateMatch(confidence=0.95, left=40, top=20, width=100, height=30, scale=1.0)
    assert match.center_x == 90
    assert match.center_y == 35
    assert match.screen_center(-1920, 0) == (-1830, 35)
