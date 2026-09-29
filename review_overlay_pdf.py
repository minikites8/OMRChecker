"""Build scan PDFs with independently switchable objective and handwriting layers."""
import hashlib
import json
from pathlib import Path

import cv2
import fitz
import numpy as np

from exam_review import PAGE_W, PAGE_H, _expand_crop_rect, _subjective_crop_regions
from objective_view import objective_layout

OVERLAY_VERSION = 3
OBJECTIVE_COLOR = (0.05, 0.48, 0.29)
FILL_COLOR = (0.12, 0.36, 0.85)


def overlay_fingerprint(files, review, reference_pdf=None):
    sources = [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in files]
    reference = None
    if reference_pdf:
        p = Path(reference_pdf)
        reference = (str(p), p.stat().st_size, p.stat().st_mtime_ns)
    data = {"version": OVERLAY_VERSION, "sources": sources, "reference": reference,
            "adjustments": review.get("crop_adjustments"),
            "objective": [(str(item.get("question", "")), item.get("recognized"))
                          for item in review.get("objective", [])],
            "questions": [str(item.get("question", "")) for item in review.get("items", [])]}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]


def scan_polygon(points, image, region=None):
    """Use the reference-to-scan matrix used by the recognition crop pipeline."""
    sx, sy = image.shape[1] / PAGE_W, image.shape[0] / PAGE_H
    matrix = np.asarray((region or {}).get("matrix", [[1, 0, 0], [0, 1, 0]]), dtype=float)
    if matrix.shape != (2, 3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix[:, :2])) < 1e-8:
        matrix = np.array([[1., 0., 0.], [0., 1., 0.]])
    pixels = np.asarray(points, dtype=float) * [sx, sy]
    mapped = (pixels @ matrix[:, :2].T + matrix[:, 2]) / [sx, sy]
    mapped[:, 0] = np.clip(mapped[:, 0], 0, PAGE_W)
    mapped[:, 1] = np.clip(mapped[:, 1], 0, PAGE_H)
    return [fitz.Point(float(x), float(y)) for x, y in mapped]


def _box(page, points, color, layer, fill=False, dashed=False):
    page.draw_polyline(points, color=color, closePath=True, width=1 if fill else 0.6,
                       fill=color if fill else None, fill_opacity=0.16,
                       stroke_opacity=0.9, dashes="[3 2] 0" if dashed else None, oc=layer, overlay=True)


def write_overlay_pdf(images, review, destination):
    """Retain each scan page and mark the exact recognition/display coordinates."""
    if not images:
        raise ValueError("答卷扫描文件缺失，请重新上传答卷")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    adjustments = review.get("crop_adjustments") or {}
    regions = adjustments.get("regions") or {}
    objective = {str(item.get("question", "")): item for item in review.get("objective", [])}
    questions = {str(item.get("question", "")) for item in review.get("items", [])}
    mode = adjustments.get("material_mode") or "grouped_61_63"
    shift = float(adjustments.get("page1_program_y_pt") or 0)
    counts = {"objective": 0, "fill": 0}
    temporary = destination.with_suffix(".pdf.tmp")
    with fitz.open() as document:
        objective_layer = document.add_ocg("选择题叠加层", on=True)
        fill_layer = document.add_ocg("填空题扫描叠加层", on=True)
        for page_index, image in enumerate(images):
            page = document.new_page(width=PAGE_W, height=PAGE_H)
            ok, encoded = cv2.imencode(".png", image)
            if not ok:
                raise ValueError("答卷扫描图读取失败")
            page.insert_image(page.rect, stream=encoded.tobytes())
            if page_index == 0:
                page.insert_text((34, 20), "选择题叠加层（绿色：识别选项）", fontname="china-s",
                                 fontsize=8, color=OBJECTIVE_COLOR, oc=objective_layer)
                for question, region, left, center, right, first, spacing, choices in objective_layout():
                    if objective and question not in objective:
                        continue
                    correction = regions.get(region)
                    color = (0.85, 0.42, 0.06) if (correction or {}).get("status") == "待复核" else OBJECTIVE_COLOR
                    polygon = scan_polygon([(left, center-12), (right, center-12),
                                            (right, center+12), (left, center+12)], image, correction)
                    _box(page, polygon, color, objective_layer)
                    counts["objective"] += 1
                    selected = str(objective.get(question, {}).get("recognized") or "").upper()
                    for index, choice in enumerate(choices):
                        if choice not in selected:
                            continue
                        x = first + spacing * index
                        bubble = scan_polygon([(x-6, center-6), (x+6, center-6),
                                               (x+6, center+6), (x-6, center+6)], image, correction)
                        _box(page, bubble, color, objective_layer, fill=True)
            if any(info.get("status") == "待复核" for info in regions.values()):
                page.insert_text((34, 31), "橙框：定位待复核，请核对作答区域", fontname="china-s",
                                 fontsize=7, color=(0.85, 0.42, 0.06))
            page.insert_text((280, 20), "蓝实线：修正后识别区；虚线：AI扫描区", fontname="china-s",
                             fontsize=8, color=FILL_COLOR, oc=fill_layer)
            for target_page, rect, label, options in _subjective_crop_regions(mode, shift):
                item_question = "64" if label in {"64思路", "64代码"} else label
                if target_page != page_index or (questions and item_question not in questions):
                    continue
                correction = regions.get(options.get("region"))
                color = (0.85, 0.42, 0.06) if (correction or {}).get("status") == "待复核" else FILL_COLOR
                polygon = None
                for scan_rect, dashed in ((rect, False), (_expand_crop_rect(options.get("display_rect", rect)), True)):
                    x, y, width, height = scan_rect
                    top = PAGE_H-y-height
                    mapped = scan_polygon([(x, top), (x+width, top), (x+width, top+height),
                                          (x, top+height)], image, correction)
                    # _crop_aligned_rect clips the bounding rectangle to exact scan pixels.
                    sx, sy = image.shape[1]/PAGE_W, image.shape[0]/PAGE_H
                    left = max(0, np.floor(min(point.x for point in mapped)*sx)/sx)
                    right = min(PAGE_W, np.ceil(max(point.x for point in mapped)*sx)/sx)
                    upper = max(0, np.floor(min(point.y for point in mapped)*sy)/sy)
                    lower = min(PAGE_H, np.ceil(max(point.y for point in mapped)*sy)/sy)
                    polygon = [fitz.Point(left,upper),fitz.Point(right,upper),fitz.Point(right,lower),fitz.Point(left,lower)]
                    _box(page, polygon, color, fill_layer, dashed=dashed)
                # Compact badges stay in the left gutter, preserving the handwriting.
                left = min(point.x for point in polygon)
                top = min(point.y for point in polygon)
                page.insert_text((max(2, left-28), max(8, top+7)), label,
                                 fontname="china-s", fontsize=6, color=FILL_COLOR, oc=fill_layer)
                counts["fill"] += 1
        document.set_metadata({"title": "答卷扫描叠加 PDF", "subject": "选择题叠加层 + 填空题扫描叠加层；使用正式识别的页面配准与局部修正",
                               "creator": "OMRChecker"})
        document.save(str(temporary), garbage=4, deflate=True)
    temporary.replace(destination)
    return {"pages": len(images), "layers": ["选择题叠加层", "填空题扫描叠加层"], **counts}


def build_overlay_pdf(files, review, destination, reference_pdf=None):
    from exam_review import _load_page, prepare_card_alignment
    pages = []
    for path in files:
        loaded = _load_page(path)
        if not loaded:
            raise ValueError("答卷扫描文件读取失败：" + path.name)
        pages.extend(image for _name, image in loaded)
    if not pages:
        raise ValueError("答卷扫描文件缺失，请重新上传答卷")
    (normalized, _scores, _references, _region_images, regions,
     program_shift, _program_score) = prepare_card_alignment(pages, reference_pdf)
    corrected = {**review, "crop_adjustments": {
        **(review.get("crop_adjustments") or {}), "regions": regions,
        "page1_program_y_pt": 0.0 if regions.get("program", {}).get("status") == "已校正" else -program_shift,
    }}
    return write_overlay_pdf(normalized, corrected, destination)


def build_upload_preview_pdf(files, template, destination):
    """Locate scan regions and read bubbles without handwriting OCR or grading."""
    from exam_review import (_load_page, prepare_card_alignment, _normalize_illumination,
                             _bubble_scores, _single_bubble_answer, _multiple_bubble_answer)
    pages = []
    for path in files:
        loaded = _load_page(path)
        if not loaded:
            raise ValueError("答卷扫描文件读取失败：" + path.name)
        pages.extend(image for _name, image in loaded)
    if not pages:
        raise ValueError("请添加答卷扫描文件")
    reference = template.get("reference_pdf")
    (normalized, _scores, _references, region_images, regions,
     program_shift, _program_score) = prepare_card_alignment(pages, reference)
    inks = {region: _normalize_illumination(region_images.get(region, normalized[0]))
            for region in ("single", "multiple_tf")}
    sx, sy = normalized[0].shape[1]/PAGE_W, normalized[0].shape[0]/PAGE_H
    objective = []
    for question, region, _left, center, _right, first, spacing, choices in objective_layout():
        kwargs = {"radius": 5} if choices == "TF" else {}
        scores = _bubble_scores(inks[region], first, PAGE_H-center, choices, sx, sy, spacing, **kwargs)
        answer = (_multiple_bubble_answer(scores) if 16 <= int(question) <= 20 else _single_bubble_answer(scores))
        objective.append({"question": question, "recognized": answer})
    review = {"objective": objective, "items": [], "crop_adjustments": {
        "regions": regions,
        "material_mode": template.get("material_mode") or "grouped_61_63",
        "page1_program_y_pt": 0.0 if regions.get("program", {}).get("status") == "已校正" else -program_shift,
    }}
    result = write_overlay_pdf(normalized, review, destination)
    warnings = []
    expected_pages = int(template.get("page_count") or 2)
    if len(pages) != expected_pages:
        warnings.append("当前答卷 {} 页，所选模板要求 {} 页".format(len(pages), expected_pages))
    labels = {"single": "单选题区", "multiple_tf": "多选/判断题区", "program": "程序填空区",
              "correction": "逻辑改错区", "material": "材料问答区"}
    pending = [labels[region] for region, info in regions.items() if info.get("status") == "待复核"]
    if pending:
        warnings.append("定位待复核：" + "、".join(pending))
    return {**result, "warnings": warnings}
