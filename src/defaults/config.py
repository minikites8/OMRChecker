from dotmap import DotMap

CONFIG_DEFAULTS = DotMap(
    {
        "dimensions": {
            "display_height": 2480,
            "display_width": 1640,
            "processing_height": 820,
            "processing_width": 666,
        },
        "threshold_params": {
            "GAMMA_LOW": 0.7,
            "MIN_GAP": 30,
            "MIN_JUMP": 25,
            "CONFIDENT_SURPLUS": 5,
            "JUMP_DELTA": 30,
            "PAGE_TYPE_FOR_THRESHOLD": "white",
            "TEXT_INK_THRESHOLD": 180,
            "TEXT_MIN_INK_RATIO": 0.01,
        },
        "alignment_params": {
            # Note: 'auto_align' enables automatic template alignment, use if the scans show slight misalignments.
            "auto_align": False,
            "match_col": 5,
            "max_steps": 20,
            "stride": 1,
            "thickness": 3,
        },
        "pdf_params": {
            "pdf_dpi": "auto",
            "pdf_page": 1,
        },
        "ocr_params": {
            "enabled": False,
            "provider": "paddleocr",
            "model_name": "PP-OCRv6_medium_rec",
            "device": "cpu",
            "min_confidence": 0.65,
            "text_suffix": "_ocr",
            "confidence_suffix": "_ocr_confidence",
            "batch_size": 8,
            "upscale": 2.5,
            "padding_x": 20,
            "padding_y": 12,
            "trim_whitespace": True,
            "save_crops": False,
            "skip_model_source_check": True,
            "field_charsets": {},
        },
        "outputs": {
            "show_image_level": 0,
            "save_image_level": 0,
            "save_detections": True,
            "filter_out_multimarked_files": False,
        },
    },
    _dynamic=False,
)
