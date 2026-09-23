from types import SimpleNamespace

import numpy as np

from src.core import ImageInstanceOps


def _config():
    return SimpleNamespace(
        threshold_params=SimpleNamespace(
            TEXT_INK_THRESHOLD=180,
            TEXT_MIN_INK_RATIO=0.01,
        )
    )


def _field_block():
    return SimpleNamespace(bubble_dimensions=[100, 50], shift=0)


def _bubble():
    return SimpleNamespace(x=0, y=0)


def test_text_region_empty():
    image = np.full((50, 100), 255, dtype=np.uint8)
    assert ImageInstanceOps.is_text_region_filled(
        image, _bubble(), _field_block(), _config()
    ) is False


def test_text_region_filled():
    image = np.full((50, 100), 255, dtype=np.uint8)
    image[20:25, 10:90] = 0
    assert ImageInstanceOps.is_text_region_filled(
        image, _bubble(), _field_block(), _config()
    ) is True
