"""Reference-style answer cards: one layout drives live previews, PDFs and OMR crops."""
from __future__ import annotations

import io
import json
import math
import re
import shutil
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import fitz
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

WIDTH, HEIGHT = A4
DPI = 300
SCALE = DPI / 72
MARGIN, GAP, BOTTOM = 44, 18, HEIGHT - 48
COLUMN = (WIDTH - 2 * MARGIN - GAP) / 2
MAX_QUESTIONS, MAX_SECTIONS, MAX_PAGES = 300, 24, 24
KINDS = {
    "single": "单项选择题", "multiple": "多项选择题", "judgment": "判断题",
    "fill": "填空题", "correction": "逻辑改错题", "material": "材料问答题", "essay": "解答 / 算法题",
}
OBJECTIVE = {"single", "multiple", "judgment"}
DEFAULT_SECTIONS = [
    {"kind": "single", "count": 15}, {"kind": "multiple", "count": 5},
    {"kind": "judgment", "count": 10}, {"kind": "fill", "title": "程序填空题", "count": 15},
    {"kind": "correction", "count": 15}, {"kind": "material", "count": 3},
    {"kind": "essay", "title": "算法题", "count": 1, "lines": 20},
]
PRESETS = {
    "software-16th-abc": {"name": "第十六届软件方向 A/B/C 通用答题卡", "sections": DEFAULT_SECTIONS},
    "software-15th-a": {"name": "第十五届软件方向 A 卷答题卡", "sections": [
        *DEFAULT_SECTIONS[:5],
        {"kind": "material", "count": 1, "subparts": 2},
        {"kind": "material", "count": 1, "subparts": 2},
        {"kind": "material", "count": 1, "subparts": 3},
        DEFAULT_SECTIONS[-1],
    ]},
}


def integer(value, label, low, high, default):
    if value is None:
        return default
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(f"{label}需要填写整数")
    value = int(value)
    if not low <= value <= high:
        raise ValueError(f"{label}范围为 {low} 到 {high}")
    return value


def clean(value, default, maximum):
    return (re.sub(r"[\x00-\x1f]", " ", str(value or "")).strip() or default)[:maximum]


@dataclass(frozen=True)
class CardSpec:
    title: str
    subtitle: str
    id_digits: int
    sections: tuple
    include_name: bool = True
    include_written_id: bool = True
    include_paper_type: bool = True
    preset: str = "software-16th-abc"


def normalize_card_spec(payload=None):
    payload = {} if payload is None else payload
    if not isinstance(payload, dict):
        raise ValueError("答题卡配置应为对象")
    preset_id = payload.get("preset", "software-16th-abc")
    if preset_id not in PRESETS:
        raise ValueError("请选择已有的答题卡版式")
    preset = PRESETS[preset_id]
    raw = payload.get("sections", preset["sections"])
    if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= MAX_SECTIONS:
        raise ValueError(f"请设置 1 到 {MAX_SECTIONS} 个题型区块")
    sections = []
    for item in raw:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            raise ValueError("请选择有效的题型")
        kind = item["kind"]
        sections.append({
            "kind": kind,
            "title": clean(item.get("title"), KINDS[kind], 24),
            "count": integer(item.get("count"), "题目数量", 1, MAX_QUESTIONS, 1),
            "choices": integer(item.get("choices"), "选项数量", 2, 5, 4),
            "lines": integer(item.get("lines"), "作答行数", 1, 20, 20 if kind == "essay" else 1),
            "subparts": integer(item.get("subparts"), "每题子空数", 1, 6, 1),
        })
    if sum(item["count"] for item in sections) > MAX_QUESTIONS:
        raise ValueError(f"题目总数最多 {MAX_QUESTIONS} 道")
    flags = {}
    for flag in ("include_name", "include_written_id", "include_paper_type"):
        value = payload.get(flag, True)
        if not isinstance(value, bool):
            raise ValueError("身份栏选项应为布尔值")
        flags[flag] = value
    return CardSpec(
        title=clean(payload.get("title"), preset["name"], 48),
        subtitle=clean(payload.get("subtitle"), "请完整填涂学号与客观题，按题号在指定区域作答", 120),
        id_digits=integer(payload.get("id_digits"), "学号位数", 6, 12, 12),
        sections=tuple(sections), preset=preset_id, **flags,
    )


def sheet_presets():
    return {"ok": True, "presets": [
        {"id": key, "name": value["name"], "spec": asdict(normalize_card_spec({"preset": key}))}
        for key, value in PRESETS.items()
    ], "kinds": KINDS, "limits": {"questions": MAX_QUESTIONS, "sections": MAX_SECTIONS, "pages": MAX_PAGES}}


def row_height(section):
    kind = section["kind"]
    if kind in OBJECTIVE:
        return 18
    if kind == "fill":
        return max(17, section["lines"] * 14 + 3) * section["subparts"]
    if kind == "correction":
        return max(25, section["lines"] * 14 + 8) * section["subparts"]
    if kind == "material":
        return max(40, section["lines"] * 14 + 18) * section["subparts"]
    return 30 + section["lines"] * 14


def build_layout(spec):
    pages = []
    page = None
    column = 0
    y = 300

    def new_page():
        nonlocal page, column, y
        if len(pages) >= MAX_PAGES:
            raise ValueError(f"当前组合超过 {MAX_PAGES} 页，请减少题目或作答行数")
        page = {"number": len(pages) + 1, "headings": [], "questions": []}
        pages.append(page)
        column, y = 0, 300 if len(pages) == 1 else 96

    def next_column():
        nonlocal column, y
        if column == 0:
            column, y = 1, 300 if len(pages) == 1 else 96
        else:
            new_page()

    new_page()
    number = 1
    for index, section in enumerate(spec.sections):
        height = row_height(section)
        if height + 24 > BOTTOM - 96:
            raise ValueError("单题作答区过高，请减少子空数或作答行数")
        remaining = section["count"]
        whole = remaining * height + 24
        capacity = BOTTOM - (300 if len(pages) == 1 else 96)
        if whole <= capacity and y + whole > BOTTOM:
            next_column()
        while remaining:
            available = int((BOTTOM - y - 24) // height)
            if available < 1:
                next_column()
                continue
            count = min(remaining, available)
            x = MARGIN + column * (COLUMN + GAP)
            page["headings"].append({"x": x, "y": y, "width": COLUMN,
                "title": f"{index + 1}. {section['title']}  {number}—{number + count - 1}",
                "continued": remaining != section["count"]})
            y += 24
            for offset in range(count):
                page["questions"].append({"number": number, "kind": section["kind"],
                    "x": x, "y": y, "width": COLUMN, "height": height,
                    "choices": section["choices"], "lines": section["lines"], "subparts": section["subparts"],
                    "section": index})
                number += 1
                y += height
            remaining -= count
            if remaining:
                next_column()
    return {"version": 2, "page_size": [WIDTH, HEIGHT], "dpi": DPI, "spec": asdict(spec),
            "question_count": number - 1, "page_count": len(pages), "pages": pages}


def fonts():
    try:
        pdfmetrics.getFont("STSong-Light")
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def pdf_bytes(layout):
    fonts()
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4, pageCompression=1)
    spec = layout["spec"]
    pdf.setTitle(spec["title"])
    pdf.setAuthor("OMRChecker 网页答题卡设计器")

    def text(value, x, y, size=8, max_width=None, center=False):
        value = str(value)
        font = "STSong-Light" if any(ord(c) > 255 for c in value) else "Helvetica"
        if max_width:
            size = min(size, max_width / max(pdfmetrics.stringWidth(value, font, size), 1) * size)
        pdf.setFillColor(HexColor("#243443"))
        pdf.setFont(font, size)
        (pdf.drawCentredString if center else pdf.drawString)(x, HEIGHT - y, value)

    def line(x1, y1, x2, y2, gray="#b1bdc8"):
        pdf.setStrokeColor(HexColor(gray)); pdf.setLineWidth(.45)
        pdf.line(x1, HEIGHT-y1, x2, HEIGHT-y2)

    def rect(x,y,w,h, fill=False):
        pdf.setStrokeColor(HexColor("#8d9aa7")); pdf.setLineWidth(.5)
        pdf.rect(x, HEIGHT-y-h, w, h, stroke=1, fill=int(fill))

    def bubble(x,y,label):
        pdf.setStrokeColor(HexColor("#344354")); pdf.setLineWidth(.6)
        pdf.circle(x, HEIGHT-y, 4, fill=0, stroke=1)
        text(label,x+6,y+2.3,6.8)

    for page in layout["pages"]:
        for x in (18, WIDTH-28):
            for y in (18, HEIGHT-28):
                pdf.setFillColor(HexColor("#000000")); pdf.rect(x,HEIGHT-y-10,10,10,stroke=0,fill=1)
                pdf.setFillColor(HexColor("#ffffff")); pdf.rect(x+3,HEIGHT-y-7,4,4,stroke=0,fill=1)
        text(spec["title"],WIDTH/2,40,14,WIDTH-110,True)
        # Wrap instructions without reducing legibility.
        subtitle=spec["subtitle"]
        for n in range(0,len(subtitle),65):
            text(subtitle[n:n+65],WIDTH/2,55+(n//65)*10,7,WIDTH-100,True)
        if page["number"] == 1:
            if spec["include_name"]:
                text("姓名：",44,94,9); line(76,97,240,97)
            if spec["include_written_id"]:
                text("手写学号：",44,115,9); line(94,118,285,118)
            if spec["include_paper_type"]:
                text("试卷类型",330,94,9)
                for i,label in enumerate("ABC"):
                    bubble(391+i*43,91,label)
            text(f"学号（{spec['id_digits']}位）",44,138,9)
            text("每列填涂一个数字",185,138,7)
            for i in range(spec["id_digits"]):
                x = 66+i*38
                text(i+1,x+3,151,6,center=True)
                for digit in range(10):
                    bubble(x,163+digit*12,str(digit))
            line(44,284,WIDTH-44,284)
        else:
            if spec["include_name"]:
                text("姓名：",44,80,8); line(74,83,220,83)
            if spec["include_written_id"]:
                text("学号：",290,80,8); line(320,83,WIDTH-44,83)
        for heading in page["headings"]:
            pdf.setFillColor(HexColor("#edf1f5"))
            pdf.rect(heading["x"],HEIGHT-heading["y"]-18,heading["width"],18,fill=1,stroke=0)
            label=heading["title"]+("（续）" if heading["continued"] else "")
            text(label,heading["x"]+5,heading["y"]+12,8.5,heading["width"]-10)
        for q in page["questions"]:
            x,y,w,h=q["x"],q["y"],q["width"],q["height"]
            if q["kind"] in OBJECTIVE:
                text(f"{q['number']:02d}",x+4,y+11,8)
                labels=["T", "F"] if q["kind"]=="judgment" else list("ABCDE"[:q["choices"]])
                for i,label in enumerate(labels):
                    bubble(x+39+i*35,y+9,label)
            elif q["kind"]=="essay":
                rect(x,y,w,h-5); text(f"{q['number']:02d}  解题思路与作答区",x+6,y+13,8)
                for line_index in range(q["lines"]):
                    yy=y+30+line_index*14
                    if yy<y+h-9: line(x+7,yy,x+w-7,yy,"#d6dde5")
            else:
                part_height=h/q["subparts"]
                for part in range(q["subparts"]):
                    yy=y+part*part_height
                    label=str(q["number"])+(f"({part+1})" if q["subparts"]>1 else "")
                    text(label,x+3,yy+min(13,part_height-4),7.5)
                    rect(x+32,yy+1,w-34,part_height-4)
                    for n in range(1,q["lines"]):
                        ly=yy+5+n*14
                        if ly<yy+part_height-4: line(x+36,ly,x+w-5,ly,"#d6dde5")
        text(f"第 {page['number']} 页 / 共 {layout['page_count']} 页",WIDTH-114,HEIGHT-24,7)
        text("A4 · 按实际尺寸打印",44,HEIGHT-24,7)
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def template_for_page(layout, page, reference="reference_blank.png"):
    blocks, columns, custom = {}, [], {}
    def field(name, x, y, w, h, **extra):
        blocks[name] = {"fieldType":"QTYPE_TEXT", "origin":[round(x*SCALE),round(y*SCALE)],
            "bubbleDimensions":[round(w*SCALE),round(h*SCALE)], "bubblesGap":0,"labelsGap":0,
            "fieldLabels":[name], **extra}
        columns.append(name)
    spec=layout["spec"]
    if page["number"]==1:
        blocks["StudentID"]={"fieldType":"QTYPE_INT","origin":[round(62*SCALE),round(159*SCALE)],
            "bubbleDimensions":[round(8*SCALE)]*2,"bubblesGap":12*SCALE,"labelsGap":38*SCALE,
            "fieldLabels":[f"sid1..{spec['id_digits']}"]}
        custom["student_id"]=[f"sid{i+1}" for i in range(spec["id_digits"])]
        columns.append("student_id")
        if spec["include_name"]: field("name",76,82,164,14)
        if spec["include_written_id"]: field("student_id_written",94,103,191,14)
        if spec["include_paper_type"]:
            blocks["PaperType"]={"bubbleValues":["A","B","C"],"direction":"horizontal",
                "origin":[round(387*SCALE),round(87*SCALE)],"bubbleDimensions":[round(8*SCALE)]*2,
                "bubblesGap":43*SCALE,"labelsGap":0,"fieldLabels":["paper_type"]}
            columns.append("paper_type")
    else:
        if spec["include_name"]: field("name",74,69,146,13)
        if spec["include_written_id"]: field("student_id_written",320,69,WIDTH-364,13)
    for q in page["questions"]:
        name=f"q{q['number']}"; x,y,w,h=q["x"],q["y"],q["width"],q["height"]
        if q["kind"] in OBJECTIVE:
            blocks[name]={"bubbleValues":["T","F"] if q["kind"]=="judgment" else list("ABCDE"[:q["choices"]]),
                "direction":"horizontal","origin":[round((x+35)*SCALE),round((y+5)*SCALE)],
                "bubbleDimensions":[round(8*SCALE)]*2,"bubblesGap":35*SCALE,"labelsGap":0,"fieldLabels":[name]}
            columns.append(name)
        elif q["kind"]=="essay":
            field(name,x+5,y+19,w-10,h-28)
        else:
            for part in range(q["subparts"]):
                field(name+(f"_{part+1}" if q["subparts"]>1 else ""),x+35,y+part*h/q["subparts"]+3,w-40,h/q["subparts"]-8)
    return {"pageDimensions":[math.ceil(WIDTH*SCALE),math.ceil(HEIGHT*SCALE)],
        "bubbleDimensions":[round(8*SCALE)]*2,
        "preProcessors":[{"name":"FeatureBasedAlignment","options":{"reference":reference,
            "maxFeatures":10000,"goodMatchPercent":.2,"preserveForOCR":True}}],
        "fieldBlocks":blocks,"customLabels":custom,"outputColumns":columns,"emptyValue":""}


def preview_sheet(payload):
    layout=build_layout(normalize_card_spec(payload))
    with fitz.open(stream=pdf_bytes(layout),filetype="pdf") as document:
        pages=[{"number":i+1,"svg":page.get_svg_image(text_as_path=True)} for i,page in enumerate(document)]
    return {"ok":True,"spec":layout["spec"],"page_count":layout["page_count"],
        "question_count":layout["question_count"],"pages":pages}


def generate_card_package(payload, output_root):
    layout=build_layout(normalize_card_spec(payload))
    data=pdf_bytes(layout)
    sheet_id=datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8]
    folder=Path(output_root)/sheet_id
    folder.mkdir(parents=True,exist_ok=False)
    pdf_path=folder/"omr_custom_sheet.pdf"; pdf_path.write_bytes(data)
    def write_json(path,value):
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    config=json.loads((Path(__file__).parent/"inputs/phone_scan/config.json").read_text(encoding="utf-8-sig"))
    config["dimensions"].update(processing_width=math.ceil(WIDTH*SCALE),processing_height=math.ceil(HEIGHT*SCALE),
        display_width=math.ceil(WIDTH*SCALE),display_height=math.ceil(HEIGHT*SCALE))
    config["outputs"]["filter_out_multimarked_files"]=False
    manifest=[]
    with fitz.open(stream=data,filetype="pdf") as document:
        for i,page in enumerate(document):
            directory=folder/"pages"/f"page_{i+1}"; directory.mkdir(parents=True)
            pixmap=page.get_pixmap(matrix=fitz.Matrix(SCALE,SCALE),colorspace=fitz.csGRAY,alpha=False)
            pixmap.save(str(directory/"reference_blank.png"))
            write_json(directory/"template.json",template_for_page(layout,layout["pages"][i]))
            write_json(directory/"config.json",config)
            manifest.append({"page":i+1,"directory":f"pages/page_{i+1}",
                "questions":[q["number"] for q in layout["pages"][i]["questions"]]})
    for name in ("template.json","config.json","reference_blank.png"):
        shutil.copy2(folder/"pages/page_1"/name,folder/name)
    write_json(folder/"layout.json",layout)
    write_json(folder/"sheet_spec.json",layout["spec"])
    write_json(folder/"manifest.json",{"page_count":layout["page_count"],"pages":manifest})
    (folder/"README.txt").write_text(
        "答题卡与扫描配置按同一版式生成。\n按实际尺寸打印 A4 PDF。\n"
        "pages/page_N 保存第 N 页的模板、参考图与配置；扫描时按页分组使用对应目录。\n"
        "根目录扫描配置对应第 1 页。题号与题型详见 layout.json。\n",encoding="utf-8")
    package_path=folder/"omr_custom_sheet_package.zip"
    with zipfile.ZipFile(package_path,"w",compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path != package_path: archive.write(path,path.relative_to(folder).as_posix())
    return {"sheet_id":sheet_id,"directory":folder,"pdf_path":pdf_path,"template_path":folder/"template.json",
        "reference_path":folder/"reference_blank.png","package_path":package_path,"spec":layout["spec"],
        "page_count":layout["page_count"],"question_count":layout["question_count"]}
