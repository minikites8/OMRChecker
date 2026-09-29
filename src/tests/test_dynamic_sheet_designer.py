import json
import zipfile

import fitz
import pytest

from sheet_designer import normalize_sheet_spec, generate_sheet_package


def test_default_matches_reference_card():
    spec = normalize_sheet_spec({})
    assert spec.id_digits == 12
    assert sum(section["count"] for section in spec.sections) == 64
    assert [section["kind"] for section in spec.sections] == ["single", "multiple", "judgment", "fill", "correction", "material", "essay"]


@pytest.mark.parametrize("digits", range(6, 13))
def test_supports_six_to_twelve_digits(digits):
    assert normalize_sheet_spec({"id_digits": digits}).id_digits == digits


@pytest.mark.parametrize("digits", [4, 5, 13, True, 6.5, "6.5"])
def test_rejects_invalid_student_id_length(digits):
    with pytest.raises(ValueError):
        normalize_sheet_spec({"id_digits": digits})


def test_combinations_preserve_order_and_duplicate_types():
    sections = [{"kind": "essay", "count": 2}, {"kind": "single", "count": 7}, {"kind": "essay", "count": 1}]
    spec = normalize_sheet_spec({"sections": sections})
    assert [(s["kind"], s["count"]) for s in spec.sections] == [("essay", 2), ("single", 7), ("essay", 1)]


@pytest.mark.parametrize("sections", [[], [{"kind": "unknown", "count": 3}], [{"kind": "single", "count": 1.5}], [{"kind": "single", "count": -1}], [{"kind": "single", "count": 301}], [{"kind": "fill", "count": 0}], "bad"])
def test_validates_sections(sections):
    with pytest.raises(ValueError):
        normalize_sheet_spec({"sections": sections})


def test_default_package_has_all_pages_and_numbered_fields(tmp_path):
    package = generate_sheet_package({}, output_root=tmp_path)
    with fitz.open(package["pdf_path"]) as document:
        assert document.page_count == 2
        assert round(document[0].rect.width) == 595
    layout = json.loads((package["directory"] / "layout.json").read_text(encoding="utf-8"))
    questions = [q["number"] for page in layout["pages"] for q in page["questions"]]
    assert questions == list(range(1, 65))
    assert layout["spec"]["id_digits"] == 12
    with zipfile.ZipFile(package["package_path"]) as archive:
        assert "pages/page_2/template.json" in archive.namelist()
        assert "pages/page_2/reference_blank.png" in archive.namelist()
        assert "layout.json" in archive.namelist()


@pytest.mark.parametrize("preset", ["software-16th-abc", "software-15th-a"])
@pytest.mark.parametrize("digits", [6, 9, 12])
def test_layout_keeps_every_region_on_page(preset, digits):
    from adaptive_sheet_designer import build_layout, normalize_card_spec, template_for_page
    from src.schemas.template_schema import TEMPLATE_SCHEMA
    import jsonschema
    layout = build_layout(normalize_card_spec({"preset": preset, "id_digits": digits}))
    assert layout["page_count"] == 2
    assert layout["question_count"] == 64
    for page in layout["pages"]:
        template = template_for_page(layout, page)
        jsonschema.validate(template, TEMPLATE_SCHEMA)
        for block in template["fieldBlocks"].values():
            x, y = block["origin"]
            w, h = block["bubbleDimensions"]
            assert x >= 0 and y >= 0 and w > 0 and h > 0
            assert x+w <= template["pageDimensions"][0]
            assert y+h <= template["pageDimensions"][1]
        questions = page["questions"]
        for i, first in enumerate(questions):
            for second in questions[i+1:]:
                assert (first["x"]+first["width"] <= second["x"] or second["x"]+second["width"] <= first["x"]
                        or first["y"]+first["height"] <= second["y"] or second["y"]+second["height"] <= first["y"])


def test_mixed_sections_paginate_and_number_contiguously():
    from adaptive_sheet_designer import build_layout
    spec = normalize_sheet_spec({"sections": [{"kind": "essay", "count": 3}, {"kind": "single", "count": 150, "choices": 5}, {"kind": "fill", "count": 40}, {"kind": "multiple", "count": 5}]})
    layout = build_layout(spec)
    assert layout["page_count"] > 2
    assert [q["number"] for page in layout["pages"] for q in page["questions"]] == list(range(1,199))


def test_preview_renders_actual_pdf_pages_without_files(tmp_path, monkeypatch):
    from adaptive_sheet_designer import preview_sheet
    monkeypatch.chdir(tmp_path)
    result = preview_sheet({"id_digits":6,"sections":[{"kind":"multiple","count":7,"choices":5},{"kind":"judgment","count":2}]})
    assert result["question_count"] == 9
    assert result["page_count"] == 1
    assert "<svg" in result["pages"][0]["svg"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("payload", [
    {"sections":[{"kind":"single","count":200},{"kind":"fill","count":101}]},
    {"sections":[{"kind":"essay","count":300}]},
    {"sections":[{"kind":"material","count":1,"lines":20,"subparts":6}]},
    {"sections":[{"kind":"single","count":1}]*25},
    {"sections":[{"kind":"single","count":1,"choices":8}]},
])
def test_resource_and_geometry_limits(payload):
    from adaptive_sheet_designer import build_layout
    with pytest.raises(ValueError):
        build_layout(normalize_sheet_spec(payload))


def test_fastapi_preset_preview_and_validation(monkeypatch):
    from fastapi.testclient import TestClient
    import backend.app as backend_app
    monkeypatch.setattr(backend_app, "current_user", lambda _: {"id":"sheet-test"})
    client = TestClient(backend_app.app, raise_server_exceptions=False)
    presets = client.get("/api/sheets/presets")
    assert presets.status_code == 200 and len(presets.json()["presets"]) == 2
    response = client.post("/api/sheets/preview", json={"sections":[{"kind":"fill","count":12}]})
    assert response.status_code == 200 and response.json()["question_count"] == 12
    for route in ("/api/sheets/preview", "/api/sheets"):
        response = client.post(route, json={"id_digits":5})
        assert response.status_code == 400


def test_legacy_preview_endpoint(tmp_path, monkeypatch):
    import scan_ui
    import threading
    from urllib.request import Request, urlopen
    monkeypatch.setattr(scan_ui, "SHEETS_ROOT", tmp_path)
    server = scan_ui.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = "http://127.0.0.1:"+str(server.server_address[1])
        with urlopen(base+"/api/sheets/presets") as response:
            assert len(json.load(response)["presets"]) == 2
        request = Request(base+"/api/sheets/preview", data=json.dumps({"id_digits":7,"sections":[{"kind":"essay","count":1}]}).encode(), headers={"Content-Type":"application/json"},method="POST")
        with urlopen(request) as response:
            assert json.load(response)["spec"]["id_digits"] == 7
        assert list(tmp_path.iterdir()) == []
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_export_loads_in_real_omr_engine(tmp_path):
    from src.template import Template
    from src.utils.parsing import open_config_with_defaults
    package = generate_sheet_package({"preset":"software-15th-a", "id_digits":6}, output_root=tmp_path)
    for path in sorted(package["directory"].glob("pages/*/template.json")):
        template = Template(path, open_config_with_defaults(path.with_name("config.json")))
        assert template.field_blocks
        pixmap = fitz.Pixmap(path.with_name("reference_blank.png"))
        assert list(template.page_dimensions) == [pixmap.width, pixmap.height]


def test_optional_identity_fields_keep_template_columns_consistent():
    from adaptive_sheet_designer import build_layout, template_for_page
    layout = build_layout(normalize_sheet_spec({"include_name":False,"include_written_id":False,"include_paper_type":False}))
    for page in layout["pages"]:
        template = template_for_page(layout,page)
        assert all(name not in template["outputColumns"] for name in ["name","student_id_written","paper_type"])
        assert "paper_type" not in template["customLabels"]
