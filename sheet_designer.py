"""生成可打印并兼容 OMRChecker 的答题卡。"""

import json
import re
import shutil
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import fitz
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

PAGE_WIDTH_PX = 2550
PAGE_HEIGHT_PX = 3300
DPI = 300
PX_TO_PT = 72.0 / DPI
PAGE_SIZE = letter
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "output" / "pdf" / "web_designer"


@dataclass(frozen=True)
class SheetSpec:
    title: str = "OMR答题卡"
    subtitle: str = "请完整填涂答案，并在横线上清晰书写"
    id_digits: int = 8
    mcq_count: int = 5
    mcq_choices: int = 4
    tf_count: int = 5
    text_count: int = 5
    include_name: bool = True
    include_written_id: bool = True
    include_paper_type: bool = True


def _bounded_int(value, field, minimum, maximum, default):
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} 需要填写整数") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{field} 范围为 {minimum} 到 {maximum}")
    return parsed


def _clean_text(value, default, maximum):
    text = re.sub(r"[\x00-\x1f]", " ", str(value or "")).strip()
    return (text or default)[:maximum]


def normalize_sheet_spec(payload):
    payload = payload or {}
    spec = SheetSpec(
        title=_clean_text(payload.get("title"), SheetSpec.title, 48),
        subtitle=_clean_text(payload.get("subtitle"), SheetSpec.subtitle, 120),
        id_digits=_bounded_int(payload.get("id_digits"), "学号位数", 4, 12, 8),
        mcq_count=_bounded_int(payload.get("mcq_count"), "选择题数量", 0, 5, 5),
        mcq_choices=_bounded_int(payload.get("mcq_choices"), "选择题选项数", 2, 5, 4),
        tf_count=_bounded_int(payload.get("tf_count"), "判断题数量", 0, 5, 5),
        text_count=_bounded_int(payload.get("text_count"), "填空题数量", 0, 5, 5),
        include_name=bool(payload.get("include_name", True)),
        include_written_id=bool(payload.get("include_written_id", True)),
        include_paper_type=bool(payload.get("include_paper_type", True)),
    )
    if spec.mcq_count + spec.tf_count + spec.text_count == 0:
        raise ValueError("至少保留一种题型")
    return spec


def _register_fonts():
    try:
        pdfmetrics.getFont("STSong-Light")
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def _contains_cjk(text):
    return any("\u2e80" <= character <= "\u9fff" for character in text)


def _font_for(text, bold=False):
    if _contains_cjk(text):
        return "STSong-Light"
    return "Helvetica-Bold" if bold else "Helvetica"


def _px_x(value):
    return value * PX_TO_PT


def _px_y(value):
    return PAGE_SIZE[1] - value * PX_TO_PT


def _fit_font_size(text, maximum_size, maximum_width, bold=False):
    font = _font_for(text, bold)
    size = maximum_size
    while size > 7 and pdfmetrics.stringWidth(text, font, size) > maximum_width:
        size -= 0.5
    return font, size


def _draw_centered_text(pdf, text, y_px, size, bold=False, color=black):
    font, fitted = _fit_font_size(text, size, PAGE_SIZE[0] - 72, bold)
    pdf.setFillColor(color)
    pdf.setFont(font, fitted)
    pdf.drawCentredString(PAGE_SIZE[0] / 2, _px_y(y_px), text)


def _draw_label(pdf, text, x_px, y_px, size=9, bold=False, color=black):
    pdf.setFillColor(color)
    pdf.setFont(_font_for(text, bold), size)
    pdf.drawString(_px_x(x_px), _px_y(y_px), text)


def _draw_circle(pdf, x_px, y_px, radius_px=13):
    pdf.setStrokeColor(HexColor("#303638"))
    pdf.setLineWidth(0.28)
    pdf.circle(_px_x(x_px), _px_y(y_px), _px_x(radius_px), stroke=1, fill=0)


def _draw_line(pdf, x1_px, y1_px, x2_px, y2_px, width=1.0, color=black):
    pdf.setStrokeColor(color)
    pdf.setLineWidth(width)
    pdf.line(_px_x(x1_px), _px_y(y1_px), _px_x(x2_px), _px_y(y2_px))


def _draw_header(pdf, spec):
    _draw_centered_text(pdf, spec.title, 105, 20, bold=True, color=HexColor("#162f34"))
    _draw_centered_text(pdf, spec.subtitle, 160, 8.5, color=HexColor("#3e4d50"))
    _draw_label(pdf, "第1页 / 共1页", 2245, 205, 7.5, color=HexColor("#3e4d50"))


def _draw_identity(pdf, spec):
    _draw_label(pdf, f"学号（{spec.id_digits}位）", 220, 360, 10, bold=True)
    origin_x = 220 if spec.id_digits <= 8 else 170
    labels_gap = 155 if spec.id_digits <= 8 else 105
    origin_y = 450
    for column in range(spec.id_digits):
        x = origin_x + column * labels_gap
        _draw_label(pdf, str(column + 1), x + 3, origin_y - 20, 7.5, bold=True)
        for value in range(10):
            y = origin_y + value * 45 + 16
            _draw_circle(pdf, x + 16, y)
            _draw_label(pdf, str(value), x + 39, y + 4, 6.8)

    _draw_line(pdf, 1400, 230, 1400, 950, 1.0, HexColor("#27383b"))
    if spec.include_paper_type:
        _draw_label(pdf, "试卷类型（请填涂）", 1450, 320, 10, bold=True)
        for index, value in enumerate(("A", "B", "C")):
            x = 1650 + index * 210
            _draw_circle(pdf, x, 356, radius_px=14)
            _draw_label(pdf, value, x + 28, 360, 9, bold=True)
    if spec.include_name:
        _draw_label(pdf, "姓名：", 1450, 520, 10, bold=True)
        _draw_line(pdf, 1600, 525, 2250, 525, 1.05)
    if spec.include_written_id:
        _draw_label(pdf, "学号：", 1450, 720, 10, bold=True)
        _draw_line(pdf, 1600, 725, 2250, 725, 1.05)
    _draw_line(pdf, 120, 950, 2430, 950, 1.0, HexColor("#27383b"))


def _draw_objective(pdf, spec):
    if spec.mcq_count:
        _draw_label(pdf, f"选择题（A-{chr(64 + spec.mcq_choices)}）", 220, 1020, 10, bold=True)
        values = [chr(65 + index) for index in range(spec.mcq_choices)]
        for row in range(spec.mcq_count):
            y = 1066 + row * 105
            _draw_label(pdf, f"{row + 1:02d}", 185, y + 4, 7)
            for column, value in enumerate(values):
                x = 220 + column * 72
                _draw_circle(pdf, x + 16, y)
                _draw_label(pdf, value, x + 39, y + 4, 7)
    if spec.tf_count:
        _draw_label(pdf, "判断题（T/F）", 1450, 1020, 10, bold=True)
        for row in range(spec.tf_count):
            y = 1066 + row * 125
            _draw_label(pdf, f"{spec.mcq_count + row + 1:02d}", 1400, y + 4, 7)
            for column, value in enumerate(("T", "F")):
                x = 1450 + column * 100
                _draw_circle(pdf, x + 16, y)
                _draw_label(pdf, value, x + 39, y + 4, 7)


def _draw_text_lines(pdf, spec):
    if not spec.text_count:
        return
    _draw_label(pdf, "书写题", 180, 1710, 10, bold=True)
    for row in range(spec.text_count):
        top_y = 1810 + row * 190
        _draw_label(pdf, f"第{row + 1}题", 180, top_y + 48, 7.5, bold=True)
        _draw_line(pdf, 300, top_y + 100, 2250, top_y + 100, 1.0)


def create_pdf(spec, pdf_path):
    _register_fonts()
    pdf = canvas.Canvas(str(pdf_path), pagesize=PAGE_SIZE, pageCompression=1)
    pdf.setTitle(spec.title)
    pdf.setAuthor("OMRChecker 网页设计器")
    pdf.setSubject("可打印光学答题卡")
    pdf.setFillColor(white)
    pdf.rect(0, 0, PAGE_SIZE[0], PAGE_SIZE[1], stroke=0, fill=1)
    pdf.setStrokeColor(HexColor("#26383b"))
    pdf.setLineWidth(0.9)
    pdf.rect(_px_x(120), _px_y(3020), _px_x(2310), _px_x(2790), stroke=1, fill=0)
    _draw_header(pdf, spec)
    _draw_identity(pdf, spec)
    _draw_objective(pdf, spec)
    _draw_text_lines(pdf, spec)
    pdf.showPage()
    pdf.save()


def build_template(spec, reference_name):
    field_blocks = {}
    output_columns = ["student_id"]
    if spec.include_paper_type:
        field_blocks["PaperType"] = {
            "bubbleValues": ["A", "B", "C"],
            "direction": "horizontal",
            "origin": [1636, 342],
            "bubblesGap": 210,
            "labelsGap": 240,
            "fieldLabels": ["paper_type"],
        }
        output_columns.append("paper_type")
    student_id_origin_x = 220 if spec.id_digits <= 8 else 170
    student_id_labels_gap = 155 if spec.id_digits <= 8 else 105
    field_blocks["StudentID"] = {
        "fieldType": "QTYPE_INT",
        "origin": [student_id_origin_x, 450],
        "bubblesGap": 45,
        "labelsGap": student_id_labels_gap,
        "fieldLabels": [f"sid1..{spec.id_digits}"],
    }
    if spec.include_name:
        field_blocks["Name"] = {
            "fieldType": "QTYPE_TEXT",
            "bubbleDimensions": [650, 85],
            "origin": [1600, 430],
            "bubblesGap": 0,
            "labelsGap": 0,
            "fieldLabels": ["name"],
        }
        output_columns.append("name")
    if spec.include_written_id:
        field_blocks["StudentIDWritten"] = {
            "fieldType": "QTYPE_TEXT",
            "bubbleDimensions": [650, 85],
            "origin": [1600, 630],
            "bubblesGap": 0,
            "labelsGap": 0,
            "fieldLabels": ["student_id_written"],
        }
        output_columns.append("student_id_written")
    if spec.mcq_count:
        field_blocks["MCQ"] = {
            "bubbleValues": [chr(65 + index) for index in range(spec.mcq_choices)],
            "direction": "horizontal",
            "origin": [220, 1050],
            "bubblesGap": 72,
            "labelsGap": 105,
            "fieldLabels": [f"q1..{spec.mcq_count}"],
        }
        output_columns.extend(f"q{index}" for index in range(1, spec.mcq_count + 1))
    if spec.tf_count:
        field_blocks["TrueFalse"] = {
            "bubbleValues": ["T", "F"],
            "direction": "horizontal",
            "origin": [1450, 1050],
            "bubblesGap": 100,
            "labelsGap": 125,
            "fieldLabels": [f"judge1..{spec.tf_count}"],
        }
        output_columns.extend(
            f"judge{index}" for index in range(1, spec.tf_count + 1)
        )
    if spec.text_count:
        field_blocks["HandwrittenFill"] = {
            "fieldType": "QTYPE_TEXT",
            "bubbleDimensions": [1950, 85],
            "origin": [300, 1810],
            "bubblesGap": 0,
            "labelsGap": 190,
            "fieldLabels": [f"text1..{spec.text_count}"],
        }
        output_columns.extend(
            f"text{index}" for index in range(1, spec.text_count + 1)
        )
    return {
        "pageDimensions": [PAGE_WIDTH_PX, PAGE_HEIGHT_PX],
        "bubbleDimensions": [32, 32],
        "preProcessors": [
            {
                "name": "AlignPageBorder",
                "options": {
                    "targetRect": [120, 230, 2430, 3020],
                    "topSearch": [0.055, 0.18],
                    "bottomSearch": [0.65, 0.94],
                    "leftSearch": [0.035, 0.30],
                    "rightSearch": [0.70, 0.995],
                    "houghThreshold": 250,
                    "maxBottomLineRank": 500,
                    "minAreaRatio": 0.25,
                    "disableAutoAlign": True,
                },
            },
            {
                "name": "FeatureBasedAlignment",
                "options": {
                    "reference": reference_name,
                    "maxFeatures": 10000,
                    "goodMatchPercent": 0.2,
                    "preserveForOCR": True,
                },
            },
        ],
        "fieldBlocks": field_blocks,
        "customLabels": {
            "student_id": [f"sid{index}" for index in range(1, spec.id_digits + 1)],
            "paper_type": ["paper_type"],
        },
        "outputColumns": output_columns,
        "emptyValue": "",
    }


def render_reference(pdf_path, png_path):
    document = fitz.open(str(pdf_path))
    try:
        page = document[0]
        matrix = fitz.Matrix(DPI / 72.0, DPI / 72.0)
        pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY, alpha=False)
        pixmap.save(str(png_path))
    finally:
        document.close()


def generate_sheet_package(payload, output_root=DEFAULT_OUTPUT_ROOT):
    spec = normalize_sheet_spec(payload)
    sheet_id = "{}-{}".format(
        datetime.now().strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6]
    )
    sheet_dir = Path(output_root) / sheet_id
    sheet_dir.mkdir(parents=True, exist_ok=False)
    base_name = "omr_custom_sheet"
    pdf_path = sheet_dir / f"{base_name}.pdf"
    template_path = sheet_dir / "template.json"
    reference_path = sheet_dir / "reference_blank.png"
    config_path = sheet_dir / "config.json"
    spec_path = sheet_dir / "sheet_spec.json"
    package_path = sheet_dir / f"{base_name}_package.zip"

    create_pdf(spec, pdf_path)
    render_reference(pdf_path, reference_path)
    template = build_template(spec, reference_path.name)
    template_path.write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    source_config = Path(__file__).resolve().parent / "inputs" / "phone_scan" / "config.json"
    shutil.copy2(source_config, config_path)
    spec_path.write_text(
        json.dumps(asdict(spec), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in (pdf_path, template_path, reference_path, config_path, spec_path):
            archive.write(path, path.name)
    return {
        "sheet_id": sheet_id,
        "directory": sheet_dir,
        "pdf_path": pdf_path,
        "template_path": template_path,
        "reference_path": reference_path,
        "package_path": package_path,
        "spec": asdict(spec),
    }
