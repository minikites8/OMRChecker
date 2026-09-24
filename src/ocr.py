"""Optional OCR support for fixed text regions in OMR templates."""

import os
import unicodedata
from dataclasses import dataclass

import cv2
import numpy as np

from src.logger import logger
from recognition_config import resolve_local_ocr_enabled


@dataclass(frozen=True)
class OCRResult:
    text: str
    confidence: float


def config_value(config, key, default=None):
    if hasattr(config, "get"):
        return config.get(key, default)
    return getattr(config, key, default)


def build_ocr_column_map(base_columns, text_labels, ocr_params):
    if not config_value(ocr_params, "enabled", False):
        return {}, []

    text_suffix = config_value(ocr_params, "text_suffix", "_ocr")
    confidence_suffix = config_value(
        ocr_params, "confidence_suffix", "_ocr_confidence"
    )
    mapping = {}
    columns = []
    used_columns = set(base_columns)
    for field_label in base_columns:
        if field_label not in text_labels:
            continue
        text_column = f"{field_label}{text_suffix}"
        confidence_column = f"{field_label}{confidence_suffix}"
        for column in (text_column, confidence_column):
            if column in used_columns:
                raise ValueError(f"OCR output column collides with template: {column}")
            used_columns.add(column)
            columns.append(column)
        mapping[field_label] = (text_column, confidence_column)
    return mapping, columns


class PaddleTextRecognizer:
    def __init__(self, ocr_params, model=None):
        self.ocr_params = ocr_params
        self.batch_size = int(config_value(ocr_params, "batch_size", 8))
        self.upscale = float(config_value(ocr_params, "upscale", 2.5))
        self.padding_x = int(config_value(ocr_params, "padding_x", 20))
        self.padding_y = int(config_value(ocr_params, "padding_y", 12))
        self.trim_whitespace = bool(
            config_value(ocr_params, "trim_whitespace", True)
        )
        self.field_charsets = config_value(ocr_params, "field_charsets", {})
        self.model = model or self._load_model()

    def _load_model(self):
        if config_value(self.ocr_params, "skip_model_source_check", True):
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        try:
            from paddleocr import TextRecognition
        except ImportError as error:
            raise RuntimeError(
                "OCR dependencies are missing. Install requirements.ocr.txt."
            ) from error

        model_name = config_value(
            self.ocr_params, "model_name", "PP-OCRv6_medium_rec"
        )
        device = config_value(self.ocr_params, "device", "cpu")
        logger.info(f"Loading OCR model: {model_name} on {device}")
        return TextRecognition(model_name=model_name, device=device)

    @staticmethod
    def _filter_small_components(mask, minimum_area=6):
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
            mask, 8
        )
        filtered = np.zeros_like(mask)
        for component in range(1, count):
            if stats[component, cv2.CC_STAT_AREA] >= minimum_area:
                filtered[labels == component] = 255
        return filtered

    def _trim_image(self, image):
        if not self.trim_whitespace:
            return image
        gray = (
            image
            if image.ndim == 2
            else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        )
        mask = np.where(gray < 190, 255, 0).astype(np.uint8)
        mask = self._filter_small_components(mask)
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return image
        left = max(0, int(xs.min()) - 12)
        right = min(image.shape[1], int(xs.max()) + 13)
        top = max(0, int(ys.min()) - 10)
        bottom = min(image.shape[0], int(ys.max()) + 11)
        return image[top:bottom, left:right]

    def prepare_image(self, image):
        prepared = self._trim_image(image)
        border_value = 255 if prepared.ndim == 2 else (255, 255, 255)
        prepared = cv2.copyMakeBorder(
            prepared,
            self.padding_y,
            self.padding_y,
            self.padding_x,
            self.padding_x,
            cv2.BORDER_CONSTANT,
            value=border_value,
        )
        if self.upscale != 1:
            prepared = cv2.resize(
                prepared,
                None,
                fx=self.upscale,
                fy=self.upscale,
                interpolation=cv2.INTER_CUBIC,
            )
        if prepared.ndim == 2:
            prepared = cv2.cvtColor(prepared, cv2.COLOR_GRAY2BGR)
        elif prepared.shape[2] == 4:
            prepared = cv2.cvtColor(prepared, cv2.COLOR_BGRA2BGR)
        return prepared

    def _apply_charset(self, text, field_label):
        normalized = unicodedata.normalize("NFKC", text).strip()
        charset = config_value(self.field_charsets, field_label)
        if charset:
            normalized = "".join(
                character for character in normalized if character in charset
            )
        return normalized

    def recognize(self, images, field_labels):
        prepared_images = [self.prepare_image(image) for image in images]
        predictions = list(
            self.model.predict(
                input=prepared_images,
                batch_size=self.batch_size,
            )
        )
        if len(predictions) != len(field_labels):
            raise RuntimeError(
                "OCR result count differs from input count: "
                f"{len(predictions)} != {len(field_labels)}"
            )

        results = []
        for field_label, prediction in zip(field_labels, predictions):
            text = self._apply_charset(prediction["rec_text"], field_label)
            confidence = float(prediction["rec_score"])
            results.append(OCRResult(text=text, confidence=confidence))
        return results


class AITextRecognizer:
    """Read text through the shared vision API; model dependencies load per provider."""
    def recognize(self, images, field_labels):
        from ai_judge import recognize_handwriting_crops
        results = recognize_handwriting_crops(images, field_labels)
        errors = [result.error for result in results if result.error]
        if errors:
            raise RuntimeError("; ".join(dict.fromkeys(errors)))
        return [OCRResult(text=result.text, confidence=result.confidence) for result in results]


def create_text_recognizer(ocr_params):
    if not resolve_local_ocr_enabled(config_value(ocr_params, "local_ocr_enabled")):
        return AITextRecognizer()
    provider = config_value(ocr_params, "provider", "paddleocr")
    if provider == "paddleocr":
        return PaddleTextRecognizer(ocr_params)
    raise ValueError(f"Unsupported OCR provider: {provider}")
