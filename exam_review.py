"""试卷内容提取、答题卡 OCR 与自动复核。"""

from __future__ import annotations

import difflib
import json
import math
import re
import threading
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import cv2
import fitz
import numpy as np

from ai_judge import _clean_visual_text, judge_handwritten_items
from answer_alignment import align_printed_region
from candidate_identity import IDENTITY_FIELDS, prepare_name_crop, name_fields

PAGE_W = 595.2756
PAGE_H = 841.8898
REFERENCE_PDF = Path(__file__).resolve().parent / "output" / "pdf" / "exam_16th_answer_card_unified_2026" / "第十六届软件方向二面试题A_B_C通用答题卡_定位标记版.pdf"
MARKER_ASSET = Path(__file__).resolve().parent / "output" / "pdf" / "exam_answer_cards" / "marker_version" / "omr_marker.jpg"
MARKER_INSET_PT = 26.0
PAPER_TYPE_OPTIONS = "ABC"
PAPER_TYPE_BUBBLE_X = 458.0
PAPER_TYPE_BUBBLE_Y = 259.0
PAPER_TYPE_BUBBLE_SPACING = 28.0
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
QUESTION_ORDER = [*(str(i) for i in range(31, 65))]
_OCR_THREAD_LOCAL = threading.local()


@dataclass
class ReviewItem:
    question: str
    source_content: str
    expected_answer: str
    recognized_text: str
    confidence: float
    auto_status: str
    manual_status: str = "待复核"
    manual_text: str = ""
    reason: str = ""


def _normalize(value):
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = value.replace("（", "(").replace("）", ")")
    value = value.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", "", value).strip()


def _xml_text(element):
    return "".join(element.itertext()).strip()


def extract_docx_content(path: Path):
    """读取 DOCX 的段落和表格文本，避免依赖 python-docx。"""
    with zipfile.ZipFile(path) as package:
        root = ET.fromstring(package.read("word/document.xml"))
    paragraphs = []
    tables = []
    body = root.find("w:body", NS)
    if body is None:
        return paragraphs, tables
    for child in body:
        if child.tag == f"{{{NS['w']}}}p":
            text = "".join(node.text or "" for node in child.findall(".//w:t", NS)).strip()
            if text:
                paragraphs.append(text)
        elif child.tag == f"{{{NS['w']}}}tbl":
            rows = []
            for row in child.findall("./w:tr", NS):
                cells = []
                for cell in row.findall("./w:tc", NS):
                    cell_text = "\n".join(
                        "".join(node.text or "" for node in paragraph.findall(".//w:t", NS)).strip()
                        for paragraph in cell.findall("./w:p", NS)
                    ).strip()
                    cells.append(cell_text)
                if any(cells):
                    rows.append(cells)
            if rows:
                tables.append(rows)
    return paragraphs, tables


def _find_source_content(paragraphs, tables, question):
    number = question.split("(", 1)[0]
    patterns = [
        re.compile(rf"(?:^|[^0-9]){re.escape(question)}(?:[^0-9]|$)"),
        re.compile(rf"(?:^|[^0-9]){re.escape(number)}[.、）)]"),
    ]
    matches = [text for text in paragraphs if any(pattern.search(text) for pattern in patterns)]
    placeholder = re.compile(rf"___\({re.escape(number)}\)___")
    for table in tables:
        joined = "\n".join(" | ".join(row) for row in table)
        if placeholder.search(joined) or any(pattern.search(joined) for pattern in patterns):
            matches.append(joined)
    if matches:
        return "\n".join(dict.fromkeys(matches))[:2400]
    return ""


def extract_exam_content(exam_docx: Path):
    paragraphs, tables = extract_docx_content(exam_docx)
    source = {}
    for question in QUESTION_ORDER:
        source[question] = _find_source_content(paragraphs, tables, question)
    return {
        "title": paragraphs[0] if paragraphs else exam_docx.stem,
        "paragraph_count": len(paragraphs),
        "table_count": len(tables),
        "source": source,
    }


def extract_answer_key(answer_docx: Path):
    _paragraphs, tables = extract_docx_content(answer_docx)
    answers = {}
    for table in tables:
        for row in table:
            for cell in row:
                text = " ".join(str(cell).split())
                match = re.match(r"^(\d+)\s*(?:\((\d+)\))?\s*(.*)$", text)
                if not match:
                    continue
                number, _subpart, answer = match.groups()
                key = number
                answer = re.sub(r"[（(]\d+分[）)]\s*$", "", answer).strip()
                if key not in answers:
                    answers[key] = answer
    return answers


def _answer_forms(value):
    normalized = _normalize(value).lower().replace("≠", "!=").replace("<>", "!=")
    normalized = re.sub(r"^\(\d+\)", "", normalized)
    forms = {normalized}
    forms.add(re.sub(r"[^a-z0-9_]+", "", normalized))
    if normalized.startswith("1%"):
        forms.add("i" + normalized[1:])
    return {form for form in forms if form}


def _similarity(expected, recognized):
    expected_forms = _answer_forms(expected)
    recognized_forms = _answer_forms(recognized)
    if not expected_forms or not recognized_forms:
        return 0.0
    return max(
        difflib.SequenceMatcher(None, left, right).ratio()
        for left in expected_forms
        for right in recognized_forms
    )


def _critical_operators(value):
    normalized = _normalize(value).replace("≠", "!=").replace("<>", "!=")
    return re.findall(r"!=|==|>=|<=|>>|<<|&&|\|\||\+\+|--|[+%*/<>-]", normalized)


def _code_tokens(value):
    normalized = _normalize(value).lower().replace("≠", "!=").replace("<>", "!=")
    return re.findall(
        r"[a-z_]\w*|\d+(?:\.\d+)?|!=|==|>=|<=|>>|<<|&&|\|\||\+\+|--|[+%*/<>=!&|^~?:\[\]()-]",
        normalized,
    )


def _looks_like_code(value):
    normalized = _normalize(value)
    has_identifier = bool(re.search(r"[A-Za-z_]\w*", normalized))
    has_code_syntax = bool(
        re.search(r"[\[\]()]", normalized)
        or re.search(r"!=|==|>=|<=|&&|\|\||[%*/+<>-]", normalized)
    )
    return has_identifier and has_code_syntax


def _is_correction_question(question):
    match = re.match(r"\s*(\d+)", str(question or ""))
    return bool(match and 46 <= int(match.group(1)) <= 60)


def _split_correction_answer(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    patterns = (
        r"^\s*第\s*(\d{1,3})\s*行\s*[.、:：)）-]?\s*(.+?)\s*$",
        r"^\s*[（(\[]?\s*(\d{1,3})\s*[）)\]]\s*[.、:：-]?\s*(.+?)\s*$",
        r"^\s*(\d{1,3})\s*[.、:：-]\s*(.+?)\s*$",
        r"^\s*(\d{1,3})\s+(.+?)\s*$",
    )
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.DOTALL)
        if match and match.group(2).strip():
            return int(match.group(1)), match.group(2).strip()
    return None, ""


def _split_correction_options(value):
    """拆分改错题参考答案的多个等价写法。"""
    return [part.strip() for part in re.split(r"\s*(?:或|或者|\bor\b)\s*", str(value or ""), flags=re.IGNORECASE) if part.strip()]


def _source_code_lines(source_content):
    """提取题干中的代码行，优先使用题干已有的行号。"""
    text = unicodedata.normalize("NFKC", str(source_content or "")).replace("\r\n", "\n").replace("\r", "\n")
    explicit = []
    for raw in text.split("\n"):
        line = raw.strip()
        match = re.match(r"^(\d{1,3})\s+(?![.、])(.+?)\s*$", line)
        if match:
            explicit.append((int(match.group(1)), match.group(2).strip()))
    if len(explicit) >= 2:
        return explicit

    lines = []
    started = False
    code_start = re.compile(
        r"^(?:#\s*(?:include|define)|(?:class|struct|namespace|template)\b|"
        r"(?:const\s+)?(?:void|bool|char|short|long|float|double|int|unsigned|signed)\b|"
        r"[A-Za-z_]\w*\s*\([^)]*\)\s*\{?)"
    )
    for raw in text.split("\n"):
        line = raw.strip()
        if not started:
            if not line or not code_start.match(line):
                continue
            started = True
        if line:
            lines.append((len(lines) + 1, line))
    return lines


def _line_match_score(expected, candidate):
    expected_text = _normalize(expected).lower()
    candidate_text = _normalize(candidate).lower()
    if not expected_text or not candidate_text:
        return 0.0
    char_score = difflib.SequenceMatcher(None, expected_text, candidate_text).ratio()
    expected_tokens = _code_tokens(expected)
    candidate_tokens = _code_tokens(candidate)
    token_score = difflib.SequenceMatcher(None, expected_tokens, candidate_tokens).ratio() if expected_tokens and candidate_tokens else 0.0
    shared_identifiers = set(re.findall(r"[a-z_]\w*", expected_text)) & set(re.findall(r"[a-z_]\w*", candidate_text))
    identifier_bonus = min(0.12, 0.03 * len(shared_identifiers))
    return min(1.0, 0.62 * char_score + 0.38 * token_score + identifier_bonus)


def _infer_correction_line(expected, source_content):
    """从题干代码中推导未标注行号的改错参考答案。"""
    line, content = _split_correction_answer(expected)
    if line is not None and content:
        return line
    if not content:
        content = str(expected or "").strip()
    source_lines = _source_code_lines(source_content)
    if not source_lines:
        return None
    options = _split_correction_options(content) or [content]
    ranked = []
    for source_line, source_text in source_lines:
        score = max(_line_match_score(option, source_text) for option in options)
        ranked.append((score, source_line))
    ranked.sort(reverse=True)
    if not ranked or ranked[0][0] < 0.45:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.025 and ranked[0][0] < 0.78:
        return None
    return ranked[0][1]


def _normalize_correction_expected(question, expected, source_content=""):
    """为改错参考答案补齐行号，保留原答案文本和等价写法。"""
    if not _is_correction_question(question):
        return expected
    line, content = _split_correction_answer(expected)
    if line is not None and content:
        return expected
    inferred_line = _infer_correction_line(expected, source_content)
    if inferred_line is None:
        return expected
    return "{}. {}".format(inferred_line, str(expected or "").strip())


def _judge_answer_content(expected, recognized, confidence):
    expected_forms = _answer_forms(expected)
    recognized_forms = _answer_forms(recognized)
    expected_operators = _critical_operators(expected)
    recognized_operators = _critical_operators(recognized)
    operators_match = expected_operators == recognized_operators
    contains = any(left in right for left in expected_forms for right in recognized_forms)
    similarity = _similarity(expected, recognized)
    code_answer = _looks_like_code(expected)
    code_tokens_match = _code_tokens(expected) == _code_tokens(recognized)
    if confidence < 0.65:
        return "需人工复核", "OCR置信度低于0.65"
    if code_answer and not code_tokens_match:
        return "不通过", "代码变量、下标、数值或运算符与参考答案不一致"
    if operators_match and (contains or similarity >= 0.90):
        return "自动通过", f"答案相似度{similarity:.2f}"
    if not operators_match:
        return "不通过" if code_answer else "需人工复核", "关键运算符与参考答案不一致"
    return "需人工复核", f"答案相似度{similarity:.2f}"


def judge_answer(expected, recognized, confidence, question=None):
    expected_norm = _normalize(expected)
    recognized_norm = _normalize(recognized)
    confidence = float(confidence or 0)
    if _is_correction_question(question) and not recognized_norm:
        return "不通过", "改错题未填写对应行号和改错内容"
    if not recognized_norm:
        return "待复核", "OCR没有提取到文字"
    if not expected_norm:
        return "需人工复核", "参考答案需要人工确认"
    if _is_correction_question(question):
        expected_line, expected_content = _split_correction_answer(expected)
        recognized_line, recognized_content = _split_correction_answer(recognized)
        if expected_line is None or not expected_content:
            return "需人工复核", "改错题参考答案需要同时提供行号和改错内容"
        if recognized_line is None:
            return "不通过", "改错题必须同时填写对应行号和改错内容"
        if recognized_line != expected_line:
            return "不通过", "改错题行号错误：应为第{}行，识别为第{}行".format(expected_line, recognized_line)
        if not recognized_content:
            return "不通过", "改错题缺少改错内容"
        results = [_judge_answer_content(option, recognized_content, confidence) for option in _split_correction_options(expected_content)]
        if any(status == "自动通过" for status, _reason in results):
            return next(result for result in results if result[0] == "自动通过")
        if results and all(status == "不通过" for status, _reason in results):
            return results[0]
        return next((result for result in results if result[0] == "需人工复核"), results[0] if results else ("需人工复核", "参考答案需要人工确认"))
    return _judge_answer_content(expected, recognized, confidence)


def _objective_expected(value):
    normalized = _normalize(value).upper()
    if normalized in {"对", "正确", "TRUE", "T"}:
        return "T"
    if normalized in {"错", "错误", "FALSE", "F"}:
        return "F"
    return normalized

def _load_page(path: Path):
    if path.suffix.lower() == ".pdf":
        document = fitz.open(str(path))
        pages = []
        try:
            for index, page in enumerate(document):
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csGRAY, alpha=False)
                image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width).copy()
                pages.append((f"{path.stem}_p{index + 1}", image))
        finally:
            document.close()
        return pages
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    return [(path.stem, image)] if image is not None else []


def _line_y(line, x):
    rho, theta = line
    sine = math.sin(theta)
    return None if abs(sine) < 1e-6 else (rho - x * math.cos(theta)) / sine


def _line_x(line, y):
    rho, theta = line
    cosine = math.cos(theta)
    return None if abs(cosine) < 1e-6 else (rho - y * math.sin(theta)) / cosine


def _intersection(first, second):
    first_coefficients = np.array([math.cos(first[1]), math.sin(first[1]), -first[0]], dtype=float)
    second_coefficients = np.array([math.cos(second[1]), math.sin(second[1]), -second[0]], dtype=float)
    point = np.cross(first_coefficients, second_coefficients)
    return None if abs(point[2]) < 1e-6 else point[:2] / point[2]


def _find_printed_border(image):
    height, width = image.shape[:2]
    normalized = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX)
    edges = cv2.Canny(cv2.GaussianBlur(normalized, (5, 5), 0), 50, 150)
    threshold = max(140, int(min(width, height) * 0.07))
    lines = cv2.HoughLines(edges, 1, np.pi / 1800, threshold)
    if lines is None:
        return None
    horizontal, vertical = [], []
    for rank, (rho, theta) in enumerate(np.asarray(lines).reshape(-1, 2)):
        line = (float(rho), float(theta))
        angle = math.degrees(theta)
        if abs(angle - 90) <= 8:
            position = _line_y(line, width / 2)
            if position is not None:
                horizontal.append((rank, position, line))
        if angle <= 8 or angle >= 172:
            position = _line_x(line, height / 2)
            if position is not None:
                vertical.append((rank, position, line))
    top = [item for item in horizontal if 0.02 * height <= item[1] <= 0.20 * height]
    bottom = [item for item in horizontal if item[0] < 900 and 0.68 * height <= item[1] <= 0.99 * height]
    left = [item for item in vertical if 0.02 * width <= item[1] <= 0.30 * width]
    right = [item for item in vertical if 0.70 * width <= item[1] <= 0.99 * width]
    if not all((top, bottom, left, right)):
        return None
    selected = [
        min(top, key=lambda item: item[0])[2],
        min(right, key=lambda item: item[0])[2],
        max(bottom, key=lambda item: item[1])[2],
        min(left, key=lambda item: item[0])[2],
    ]
    corners = [
        _intersection(selected[0], selected[3]),
        _intersection(selected[0], selected[1]),
        _intersection(selected[2], selected[1]),
        _intersection(selected[2], selected[3]),
    ]
    if any(point is None for point in corners):
        return None
    return np.asarray(corners, dtype=np.float32)


MARKER_BOX_INSET_PT = 17.0
MARKER_SIZE_PT = 18.0


def _order_marker_centers(points):
    """将四个定位标记中心统一为左上、右上、右下、左下。"""
    points = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    if len(points) != 4:
        return None
    sums = points.sum(axis=1)
    diffs = points[:, 1] - points[:, 0]
    ordered = np.asarray([
        points[np.argmin(sums)],
        points[np.argmin(diffs)],
        points[np.argmax(sums)],
        points[np.argmax(diffs)],
    ], dtype=np.float32)
    if len({tuple(np.round(point, 2)) for point in ordered}) != 4:
        return None
    return ordered


def _target_marker_centers(target_w, target_h):
    """返回定位标记图案中心的目标像素坐标，保留整张纸和完整四角标记。"""
    scale_x = target_w / PAGE_W
    scale_y = target_h / PAGE_H
    center_x = (MARKER_BOX_INSET_PT + MARKER_SIZE_PT / 2) * scale_x
    center_y = (MARKER_BOX_INSET_PT + MARKER_SIZE_PT / 2) * scale_y
    return np.asarray([
        [center_x, center_y],
        [target_w - center_x, center_y],
        [target_w - center_x, target_h - center_y],
        [center_x, target_h - center_y],
    ], dtype=np.float32)


def _warp_marker_page(gray):
    """通过四个定位标记直接校正整张纸，保留完整页边和四角标记。"""
    if not MARKER_ASSET.is_file():
        return None
    import src.template

    target_w, target_h = int(PAGE_W * 2), int(PAGE_H * 2)
    configuration = SimpleNamespace(
        dimensions=SimpleNamespace(processing_width=target_w),
        outputs=SimpleNamespace(show_image_level=0),
    )
    image_ops = SimpleNamespace(tuning_config=configuration, append_save_img=lambda *args, **kwargs: None)
    crop_class = src.template.PROCESSOR_MANAGER.processors["CropOnMarkers"]
    target_centers = _target_marker_centers(target_w, target_h)

    def try_align(candidate):
        processor = crop_class(
            options={
                "relativePath": MARKER_ASSET.name,
                "sheetToMarkerWidthRatio": 33,
                "marker_rescale_range": [55, 135],
                "marker_rescale_steps": 16,
                "min_matching_threshold": 0.55,
                "max_matching_variation": 0.35,
            },
            relative_dir=str(MARKER_ASSET.parent),
            image_instance_ops=image_ops,
        )
        # 处理器会在内部绘制调试框，因此使用副本检测，使用原图做最终透视变换。
        processor.apply_filter(candidate.copy(), "marker-version")
        centers = _order_marker_centers(getattr(processor, "marker_centers", []))
        score = processor.threshold_circles[-1] if processor.threshold_circles else 0.0
        if centers is None or score < 0.65:
            return None
        transform = cv2.getPerspectiveTransform(centers, target_centers)
        return cv2.warpPerspective(
            candidate, transform, (target_w, target_h),
            flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=255,
        )

    resized = cv2.resize(gray, (target_w, target_h), interpolation=cv2.INTER_AREA)
    aligned = try_align(resized)
    if aligned is not None:
        return aligned
    corners = _find_printed_border(gray)
    if corners is not None:
        destination = np.asarray(
            [[52, 56], [target_w - 52, 56], [target_w - 52, target_h - 56], [52, target_h - 56]],
            dtype=np.float32,
        )
        rough = cv2.warpPerspective(
            gray, cv2.getPerspectiveTransform(corners, destination),
            (target_w, target_h), borderValue=255,
        )
        aligned = try_align(rough)
        if aligned is not None:
            return aligned
    return None


def _warp_page(image):
    if image is None:
        return None
    target_w, target_h = int(PAGE_W * 2), int(PAGE_H * 2)
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    marker_aligned = _warp_marker_page(gray)
    if marker_aligned is not None:
        return marker_aligned
    if abs((gray.shape[1] / gray.shape[0]) - (PAGE_W / PAGE_H)) < 0.01:
        return cv2.resize(gray, (target_w, target_h), interpolation=cv2.INTER_AREA)
    corners = _find_printed_border(gray)
    if corners is None:
        return cv2.resize(gray, (target_w, target_h), interpolation=cv2.INTER_AREA)
    destination = np.asarray(
        [[52, 56], [target_w - 52, 56], [target_w - 52, target_h - 56], [52, target_h - 56]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(corners, destination)
    return cv2.warpPerspective(gray, transform, (target_w, target_h), flags=cv2.INTER_CUBIC, borderValue=255)


def _reference_pages(reference_pdf=None):
    reference_path = Path(reference_pdf) if reference_pdf else REFERENCE_PDF
    if not reference_path.is_file():
        return []
    document = fitz.open(str(reference_path))
    pages = []
    try:
        for page in document:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csGRAY, alpha=False)
            pages.append(np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width).copy())
    finally:
        document.close()
    return pages


def _feature_align(image, reference):
    orb = cv2.ORB_create(16000)
    source_keypoints, source_descriptors = orb.detectAndCompute(image, None)
    reference_keypoints, reference_descriptors = orb.detectAndCompute(reference, None)
    if source_descriptors is None or reference_descriptors is None:
        return image, 0
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(source_descriptors, reference_descriptors, k=2)
    good = [first for first, second in matches if first.distance < 0.78 * second.distance]
    if len(good) < 12:
        return image, 0
    source_points = np.float32([source_keypoints[item.queryIdx].pt for item in good])
    reference_points = np.float32([reference_keypoints[item.trainIdx].pt for item in good])
    homography, mask = cv2.findHomography(source_points, reference_points, cv2.RANSAC, 3.0)
    if homography is None or mask is None:
        return image, 0
    aligned = cv2.warpPerspective(image, homography, (reference.shape[1], reference.shape[0]), borderValue=255)
    return aligned, int(mask.sum())


def _align_and_order_pages(images, reference_pdf=None):
    rough_pages = [_warp_page(image) for image in images]
    references = _reference_pages(reference_pdf) if reference_pdf else _reference_pages()
    if len(references) < 2 or len(rough_pages) < 2:
        return rough_pages, []
    candidates = []
    for page_index, image in enumerate(rough_pages):
        row = []
        for reference_index, reference in enumerate(references[:2]):
            aligned, score = _feature_align(image, reference)
            row.append((aligned, score, page_index, reference_index))
        candidates.append(row)
    direct_score = candidates[0][0][1] + candidates[1][1][1]
    swapped_score = candidates[0][1][1] + candidates[1][0][1]
    selected = [candidates[0][0][0], candidates[1][1][0]] if direct_score >= swapped_score else [candidates[1][0][0], candidates[0][1][0]]
    scores = [direct_score, swapped_score]
    return selected + rough_pages[2:], scores

def _crop_pdf_rect(image, x, y, width, height):
    scale_x = image.shape[1] / PAGE_W
    scale_y = image.shape[0] / PAGE_H
    left = max(0, round(x * scale_x))
    right = min(image.shape[1], round((x + width) * scale_x))
    top = max(0, round((PAGE_H - y - height) * scale_y))
    bottom = min(image.shape[0], round((PAGE_H - y) * scale_y))
    return image[top:bottom, left:right].copy()


def _crop_aligned_rect(image, rect, matrix=None):
    """把参考框映射回扫描图，直接截取原像素，保留细小标点笔画。"""
    if matrix is None:
        return _crop_pdf_rect(image, *rect)
    x, y, width, height = rect
    sx, sy = image.shape[1] / PAGE_W, image.shape[0] / PAGE_H
    top = PAGE_H - y - height
    corners = np.float32([[x * sx, top * sy], [(x + width) * sx, top * sy],
                           [(x + width) * sx, (top + height) * sy], [x * sx, (top + height) * sy]])
    mapped = cv2.transform(corners[None], np.asarray(matrix, dtype=np.float64))[0]
    left, upper = np.floor(mapped.min(axis=0)).astype(int)
    right, lower = np.ceil(mapped.max(axis=0)).astype(int)
    return image[max(0, upper):min(image.shape[0], lower), max(0, left):min(image.shape[1], right)].copy()


def _estimate_local_vertical_shift(image, reference, top=590.0, bottom=790.0,
                                   left=35.0, right=570.0, max_shift=40.0,
                                   min_score=0.025, max_adjustment=24.0):
    """根据答题卡底部印刷线条估算局部纵向形变，返回顶部坐标方向的偏移量。"""
    if image is None or reference is None or image.size == 0 or reference.size == 0:
        return 0.0, 0.0

    image_gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    reference_gray = reference if reference.ndim == 2 else cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    scale_x = image_gray.shape[1] / PAGE_W
    scale_y = image_gray.shape[0] / PAGE_H
    reference_scale_x = reference_gray.shape[1] / PAGE_W
    reference_scale_y = reference_gray.shape[0] / PAGE_H

    search_left = max(0, round(left * scale_x))
    search_right = min(image_gray.shape[1], round(right * scale_x))
    search_top = max(0, round((top - max_shift) * scale_y))
    search_bottom = min(image_gray.shape[0], round((bottom + max_shift) * scale_y))
    template_left = max(0, round(left * reference_scale_x))
    template_right = min(reference_gray.shape[1], round(right * reference_scale_x))
    template_top = max(0, round(top * reference_scale_y))
    template_bottom = min(reference_gray.shape[0], round(bottom * reference_scale_y))

    search = image_gray[search_top:search_bottom, search_left:search_right]
    template = reference_gray[template_top:template_bottom, template_left:template_right]
    if search.size == 0 or template.size == 0 or search.shape[0] < template.shape[0]:
        return 0.0, 0.0
    if search.shape[1] != template.shape[1]:
        search = cv2.resize(search, (template.shape[1], search.shape[0]), interpolation=cv2.INTER_AREA)

    search_edges = cv2.Canny(cv2.GaussianBlur(search, (5, 5), 0), 60, 160)
    template_edges = cv2.Canny(cv2.GaussianBlur(template, (5, 5), 0), 60, 160)
    if cv2.countNonZero(template_edges) < 50:
        return 0.0, 0.0

    response = cv2.matchTemplate(search_edges, template_edges, cv2.TM_CCOEFF_NORMED)
    _minimum, score, _minimum_location, best_location = cv2.minMaxLoc(response)
    expected_top = round(top * scale_y) - search_top
    shift_points = (best_location[1] - expected_top) / scale_y
    if not math.isfinite(score) or score < min_score:
        return 0.0, float(score) if math.isfinite(score) else 0.0
    shift_points = max(-max_adjustment, min(max_adjustment, shift_points))
    return float(shift_points), float(score)


def _split_handwriting_lines(image):
    """将大题答题框按实际墨迹拆成文本行，保留原图供人工与视觉审核。"""
    if image is None or image.size == 0:
        return []
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = (_normalize_illumination(gray) < 145).astype(np.uint8)
    mask[:2, :] = mask[-2:, :] = 0
    mask[:, :2] = mask[:, -2:] = 0
    count, components, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    clean = np.zeros_like(mask)
    for index in range(1, count):
        x, y, width, height, area = stats[index]
        if area >= 8 and height >= 3 and not (width > gray.shape[1] * 0.6 and height < 5):
            clean[components == index] = 1
    rows = np.flatnonzero(clean.sum(axis=1) >= max(2, gray.shape[1] * 0.004))
    if not len(rows):
        return []
    groups = np.split(rows, np.where(np.diff(rows) > 3)[0] + 1)
    lines = []
    for group in groups:
        top, bottom = int(group[0]), int(group[-1]) + 1
        ys, xs = np.where(clean[top:bottom] > 0)
        if bottom - top < 6 or len(xs) < 20:
            continue
        left, right = max(0, int(xs.min()) - 6), min(gray.shape[1], int(xs.max()) + 7)
        lines.append(gray[max(0, top - 5):min(gray.shape[0], bottom + 5), left:right].copy())
    return lines


def _recognize_crops(crops, labels):
    if not crops:
        return []
    try:
        from src.ocr import create_text_recognizer
        params = SimpleNamespace(
            enabled=True,
            provider="paddleocr",
            model_name="PP-OCRv6_medium_rec",
            device="cpu",
            batch_size=7,
            upscale=3.0,
            padding_x=24,
            padding_y=16,
            trim_whitespace=True,
            field_charsets={},
            skip_model_source_check=True,
        )
        recognizer = getattr(_OCR_THREAD_LOCAL, "recognizer", None)
        if recognizer is None:
            recognizer = create_text_recognizer(params)
            _OCR_THREAD_LOCAL.recognizer = recognizer
        batch_images, batch_labels, spans = [], [], []
        for crop, label in zip(crops, labels):
            lines = _split_handwriting_lines(crop) if label in {"64思路", "64代码"} else [crop]
            start = len(batch_images)
            batch_images.extend(lines)
            batch_labels.extend([label] * len(lines))
            spans.append((start, len(batch_images)))
        predictions = recognizer.recognize(batch_images, batch_labels) if batch_images else []
        results = []
        for start, end in spans:
            parts = predictions[start:end]
            texts = [str(part.text).strip() for part in parts if str(part.text).strip()]
            scores = [float(part.confidence) for part in parts if str(part.text).strip()]
            errors = [str(part.error) for part in parts if getattr(part, "error", None)]
            results.append(SimpleNamespace(text="\n".join(texts), confidence=min(scores, default=0.0),
                                           error="; ".join(errors) if errors else None))
        return results
    except Exception as error:
        return [SimpleNamespace(text="", confidence=0.0, error=str(error)) for _ in crops]


def _clean_ocr_text(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ""
    if "\ufffd" in text:
        return ""
    if all(character in "_-—=|·.,，。:：/\\一" for character in text):
        return ""
    return text


def _dark_ratio(image, center_x, center_y, radius=5):
    x1, x2 = max(0, int(center_x - radius)), min(image.shape[1], int(center_x + radius + 1))
    y1, y2 = max(0, int(center_y - radius)), min(image.shape[0], int(center_y + radius + 1))
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return 0.0
    return float(np.mean(crop < 150))


def _normalize_illumination(image):
    background = cv2.GaussianBlur(image, (0, 0), 25)
    return cv2.divide(image, background, scale=255)


def _handwriting_ratio(crop, reference):
    if reference is None or crop.size == 0:
        return 1.0
    if reference.shape != crop.shape:
        reference = cv2.resize(reference, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_AREA)
    current = _normalize_illumination(crop).astype(np.int16)
    blank = _normalize_illumination(reference).astype(np.int16)
    return float(np.mean((blank - current) > 20))


def _cluster_values(values, tolerance):
    groups = []
    for value in sorted(values):
        if not groups or value - float(np.mean(groups[-1])) > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return groups


def _read_student_id(image, ink_image):
    left, top, right, bottom = 50, 300, min(800, image.shape[1]), min(560, image.shape[0])
    region = image[top:bottom, left:right]
    circles = cv2.HoughCircles(
        cv2.GaussianBlur(region, (5, 5), 1),
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=12,
        param1=100,
        param2=18,
        minRadius=4,
        maxRadius=10,
    )
    if circles is None:
        return ""
    circles = np.round(circles[0]).astype(int)
    x_groups = [group for group in _cluster_values(circles[:, 0], 10) if len(group) >= 7]
    y_groups = [group for group in _cluster_values(circles[:, 1], 8) if len(group) >= 7]
    if len(x_groups) != 12 or len(y_groups) != 10:
        return ""
    x_centers = [float(np.mean(group)) + left for group in x_groups]
    y_centers = [float(np.mean(group)) + top for group in y_groups]
    digits = []
    for center_x in x_centers:
        scores = [_dark_ratio(ink_image, center_x, center_y, 5) for center_y in y_centers]
        ordered = sorted(range(10), key=lambda index: scores[index], reverse=True)
        if scores[ordered[0]] < 0.25 or scores[ordered[0]] - scores[ordered[1]] < 0.10:
            return ""
        digits.append(str(ordered[0]))
    return "".join(digits)


def _bubble_scores(image, x, y, choices, scale_x, scale_y, spacing, radius=6):
    scores = []
    for index, choice in enumerate(choices):
        center_x = (x + index * spacing) * scale_x
        center_y = (PAGE_H - y) * scale_y
        scores.append((choice, _dark_ratio(image, center_x, center_y, radius)))
    return scores


def _single_bubble_answer(scores):
    ordered = sorted(scores, key=lambda item: item[1], reverse=True)
    top, second = ordered[0], ordered[1]
    # 多处实心填涂、弱笔迹与分数接近的选项统一交给人工确认。
    if second[1] >= 0.45 or top[1] < 0.25 or top[1] - second[1] < 0.08:
        return ""
    return top[0]


def _read_paper_type(ink_image, scale_x, scale_y):
    scores = _bubble_scores(
        ink_image,
        PAPER_TYPE_BUBBLE_X,
        PAGE_H - PAPER_TYPE_BUBBLE_Y,
        PAPER_TYPE_OPTIONS,
        scale_x,
        scale_y,
        PAPER_TYPE_BUBBLE_SPACING,
        radius=5,
    )
    return _single_bubble_answer(scores)


def _multiple_bubble_answer(scores):
    ordered_values = sorted(score for _choice, score in scores)
    gaps = [ordered_values[index + 1] - ordered_values[index] for index in range(len(ordered_values) - 1)]
    split = max(range(len(gaps)), key=gaps.__getitem__)
    if gaps[split] < 0.18:
        return ""
    threshold = (ordered_values[split] + ordered_values[split + 1]) / 2
    return "".join(choice for choice, score in scores if score > threshold)

def _save_objective_crops(image, destination, region_images=None):
    """Save the bubble row for each objective answer so a reviewer can inspect the ink."""
    folder = Path(destination) / "objective"
    folder.mkdir(parents=True, exist_ok=True)
    sx, sy = image.shape[1] / PAGE_W, image.shape[0] / PAGE_H
    paths = {}
    regions = []
    for index in range(15):
        col, row = divmod(index, 5)
        regions.append((str(index + 1), [52, 226, 400][col] - 13,
                        348 + row * 20.5, [52, 226, 400][col] + 139))
    for row, number in enumerate(range(16, 21)):
        regions.append((str(number), 49, 503 + row * 20.5, 208))
    for index, number in enumerate(range(21, 31)):
        col, row = divmod(index, 5)
        left = 337 + col * 112
        regions.append((str(number), left - 13, 503 + row * 20.5, left + 97))
    for question, left, top, right in regions:
        x1, x2 = max(0, round(left * sx)), min(image.shape[1], round(right * sx))
        y1, y2 = max(0, round((top - 12) * sy)), min(image.shape[0], round((top + 12) * sy))
        aligned_image = (region_images or {}).get("single" if int(question) <= 15 else "multiple_tf", image)
        crop = aligned_image[y1:y2, x1:x2]
        if crop.size:
            encoded, buffer = cv2.imencode(".png", crop)
            if encoded:
                path = folder / ("q{}.png".format(question.zfill(2)))
                path.write_bytes(buffer.tobytes())
                paths[question] = str(path)
    return paths


def extract_answer_card(card_paths, image_dir=None, template_config=None):
    template_config = dict(template_config or {})
    material_mode = str(template_config.get("material_mode") or "grouped_61_63")
    answer_card_layout = str(template_config.get("layout") or "16th_abc_61_63_fill_64_algorithm")
    pages = []
    for path in card_paths:
        pages.extend(_load_page(Path(path)))
    if not pages:
        raise ValueError("答题卡图片无法读取")
    reference_pdf = template_config.get("reference_pdf")
    page_images = [image for _name, image in pages]
    normalized, alignment_scores = (
        _align_and_order_pages(page_images, reference_pdf=reference_pdf)
        if reference_pdf else _align_and_order_pages(page_images)
    )
    references = _reference_pages(reference_pdf) if reference_pdf else _reference_pages()
    region_images, region_alignment = {}, {}
    program_shift = program_match_score = 0.0
    if normalized and references:
        program_shift, program_match_score = _estimate_local_vertical_shift(normalized[0], references[0])
    for region, page_index in (("single", 0), ("multiple_tf", 0), ("program", 0), ("correction", 1), ("material", 1)):
        if page_index < len(normalized) and page_index < len(references):
            initial_y = program_shift if region == "program" else 0.0
            if region == "material" and region_alignment.get("correction", {}).get("matrix"):
                matrix = np.asarray(region_alignment["correction"]["matrix"])
                sx = normalized[page_index].shape[1] / PAGE_W
                sy = normalized[page_index].shape[0] / PAGE_H
                center = np.asarray([55 * sx, 660 * sy])
                initial_y = float((matrix[:, :2] @ center + matrix[:, 2] - center)[1] / sy)
            region_images[region], region_alignment[region] = align_printed_region(
                normalized[page_index], references[page_index], region, initial_shift_y=initial_y)
    objective = {}
    objective_flags = {}
    if normalized:
        image = normalized[0]
        ink_image = _normalize_illumination(image)
        sx, sy = image.shape[1] / PAGE_W, image.shape[0] / PAGE_H
        student_id = _read_student_id(image, ink_image)
        paper_type = _read_paper_type(ink_image, sx, sy)
        single_ink = _normalize_illumination(region_images.get("single", image))
        other_ink = _normalize_illumination(region_images.get("multiple_tf", image))
        columns = [52, 226, 400]
        for index in range(15):
            col, row = divmod(index, 5)
            scores = _bubble_scores(single_ink, columns[col] + 28, PAGE_H - 348 - row * 20.5, "ABCD", sx, sy, 27)
            objective[str(index + 1)] = _single_bubble_answer(scores)
            if sum(score >= 0.45 for _choice, score in scores) > 1:
                objective_flags[str(index + 1)] = "单选题出现多处填涂，请核对扫描图"
        for row, number in enumerate(range(16, 21)):
            scores = _bubble_scores(other_ink, 62 + 28, PAGE_H - 503 - row * 20.5, "ABCD", sx, sy, 29)
            objective[str(number)] = _multiple_bubble_answer(scores)
        for idx, number in enumerate(range(21, 31)):
            col, row = divmod(idx, 5)
            scores = _bubble_scores(other_ink, 337 + col * 112 + 28, PAGE_H - 503 - row * 20.5, "TF", sx, sy, 37, radius=5)
            objective[str(number)] = _single_bubble_answer(scores)
            if sum(score >= 0.45 for _choice, score in scores) > 1:
                objective_flags[str(number)] = "判断题出现多处填涂，请核对扫描图"
        for question in objective:
            region = "single" if int(question) <= 15 else "multiple_tf"
            if region_alignment.get(region, {}).get("status") == "待复核":
                objective_flags[question] = "题号定位证据不足，请核对扫描图"

    crops = []
    display_crops = []
    labels = []
    ink_ratios = []
    def append_crop(page_index, x, y, width, height, label, display_rect=None, reference_rect=None, region=None):
        matrix = region_alignment.get(region, {}).get("matrix")
        crop_image = normalized[page_index]
        crop = _crop_aligned_rect(crop_image, (x, y, width, height), matrix)
        reference_coordinates = reference_rect or (x, y, width, height)
        reference = _crop_pdf_rect(references[page_index], *reference_coordinates) if page_index < len(references) else None
        display_crop = _crop_aligned_rect(crop_image, display_rect, matrix) if display_rect else crop
        # 明显缩放/剪切时恢复标准字形；轻微偏移直接使用原扫描像素。
        distortion = float(np.max(np.abs(np.asarray(matrix)[:, :2] - np.eye(2)))) if matrix is not None else 0.0
        ocr_crop = _crop_pdf_rect(region_images[region], x, y, width, height) if distortion > 0.02 else crop
        if region == "program" and matrix is not None:
            # 小于 3 pt 的局部残差保留已有采样网格，避免微小标点在再次采样后变形。
            sx, sy = crop_image.shape[1] / PAGE_W, crop_image.shape[0] / PAGE_H
            top = PAGE_H - y - height
            points = np.float32([[x*sx, top*sy], [(x+width)*sx, top*sy],
                                  [x*sx, (top+height)*sy], [(x+width)*sx, (top+height)*sy]])
            residual = cv2.transform(points[None], np.asarray(matrix))[0] - points - [0, program_shift * sy]
            if np.max(np.abs(residual / [sx, sy])) <= 3.0:
                ocr_crop = _crop_pdf_rect(crop_image, x, y - program_shift, width, height)
        crops.append(ocr_crop)
        display_crops.append(display_crop)
        labels.append(label)
        ink_ratios.append(_handwriting_ratio(crop, reference))

    program_adjust = 0.0 if region_alignment.get("program", {}).get("status") == "已校正" else -program_shift
    if region_alignment:
        print("[试卷复核] 题号局部配准：" + "; ".join(
            "{}={}/{}点".format(name, item["status"], item["inliers"])
            for name, item in region_alignment.items()), flush=True)

    if len(normalized) >= 1:
        for idx, number in enumerate(range(31, 46)):
            col, row = divmod(idx, 5)
            x, base_y = 51 + col * 174 + 11, 184 - row * 29
            y = base_y + program_adjust
            label = str(number)
            reference_rect = (x, base_y - 4, 153, 22)
            if label == "39":
                # 39 的笔迹包含覆盖改写，扩大显示区域；OCR仍使用原答题框，避免引入相邻题号。
                append_crop(0, x, y - 4, 153, 22, label,
                            display_rect=(x - 3, y - 6, 156, 26), reference_rect=reference_rect, region="program")
            else:
                append_crop(0, x, y - 4, 153, 22, label, reference_rect=reference_rect, region="program")
    if len(normalized) >= 2:
        for idx, number in enumerate(range(46, 61)):
            y = PAGE_H - 205 - idx * 23.5
            append_crop(1, 63, y - 5, 224, 19, str(number), region="correction")
        if material_mode == "legacy_subfields":
            for idx, label in enumerate(("61(1)", "61(2)", "62(1)", "62(2)", "63(1)", "63(2)", "63(3)")):
                y = 253 - idx * 26
                append_crop(1, 85, y - 5, 202, 20, label, region="material")
            append_crop(1, 315, PAGE_H - 338, PAGE_W - 355, 101, "64思路")
            append_crop(1, 315, 57, PAGE_W - 355, PAGE_H - 373 - 57, "64代码")
        else:
            # 第十六届第六部分：61—63各一个填空框，64为整栏算法题。
            for idx, number in enumerate(range(61, 64)):
                top = 574 + idx * 21.5
                y = PAGE_H - top - 17.5
                append_crop(1, 63, y, 224, 17.5, str(number), region="material")
            append_crop(1, 326, PAGE_H - 484, 216, 260, "64思路")
            append_crop(1, 326, PAGE_H - 770, 216, 276, "64代码")
    objective_images = _save_objective_crops(normalized[0], image_dir, region_images) if image_dir is not None and normalized else {}
    if image_dir is not None and normalized:
        from objective_view import build_objective_view
        build_objective_view(normalized[0], Path(image_dir) / "objective_view", region_alignment, PAGE_W, PAGE_H)
    crop_paths = {}
    if image_dir is not None:
        destination = Path(image_dir)
        destination.mkdir(parents=True, exist_ok=True)
        for display_crop, label in zip(display_crops, labels):
            filename = {"64思路": "64_thought", "64代码": "64_code"}.get(
                label, re.sub(r"[^0-9A-Za-z_-]", "_", label))
            path = destination / (filename + ".png")
            # 把细小的手写笔画放大后提交视觉模型。
            enlarged = cv2.resize(display_crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            encoded, buffer = cv2.imencode(".png", enlarged)
            if encoded:
                path.write_bytes(buffer.tobytes())
                crop_paths[label] = str(path)
    name_crop, name_ratio = prepare_name_crop(normalized[0], image_dir)
    has_name_ink = name_ratio >= 0.006
    results = _recognize_crops(crops + ([name_crop] if has_name_ink else []),
                               labels + (["姓名"] if has_name_ink else []))
    identity = name_fields(results.pop() if has_name_ink else None, name_ratio)
    text_fields = {}
    errors = []
    for label, result, ink_ratio in zip(labels, results, ink_ratios):
        recognized = _clean_ocr_text(getattr(result, "text", "")) if ink_ratio >= 0.008 else ""
        confidence = float(getattr(result, "confidence", 0.0)) if recognized else 0.0
        text_fields[label] = {"text": recognized, "confidence": confidence, "ink_ratio": ink_ratio}
        if getattr(result, "error", None):
            errors.append(str(result.error))
    long_parts = [text_fields.get("64思路", {}), text_fields.get("64代码", {})]
    long_texts = [part.get("text", "") for part in long_parts if part.get("text", "")]
    long_confidences = [part.get("confidence", 0.0) for part in long_parts if part.get("text", "")]
    text_fields["64"] = {"text": "\n".join(long_texts), "confidence": max(long_confidences, default=0.0)}
    paper_type = paper_type if normalized else ""
    return {
        "student_id": student_id if normalized else "",
        **identity,
        "paper_type": paper_type,
        "paper_type_status": "自动识别" if paper_type else "待复核",
        "objective": objective,
        "objective_flags": objective_flags,
        "text_fields": text_fields,
        "ocr_errors": list(dict.fromkeys(errors)),
        "alignment_scores": alignment_scores,
        "crop_adjustments": {
            "page1_program_y_pt": round(program_adjust, 2),
            "page1_program_match_score": round(program_match_score, 4),
            "regions": region_alignment,
            "version": 4,
            "answer_card_layout": answer_card_layout,
            "material_mode": material_mode,
        },
        "crop_paths": crop_paths,
        "objective_images": objective_images,
    }


def _objective_match(question, answer, expected):
    if not answer or not expected:
        return False
    if 16 <= int(question) <= 20:
        return "".join(sorted(set(answer))) == "".join(sorted(set(expected)))
    return answer == expected


def _objective_recognition_label(marked, recognition_warning):
    """Return a reviewer-facing label that preserves ambiguous-ink state."""
    warning = str(recognition_warning or "")
    if "多处填涂" in warning:
        return "多处填涂"
    if warning:
        return "无法确定"
    if marked:
        return marked
    return "空白"


def _normalize_objective_correction(question, value):
    answer = _normalize(value).upper()
    number = int(question)
    if not answer:
        return ""
    if 1 <= number <= 15 and re.fullmatch(r"[A-D]", answer):
        return answer
    if 16 <= number <= 20 and re.fullmatch(r"[A-D]{1,4}", answer) and len(set(answer)) == len(answer):
        return "".join(sorted(answer))
    if 21 <= number <= 30 and answer in {"T", "F", "对", "错", "正确", "错误"}:
        return _objective_expected(answer)
    raise ValueError("第{}题修正答案格式错误".format(question))


def _objective_summary(items):
    return {
        "total": len(items),
        "auto_pass": sum(item.get("auto_status") == "自动通过" for item in items),
        "reviewed": sum(bool(item.get("manual_status") or item.get("override_answer")) for item in items),
        "corrected": sum(bool(item.get("override_answer")) for item in items),
        "final_pass": sum(item.get("final_status", item.get("auto_status")) in {"自动通过", "通过"} for item in items),
        "final_fail": sum(item.get("final_status") == "不通过" for item in items),
        "pending": sum(item.get("final_status", item.get("auto_status")) in {"空白", "待复核", "需人工复核"} for item in items),
    }


def _score_number(value):
    rounded = round(float(value or 0), 2)
    return int(rounded) if rounded.is_integer() else rounded


def _structured_question_labels(question):
    """返回答题卡题号，用于把外部题目 ID 关联到本地题号。"""
    title = str(question.get("title") or "")
    match = re.match(r"\s*(\d+)\s*[-~至–—]\s*(\d+)", title)
    if match:
        first, last = map(int, match.groups())
        return [str(number) for number in range(first, last + 1)]
    match = re.match(r"\s*(\d+)", title)
    if match:
        return [match.group(1)]
    return []


def _structured_question_metadata(exam):
    metadata = {}
    for section in exam.get("sections", []):
        section_id = section.get("id")
        session_id = section.get("session_id")
        for question in section.get("questions", []):
            question_id = question.get("id")
            question_metadata = {
                "id": question_id,
                "session_id": question.get("session_id", session_id),
                "section_id": question.get("section_id", section_id),
            }
            keys = list(question.get("question_ids", []))
            keys.extend(_structured_question_labels(question))
            if question_id is not None:
                keys.append(question_id)
            for key in keys:
                if key not in (None, ""):
                    metadata[str(key)] = dict(question_metadata)
    return metadata


def _structured_alias_maps(exam, source_map, answer_map):
    """为 Scout 原生题目 ID补充答题卡题号别名。"""
    aliases_source = dict(source_map or {})
    aliases_answer = dict(answer_map or {})
    for section in exam.get("sections", []):
        for question in section.get("questions", []):
            labels = _structured_question_labels(question)
            ids = [str(item) for item in question.get("question_ids", []) if str(item)]
            native_id = str(question.get("id") or "")
            source = next((aliases_source[key] for key in [native_id, *ids] if key in aliases_source), "")
            answer = next((aliases_answer[key] for key in [native_id, *ids] if key in aliases_answer), None)
            for label in labels:
                if source and label not in aliases_source:
                    aliases_source[label] = source
                if answer not in (None, "") and label not in aliases_answer:
                    aliases_answer[label] = answer
    return aliases_source, aliases_answer


def _structured_score_map(exam):
    scores = {}
    for section in exam.get("sections", []):
        for question in section.get("questions", []):
            ids = [str(item) for item in question.get("question_ids", [question.get("id", "")]) if str(item)]
            if not ids:
                continue
            total = max(0.0, float(question.get("score", 0) or 0))
            explicit = question.get("score_map") or question.get("sub_scores") or {}
            assigned = {}
            if isinstance(explicit, dict):
                for key in ids:
                    try:
                        value = float(explicit.get(key, 0) or 0)
                    except (TypeError, ValueError):
                        value = -1
                    if value >= 0 and key in explicit:
                        assigned[key] = value
            if assigned and len(assigned) == len(ids) and abs(sum(assigned.values()) - total) <= 0.01:
                scores.update({key: _score_number(value) for key, value in assigned.items()})
                for local_key in _structured_question_labels(question):
                    scores.setdefault(local_key, _score_number(total / len(ids)))
                continue
            share = total / len(ids)
            values = {key: _score_number(share) for key in ids}
            for local_key in _structured_question_labels(question):
                values.setdefault(local_key, _score_number(share))
            scores.update(values)
    return scores


def attach_structured_scores(review, exam):
    """Attach score fields to legacy review records and recalculate totals."""
    score_map = _structured_score_map(exam)
    for item in review.get("objective", []):
        item["score"] = _score_number(score_map.get(str(item.get("question", "")), 0))
    for item in review.get("items", []):
        item["score"] = _score_number(score_map.get(str(item.get("question", "")), 0))
    review["score_summary"] = _score_summary(review)
    return review


def _text_final_status(item):
    manual = item.get("manual_status")
    if manual in {"通过", "不通过"}:
        return manual
    ai_status = item.get("ai_status")
    if ai_status == "AI通过":
        return "通过"
    if ai_status == "AI不通过":
        return "不通过"
    if ai_status == "AI需复核":
        return "待复核"
    return item.get("auto_status", "待复核")


def _score_summary(review):
    pass_statuses = {"自动通过", "通过"}
    fail_statuses = {"不通过"}
    objective_score = objective_possible = text_score = text_possible = pending_score = failed_score = 0.0
    for item in review.get("objective", []):
        score = float(item.get("score", 0) or 0)
        status = item.get("final_status", item.get("auto_status", "待复核"))
        awarded = score if status in pass_statuses else 0.0
        item["awarded_score"] = _score_number(awarded)
        objective_score += awarded
        objective_possible += score
        if status not in pass_statuses | fail_statuses:
            pending_score += score
        elif status in fail_statuses:
            failed_score += score
    for item in review.get("items", []):
        score = float(item.get("score", 0) or 0)
        status = _text_final_status(item)
        awarded = score if status in pass_statuses else 0.0
        if item.get("manual_status") in {"通过", "不通过"}:
            score_basis = "人工复核"
        elif item.get("ai_status") in {"AI通过", "AI不通过", "AI需复核"}:
            score_basis = "AI审核"
        else:
            score_basis = "规则初判"
        item["final_status"] = status
        item["score_basis"] = score_basis
        item["awarded_score"] = _score_number(awarded)
        text_score += awarded
        text_possible += score
        if status not in pass_statuses | fail_statuses:
            pending_score += score
        elif status in fail_statuses:
            failed_score += score
    earned = objective_score + text_score
    possible = objective_possible + text_possible
    return {
        "total_score": _score_number(earned),
        "possible_score": _score_number(possible),
        "objective_score": _score_number(objective_score),
        "objective_possible": _score_number(objective_possible),
        "text_score": _score_number(text_score),
        "text_possible": _score_number(text_possible),
        "pending_score": _score_number(pending_score),
        "failed_score": _score_number(failed_score),
    }


def _review_summary(items):
    return {
        "total": len(items),
        "auto_pass": sum(item.get("auto_status") == "自动通过" for item in items),
        "manual": sum(item.get("auto_status") != "自动通过" for item in items),
        "ai_pass": sum(item.get("ai_status") == "AI通过" for item in items),
        "ai_fail": sum(item.get("ai_status") == "AI不通过" for item in items),
        "ai_review": sum(item.get("ai_status") == "AI需复核" for item in items),
    }


def refresh_rule_judgments(review):
    """Re-evaluate saved OCR results after rule changes and refresh totals."""
    changed = False
    eligible = False
    for item in review.get("items", []):
        if not any(key in item for key in ("expected_answer", "recognized_text", "auto_status")):
            continue
        eligible = True
        normalized_expected = _normalize_correction_expected(
            item.get("question"),
            item.get("expected_answer", ""),
            item.get("source_content", ""),
        )
        if item.get("expected_answer", "") != normalized_expected:
            item["expected_answer"] = normalized_expected
            changed = True
        status, reason = judge_answer(
            normalized_expected,
            item.get("recognized_text", ""),
            item.get("confidence", 0.0),
            question=item.get("question"),
        )
        if item.get("auto_status") != status or item.get("reason") != reason:
            item["auto_status"] = status
            item["reason"] = reason
            changed = True
    if not eligible:
        return False
    summary = _review_summary(review.get("items", []))
    scores = _score_summary(review)
    if review.get("review_summary") != summary:
        review["review_summary"] = summary
        changed = True
    if review.get("score_summary") != scores:
        review["score_summary"] = scores
        changed = True
    return changed


def apply_ai_review(review, progress_callback=None):
    if progress_callback is None:
        result = judge_handwritten_items(review.get("items", []))
    else:
        result = judge_handwritten_items(review.get("items", []), progress_callback=progress_callback)
    result_map = result.get("results", {})
    for item in review.get("items", []):
        ai_result = result_map.get(str(item.get("question", "")))
        if ai_result:
            item["ai_status"] = ai_result["status"]
            item["ai_confidence"] = ai_result["confidence"]
            item["ai_visual_text"] = _clean_visual_text(item.get("question"), ai_result.get("visual_text", ""))
            item["ai_reason"] = ai_result["reason"]
            item["ai_corrected_answer"] = ai_result["corrected_answer"]
            if item["ai_status"] == "AI通过" and _is_correction_question(item.get("question")):
                normalized_expected = _normalize_correction_expected(
                    item.get("question"),
                    item.get("expected_answer", ""),
                    item.get("source_content", ""),
                )
                item["expected_answer"] = normalized_expected
                visual_answer = item["ai_visual_text"] or item.get("recognized_text", "")
                strict_status, strict_reason = judge_answer(
                    normalized_expected, visual_answer,
                    max(float(item.get("confidence", 0) or 0), float(item.get("ai_confidence", 0) or 0)),
                    question=item.get("question"),
                )
                if strict_status != "自动通过":
                    item["ai_status"] = "AI不通过" if strict_status == "不通过" else "AI需复核"
                    item["ai_reason"] = strict_reason
        elif result["status"] == "未配置":
            item["ai_status"] = "AI待配置"
            item["ai_confidence"] = 0.0
            item["ai_visual_text"] = ""
            item["ai_reason"] = result["message"]
            item["ai_corrected_answer"] = ""
        elif result["status"] == "异常":
            item["ai_status"] = "AI异常"
            item["ai_confidence"] = 0.0
            item["ai_visual_text"] = ""
            item["ai_reason"] = result["message"]
            item["ai_corrected_answer"] = ""
        else:
            item["ai_status"] = "AI跳过"
            item["ai_confidence"] = 0.0
            item["ai_visual_text"] = ""
            item["ai_reason"] = result["message"]
            item["ai_corrected_answer"] = ""
    review["ai_judgment"] = {key: value for key, value in result.items() if key != "results"}
    review["review_summary"] = _review_summary(review.get("items", []))
    review["score_summary"] = _score_summary(review)
    return review


def _build_review_from_maps(source_map, answers, source_meta, card_paths, image_dir=None, group_map=None, score_map=None, template_config=None, card=None):
    if card is None:
        if template_config:
            card = extract_answer_card(card_paths, image_dir=image_dir, template_config=template_config)
        else:
            card = extract_answer_card(card_paths, image_dir=image_dir)
    question_order = [str(value) for value in (template_config or {}).get("question_order", QUESTION_ORDER)]
    items = []
    for question in question_order:
        field = card["text_fields"].get(question, {})
        recognized = field.get("text", "")
        confidence = field.get("confidence", 0.0)
        source_content = source_map.get(question, "")
        expected = _normalize_correction_expected(question, answers.get(question, ""), source_content)
        status, reason = judge_answer(expected, recognized, confidence, question=question)
        number = int(question.split("(", 1)[0])
        region = "program" if number <= 45 else "correction" if number <= 60 else "material" if number <= 63 else None
        crop_quality = card.get("crop_adjustments", {}).get("regions", {}).get(region, {})
        if crop_quality.get("status") == "待复核":
            status, reason = "需人工复核", "题号定位证据不足，请核对原扫描图"
        item = asdict(ReviewItem(
            question=question,
            source_content=source_content,
            expected_answer=expected,
            recognized_text=recognized,
            confidence=confidence,
            auto_status=status,
            reason=reason,
        ))
        group_key, group_label = (group_map or {}).get(question, (question.split("(", 1)[0], question.split("(", 1)[0]))
        item["ai_group"] = group_key
        item["major_question"] = group_label
        item["score"] = _score_number((score_map or {}).get(question, 0))
        image_labels = ("64思路", "64代码") if question == "64" else (question,)
        item["handwriting_images"] = [card["crop_paths"][label] for label in image_labels if label in card["crop_paths"]]
        items.append(item)
    objective = []
    for question in (str(i) for i in range(1, 31)):
        marked = card["objective"].get(question, "")
        expected = _objective_expected(answers.get(question, ""))
        recognition_warning = card.get("objective_flags", {}).get(question, "")
        recognized_label = _objective_recognition_label(marked, recognition_warning)
        if recognition_warning:
            status = "需人工复核"
        elif _objective_match(question, marked, expected):
            status = "自动通过"
        elif marked:
            status = "需人工复核"
        else:
            status = "空白"
        objective.append({
            "question": question, "recognized": marked, "expected": expected,
            "recognized_label": recognized_label,
            "source_content": source_map.get(question, ""),
            "recognition_warning": recognition_warning,
            "auto_status": status, "final_status": status,
            "final_answer": "" if recognition_warning else marked,
            "manual_status": "", "reviewed_answer": "", "override_answer": False,
            "score": _score_number((score_map or {}).get(question, 0)),
            "bubble_image": card.get("objective_images", {}).get(question, ""),
        })
    review = {
        "ok": True,
        "review_id": uuid.uuid4().hex[:12],
        "source": {**source_meta, "answer_count": len(answers)},
        "student_id": card.get("student_id", ""),
        **{key: card[key] for key in IDENTITY_FIELDS if key in card},
        "paper_type": card.get("paper_type", ""),
        "paper_type_status": card.get("paper_type_status", "待复核"),
        "objective": objective,
        "items": items,
        "ocr_errors": card["ocr_errors"],
        "alignment_scores": card.get("alignment_scores", []),
        "crop_adjustments": card.get("crop_adjustments", {}),
    }
    review["ai_judgment"] = {"status": "待运行", "enabled": False, "processed": 0, "message": "OCR与规则初判已完成"}
    for item in review["items"]:
        item.update({"ai_status": "AI待处理", "ai_confidence": 0.0, "ai_visual_text": "", "ai_reason": "", "ai_corrected_answer": ""})
    review["review_summary"] = _review_summary(review["items"])
    review["objective_summary"] = _objective_summary(review["objective"])
    review["score_summary"] = _score_summary(review)
    return review


def build_review(exam_docx: Path, answer_docx: Path, card_paths, image_dir=None, template_config=None):
    exam = extract_exam_content(exam_docx)
    answers = extract_answer_key(answer_docx)
    return _build_review_from_maps(
        exam["source"],
        answers,
        {"exam": exam["title"], "paragraph_count": exam["paragraph_count"], "table_count": exam["table_count"]},
        card_paths,
        image_dir=image_dir,
        template_config=template_config,
    )


def _paper_type_key(value):
    text = str(value or "").strip().upper()
    match = re.search(r"(?:卷|TYPE|PAPER)?\s*([ABC])(?:卷)?$", text)
    return match.group(1) if match else (text if text in {"A", "B", "C"} else "")


def _select_import_variant(imported, paper_type):
    variants = imported.get("paper_variants") or {}
    if not variants:
        return imported, "", "单套试卷答案"
    key = _paper_type_key(paper_type)
    if key and key in variants:
        return variants[key], key, "已按答题卡标记选择{}卷答案".format(key)
    return {"exam": imported.get("exam", {}), "answer_map": {}, "source_map": {}, "summary": imported.get("summary", {})}, "", "未识别试卷类型，暂不套用 A/B/C 答案"


def _import_variant_for_review(imported, answer_paper_type):
    """恢复历史复核时，按已保存的卷型取回对应题目与分值。"""
    variants = imported.get("paper_variants") or {}
    key = _paper_type_key(answer_paper_type)
    if key and key in variants:
        return variants[key]
    return imported

def build_review_from_structured(exam_text, answer_text, card_paths, image_dir=None, answer_docx=None, template_config=None):
    from exam_import import import_exam_and_answers

    imported = import_exam_and_answers(exam_text, answer_text, answer_docx=answer_docx)
    if template_config:
        card = extract_answer_card(card_paths, image_dir=image_dir, template_config=template_config)
    else:
        card = extract_answer_card(card_paths, image_dir=image_dir)
    selected, selected_type, selection_message = _select_import_variant(imported, card.get("paper_type", ""))
    summary = selected.get("summary", {})
    group_map = {}
    score_map = _structured_score_map(selected.get("exam", {}))
    for section_index, section in enumerate(selected.get("exam", {}).get("sections", [])):
        for question_index, question in enumerate(section["questions"]):
            group_key = "{}:{}".format(section_index + 1, question_index + 1)
            group_label = str(question.get("id") or question.get("title") or question_index + 1)
            for subquestion in question.get("question_ids", [group_label]):
                group_map[str(subquestion)] = (group_key, group_label)
    source_map, answer_map = _structured_alias_maps(
        selected.get("exam", {}), selected.get("source_map", {}), selected.get("answer_map", {})
    )
    report = _build_review_from_maps(
        source_map,
        answer_map,
        {"exam": "结构化试卷", "section_count": summary.get("section_count", 0), "question_count": summary.get("question_count", 0), "total_score": summary.get("total_score", 0)},
        card_paths,
        image_dir=image_dir,
        group_map=group_map,
        score_map=score_map,
        template_config=template_config,
        card=card,
    )
    metadata = _structured_question_metadata(selected.get("exam", {}))
    for item in report.get("objective", []) + report.get("items", []):
        item.update(metadata.get(str(item.get("question", "")), {}))
    session_ids = [
        item.get("session_id") for item in metadata.values()
        if item.get("session_id") not in (None, "")
    ]
    if session_ids:
        report["session_id"] = session_ids[0]
    report["paper_type"] = card.get("paper_type", "")
    report["paper_type_status"] = card.get("paper_type_status", "待复核")
    report["answer_paper_type"] = selected_type
    report["answer_selection_status"] = selection_message
    report["available_paper_types"] = list((imported.get("paper_variants") or {}).keys())
    return report


def apply_manual_review(review, decisions, objective_decisions=None):
    decision_map = {str(item.get("question")): item for item in decisions or []}
    for item in review.get("items", []):
        decision = decision_map.get(item["question"])
        if not decision:
            continue
        manual_status = str(decision.get("status") or "待复核")
        manual_text = str(decision.get("text") or "")
        normalized_expected = _normalize_correction_expected(
            item.get("question"),
            item.get("expected_answer", ""),
            item.get("source_content", ""),
        )
        item["expected_answer"] = normalized_expected
        if manual_status == "通过" and _is_correction_question(item.get("question")) and manual_text.strip():
            strict_status, strict_reason = judge_answer(
                normalized_expected, manual_text, 1.0,
                question=item.get("question"),
            )
            if strict_status != "自动通过":
                raise ValueError("第{}题改错答案未通过格式与内容校验：{}".format(item["question"], strict_reason))
        item["manual_status"] = manual_status
        item["manual_text"] = manual_text
    objective_map = {str(item.get("question")): item for item in objective_decisions or []}
    known = {item["question"] for item in review.get("objective", [])}
    if set(objective_map) - known:
        raise ValueError("客观题题号无效")
    for item in review.get("objective", []):
        decision = objective_map.get(item["question"])
        if decision is None:
            continue
        status = str(decision.get("status") or "").strip()
        if status not in {"", "通过", "不通过", "待复核"}:
            raise ValueError("第{}题复核结论无效".format(item["question"]))
        override = decision.get("override_answer") is True
        corrected = _normalize_objective_correction(item["question"], decision.get("answer", "")) if override else ""
        final_answer = corrected if override else item.get("recognized", "")
        expected = item.get("expected", "")
        if override and not status:
            status = "待复核" if not expected else ("通过" if _objective_match(item["question"], corrected, expected) else "不通过")
        item["override_answer"] = override
        item["reviewed_answer"] = corrected
        item["manual_status"] = status
        item["final_answer"] = final_answer
        item["final_status"] = status or item.get("auto_status", "待复核")
    review["objective_summary"] = _objective_summary(review.get("objective", []))
    review["score_summary"] = _score_summary(review)
    review["manual_saved"] = True
    return review
