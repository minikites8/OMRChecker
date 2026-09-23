import json
import zipfile

import fitz
import pytest

from sheet_designer import generate_sheet_package, normalize_sheet_spec


def test_normalize_sheet_spec_accepts_builder_values():
    spec = normalize_sheet_spec(
        {
            "title": "期中答题卡",
            "id_digits": "9",
            "mcq_count": "4",
            "mcq_choices": "5",
            "tf_count": "3",
            "text_count": "2",
            "include_name": True,
            "include_written_id": False,
        }
    )

    assert spec.title == "期中答题卡"
    assert spec.id_digits == 9
    assert spec.mcq_count == 4
    assert spec.mcq_choices == 5
    assert spec.include_written_id is False


def test_normalize_sheet_spec_requires_one_question_type():
    with pytest.raises(ValueError, match="至少保留一种题型"):
        normalize_sheet_spec(
            {"mcq_count": 0, "tf_count": 0, "text_count": 0}
        )


def test_generate_sheet_package_creates_pdf_template_and_bundle(tmp_path):
    result = generate_sheet_package(
        {
            "title": "数学练习答题卡",
            "subtitle": "请使用黑色笔清晰填涂",
            "id_digits": 8,
            "mcq_count": 4,
            "mcq_choices": 4,
            "tf_count": 3,
            "text_count": 2,
            "include_name": True,
            "include_written_id": True,
        },
        output_root=tmp_path,
    )

    for key in (
        "pdf_path",
        "template_path",
        "reference_path",
        "package_path",
    ):
        assert result[key].is_file()

    document = fitz.open(result["pdf_path"])
    try:
        assert document.page_count == 1
        page = document[0]
        assert round(page.rect.width) == 612
        assert round(page.rect.height) == 792
        text = page.get_text()
        assert "学号、姓名、选择题、判断题与书写题" not in text
        assert "每条横线填写一个答案。" not in text
        assert "请按100%比例打印" not in text
        assert "由 OMRChecker 网页设计器生成" not in text
        assert len(text.splitlines()) > 25
    finally:
        document.close()

    template = json.loads(result["template_path"].read_text(encoding="utf-8"))
    assert template["pageDimensions"] == [2550, 3300]
    assert template["fieldBlocks"]["MCQ"]["fieldLabels"] == ["q1..4"]
    assert template["fieldBlocks"]["TrueFalse"]["fieldLabels"] == ["judge1..3"]
    assert template["fieldBlocks"]["HandwrittenFill"]["fieldLabels"] == ["text1..2"]
    assert template["fieldBlocks"]["Name"]["origin"] == [1600, 430]
    assert template["fieldBlocks"]["Name"]["bubbleDimensions"] == [650, 85]
    assert template["fieldBlocks"]["StudentIDWritten"]["origin"] == [1600, 630]
    assert template["fieldBlocks"]["StudentIDWritten"]["bubbleDimensions"] == [650, 85]
    assert len(template["customLabels"]["student_id"]) == 8

    pixmap = fitz.Pixmap(result["reference_path"])
    assert pixmap.width == 2550
    assert pixmap.height == 3300
    with zipfile.ZipFile(result["package_path"]) as archive:
        assert set(archive.namelist()) == {
            "omr_custom_sheet.pdf",
            "template.json",
            "reference_blank.png",
            "config.json",
            "sheet_spec.json",
        }


def test_twelve_digit_student_id_uses_compact_columns(tmp_path):
    result = generate_sheet_package(
        {
            "title": "十二位学号答题卡",
            "id_digits": 12,
            "mcq_count": 1,
            "tf_count": 1,
            "text_count": 1,
        },
        output_root=tmp_path,
    )
    template = json.loads(result["template_path"].read_text(encoding="utf-8"))
    student = template["fieldBlocks"]["StudentID"]
    assert student["origin"] == [170, 450]
    assert student["labelsGap"] == 105
    assert len(template["customLabels"]["student_id"]) == 12
