from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from src.processors.manager import PROCESSOR_MANAGER

AlignPageBorder = PROCESSOR_MANAGER.processors["AlignPageBorder"]


TARGET_RECT = [120, 230, 2430, 3020]
SOURCE_CORNERS = np.asarray(
    [[291, 292], [2193, 265], [2375, 2627], [170, 2608]], dtype=np.float32
)


def _processor():
    config = SimpleNamespace(
        outputs=SimpleNamespace(show_image_level=0),
        alignment_params=SimpleNamespace(auto_align=True),
    )
    image_instance_ops = SimpleNamespace(tuning_config=config)
    return AlignPageBorder(
        options={
            "targetRect": TARGET_RECT,
            "topSearch": [0.055, 0.18],
            "bottomSearch": [0.65, 0.94],
            "leftSearch": [0.035, 0.30],
            "rightSearch": [0.70, 0.995],
            "houghThreshold": 250,
            "maxBottomLineRank": 500,
        },
        relative_dir=Path("."),
        image_instance_ops=image_instance_ops,
    )


def _photographed_fixture():
    width, height = 2550, 3300
    reference = np.full((height, width), 255, dtype=np.uint8)
    left, top, right, bottom = TARGET_RECT
    cv2.rectangle(reference, (left, top), (right, bottom), 0, 5)
    marker_centers = [(400, 600), (1300, 1200), (2050, 2400)]
    for center in marker_centers:
        cv2.circle(reference, center, 24, 0, -1)

    destination = np.asarray(
        [[left, top], [right, top], [right, bottom], [left, bottom]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(destination, SOURCE_CORNERS)
    photographed = cv2.warpPerspective(
        reference,
        transform,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    return photographed, marker_centers


def test_align_page_border_restores_template_coordinates():
    photographed, marker_centers = _photographed_fixture()
    aligned = _processor().apply_filter(photographed, "phone.jpg")

    assert aligned is not None
    assert aligned.shape == photographed.shape
    for x, y in marker_centers:
        x_start, x_end = x - 18, x + 19
        y_start, y_end = y - 18, y + 19
        region = aligned[y_start:y_end, x_start:x_end]
        assert float(np.mean(region)) < 80


def test_align_page_border_returns_none_without_frame():
    blank = np.full((3300, 2550), 255, dtype=np.uint8)
    aligned = _processor().apply_filter(blank, "blank.jpg")
    assert aligned is None

def test_align_page_border_accepts_frame_near_right_edge():
    image = np.full((3300, 2550), 255, dtype=np.uint8)
    near_edge_corners = np.asarray(
        [[191, 265], [2439, 300], [2537, 2991], [206, 3023]],
        dtype=np.int32,
    )
    cv2.polylines(image, [near_edge_corners], True, 0, 6)

    detected, _edges = _processor().find_border(image)

    assert detected is not None
    assert float(np.max(detected[:, 0])) > 0.965 * image.shape[1]
