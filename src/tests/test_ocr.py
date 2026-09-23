from types import SimpleNamespace

import numpy as np

from src.core import ImageInstanceOps
from src.ocr import (
    OCRResult,
    PaddleTextRecognizer,
    build_ocr_column_map,
)


class FakePaddleModel:
    def __init__(self, predictions):
        self.predictions = predictions
        self.received = None

    def predict(self, input, batch_size):
        self.received = (input, batch_size)
        return iter(self.predictions)


class FakeRecognizer:
    def recognize(self, images, field_labels):
        assert len(images) == len(field_labels) == 1
        return [OCRResult(text="测试文字", confidence=0.91)]

class SequenceRecognizer:
    def __init__(self, results):
        self.results = results

    def recognize(self, images, field_labels):
        assert len(images) == len(field_labels) == len(self.results)
        return self.results


def _ocr_params(**overrides):
    values = {
        "enabled": True,
        "provider": "paddleocr",
        "model_name": "PP-OCRv6_medium_rec",
        "device": "cpu",
        "min_confidence": 0.65,
        "text_suffix": "_ocr",
        "confidence_suffix": "_ocr_confidence",
        "batch_size": 4,
        "upscale": 2,
        "padding_x": 5,
        "padding_y": 5,
        "trim_whitespace": True,
        "save_crops": False,
        "skip_model_source_check": True,
        "field_charsets": {"student_id_written": "0123456789"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_paddle_text_recognizer_prepares_images_and_filters_charset():
    model = FakePaddleModel(
        [{"rec_text": "Ａ1B2", "rec_score": np.float32(0.95)}]
    )
    recognizer = PaddleTextRecognizer(_ocr_params(), model=model)
    image = np.full((40, 160), 255, dtype=np.uint8)
    image[15:25, 50:90] = 0

    results = recognizer.recognize([image], ["student_id_written"])

    assert results == [OCRResult(text="12", confidence=float(np.float32(0.95)))]
    prepared_images, batch_size = model.received
    assert batch_size == 4
    assert prepared_images[0].ndim == 3
    assert prepared_images[0].shape[2] == 3


def test_build_ocr_column_map_follows_text_field_output_order():
    mapping, columns = build_ocr_column_map(
        ["student_id", "name", "q1", "text1"],
        {"name", "text1"},
        _ocr_params(),
    )

    assert mapping == {
        "name": ("name_ocr", "name_ocr_confidence"),
        "text1": ("text1_ocr", "text1_ocr_confidence"),
    }
    assert columns == [
        "name_ocr",
        "name_ocr_confidence",
        "text1_ocr",
        "text1_ocr_confidence",
    ]


def test_read_text_fields_keeps_presence_and_adds_ocr_result():
    config = SimpleNamespace(
        outputs=SimpleNamespace(save_image_level=0),
        threshold_params=SimpleNamespace(
            TEXT_INK_THRESHOLD=180,
            TEXT_MIN_INK_RATIO=0.01,
        ),
        ocr_params=_ocr_params(),
    )
    bubble = SimpleNamespace(x=0, y=0, field_label="name")
    field_block = SimpleNamespace(
        field_type="QTYPE_TEXT",
        bubble_dimensions=[100, 50],
        shift=0,
        traverse_bubbles=[[bubble]],
    )
    template = SimpleNamespace(field_blocks=[field_block])
    image = np.full((50, 100), 255, dtype=np.uint8)
    image[20:25, 10:90] = 0
    operations = ImageInstanceOps(config)
    operations.ocr_recognizer = FakeRecognizer()

    states = operations.read_text_fields(template, image, "sample.png")

    assert states["name"] == {
        "filled": True,
        "text": "测试文字",
        "confidence": "0.9100",
    }

def test_read_text_fields_selects_better_aligned_source():
    config = SimpleNamespace(
        outputs=SimpleNamespace(save_image_level=0),
        threshold_params=SimpleNamespace(
            TEXT_INK_THRESHOLD=180,
            TEXT_MIN_INK_RATIO=0.01,
        ),
        ocr_params=_ocr_params(),
    )
    name_bubble = SimpleNamespace(x=0, y=0, field_label="name")
    id_bubble = SimpleNamespace(x=0, y=50, field_label="student_id_written")
    name_block = SimpleNamespace(
        field_type="QTYPE_TEXT",
        bubble_dimensions=[100, 50],
        shift=0,
        traverse_bubbles=[[name_bubble]],
    )
    id_block = SimpleNamespace(
        field_type="QTYPE_TEXT",
        bubble_dimensions=[100, 50],
        shift=0,
        traverse_bubbles=[[id_bubble]],
    )
    template = SimpleNamespace(field_blocks=[name_block, id_block])
    preserved = np.full((100, 100), 255, dtype=np.uint8)
    aligned = np.full((100, 100), 255, dtype=np.uint8)
    preserved[20:25, 10:90] = 0
    preserved[70:75, 10:90] = 0
    aligned[20:25, 10:90] = 0
    aligned[70:75, 10:90] = 0
    operations = ImageInstanceOps(config)
    operations.ocr_recognizer = SequenceRecognizer(
        [
            OCRResult(text="True / False (T-F)", confidence=0.93),
            OCRResult(text="", confidence=0.0),
            OCRResult(text="小明", confidence=0.91),
            OCRResult(text="24035759", confidence=0.99),
        ]
    )

    states = operations.read_text_fields(
        template,
        preserved,
        "sample.png",
        alternate_image=aligned,
    )

    assert states["name"]["text"] == "小明"
    assert states["student_id_written"]["text"] == "24035759"
