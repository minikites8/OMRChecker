"""Per-task text-recognition routing, shared by review and scanner workers."""
import os


def resolve_local_ocr_enabled(value=None):
    if value is None:
        value = os.environ.get("OMR_LOCAL_OCR_ENABLED", "true")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "on", "yes"}:
            return True
        if normalized in {"0", "false", "off", "no"}:
            return False
    raise ValueError("local_ocr_enabled 请使用 true 或 false")


def recognition_settings(value=None):
    enabled = resolve_local_ocr_enabled(value)
    return {"local_ocr_enabled": enabled,
            "recognition_mode": "local_ocr_ai" if enabled else "ai_only"}
