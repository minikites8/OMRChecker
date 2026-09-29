"""Layered PDFs preserve scan pages, recognition coordinates and workspace access."""
import hashlib
import json
from pathlib import Path

import cv2
import fitz
import numpy as np
import pytest

import exam_review
import scan_ui
import review_overlay_pdf as overlay
from src.tests.test_saas_workspaces import ALICE, BOB, http_service, store
from workspace_context import workspace_scope

RID = "20260929-scan-overlay"


def review_data():
    return {"review_id": RID, "owner_user_id": "alice", "card_files": ["答卷 #1.pdf"],
            "objective": [{"question": "1", "recognized": "AC"}],
            "items": [{"question": q} for q in ("31", "46", "64")],
            "crop_adjustments": {"material_mode": "grouped_61_63", "regions": {}}}


def seed(workspace, image_only=False):
    with workspace_scope(workspace):
        folder = scan_ui.REVIEW_ROOT / RID
        (folder / "input").mkdir(parents=True, exist_ok=True)
        (folder / "output").mkdir(exist_ok=True)
        review = review_data()
        if image_only:
            review["card_files"] = ["front.png", "back.png"]
            for name in review["card_files"]:
                ok, data = cv2.imencode(".png", np.full((842, 595), 248, dtype=np.uint8))
                assert ok
                (folder / "input" / name).write_bytes(data.tobytes())
        else:
            with fitz.open() as doc:
                for index in range(2):
                    page = doc.new_page(width=overlay.PAGE_W, height=overlay.PAGE_H)
                    page.insert_text((70, 80), "Original scan page " + str(index+1))
                    page.draw_rect(fitz.Rect(55, 640, 220, 680), color=(0, 0, 0))
                doc.save(str(folder / "input" / review["card_files"][0]))
        report = folder / "output/review.json"
        report.write_text(json.dumps(review), encoding="utf-8")
        return folder, review


@pytest.fixture
def scan_only(monkeypatch):
    monkeypatch.setattr(exam_review, "prepare_card_alignment", lambda pages, reference=None: (pages, [], [], {}, {}, 0, 0))
    def unexpected(*args, **kwargs):
        pytest.fail("Viewing scan overlays must preserve recognition results")
    monkeypatch.setattr(exam_review, "_recognize_crops", unexpected)


def test_pdf_has_two_enabled_layers_preserves_all_pages_and_highlights_selected_choices(tmp_path):
    image = np.full((842, 595), 245, dtype=np.uint8)
    output = tmp_path / "overlay.pdf"
    result = overlay.write_overlay_pdf([image, image, image], review_data(), output)
    assert result["pages"] == 3 and result["objective"] == 1 and result["fill"] == 4
    with fitz.open(output) as doc:
        assert doc.page_count == 3
        assert {layer["name"] for layer in doc.get_ocgs().values()} == {"选择题叠加层", "填空题扫描叠加层"}
        assert all(layer["on"] for layer in doc.get_ocgs().values())
        assert all(page.get_images() for page in doc)
        assert "选择题叠加层" in doc[0].get_text()
        assert "修正后识别区" in doc[1].get_text()
        drawings = doc[0].get_drawings()
        assert len([d for d in drawings if d.get("fill")]) == 2
        assert len(doc[1].get_drawings()) == 6
        assert len(doc[2].get_drawings()) == 0
        assert output.read_bytes().startswith(b"%PDF")
    assert not output.with_suffix(".pdf.tmp").exists()


def test_overlay_coordinates_follow_recognition_reference_to_scan_matrix():
    image = np.zeros((1684, 1190), dtype=np.uint8)
    sx, sy = image.shape[1]/overlay.PAGE_W, image.shape[0]/overlay.PAGE_H
    polygon = overlay.scan_polygon([(50, 100), (100, 140)], image,
                                   {"matrix": [[1, 0, 10*sx], [0, 1, -5*sy]]})
    assert [value for point in polygon for value in (point.x, point.y)] == pytest.approx([60, 95, 110, 135])


@pytest.mark.parametrize("matrix", [[[0, 0, 0], [0, 0, 0]], [[1, 2]], [[float("nan"), 0, 0], [0, 1, 0]]])
def test_invalid_alignment_uses_page_coordinates(matrix):
    image = np.zeros((842, 595), dtype=np.uint8)
    points = overlay.scan_polygon([(60, 95)], image, {"matrix": matrix})
    assert points[0].x == pytest.approx(60)
    assert points[0].y == pytest.approx(95)


def test_program_scanning_uses_saved_padding_shift_and_matrix(tmp_path):
    review = review_data()
    review["crop_adjustments"].update(page1_program_y_pt=4,
        regions={"program": {"matrix": [[1, 0, 10], [0, 1, -6]]}})
    output = tmp_path / "aligned.pdf"
    image = np.zeros((1684, 1190), dtype=np.uint8)
    overlay.write_overlay_pdf([image], review, output)
    entry = next(item for item in exam_review._subjective_crop_regions("grouped_61_63", 4) if item[2] == "31")
    x,y,w,h = exam_review._expand_crop_rect(entry[3].get("display_rect", entry[1]))
    expected = overlay.scan_polygon([(x, overlay.PAGE_H-y-h), (x+w, overlay.PAGE_H-y)], image,
                                   review["crop_adjustments"]["regions"]["program"])
    with fitz.open(output) as doc:
        blue = [d for d in doc[0].get_drawings() if d["color"][2] > 0.8 and d["dashes"] != "[] 0"]
        assert len(blue) == 1
        sx, sy = image.shape[1]/overlay.PAGE_W, image.shape[0]/overlay.PAGE_H
        bounds = [np.floor(expected[0].x*sx)/sx, np.floor(expected[0].y*sy)/sy,
                  np.ceil(expected[1].x*sx)/sx, np.ceil(expected[1].y*sy)/sy]
        assert list(blue[0]["rect"]) == pytest.approx(bounds, abs=0.001)


@pytest.mark.parametrize("prefix", ["/w", "/api/w"])
def test_teachers_open_layered_pdf_with_unchanged_sources_and_reports(http_service, scan_only, prefix):
    client, store, root = http_service
    store.migrate_shared([ALICE, BOB])
    workspace = store.require_member("shared", ALICE)
    folder, review = seed(workspace)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in [folder/"output/review.json", *list((folder/"input").iterdir())]}
    url = prefix + "/shared/api/review/overlay-pdf?review_id=" + RID
    for teacher in ("alice", "bob"):
        response = client.get(url, headers={"Cookie": teacher})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["review_id"] == RID and len(data["files"]) == 1
        item = data["files"][0]
        assert item["url"].startswith(prefix + "/shared/reviews/" + RID + "/output/scan_overlay/")
        pdf = client.get(item["url"], headers={"Cookie": teacher})
        assert pdf.status_code == 200 and "application/pdf" in pdf.headers["content-type"]
        assert "attachment" not in pdf.headers.get("content-disposition", "")
        assert len(pdf.content) == item["size"]
        with fitz.open(stream=pdf.content, filetype="pdf") as doc:
            assert doc.page_count == 2 and len(doc.get_ocgs()) == 2
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}


def test_overlay_api_and_generated_pdf_enforce_membership(http_service, scan_only):
    client, store, root = http_service
    workspace = store.create(ALICE, "Overlay")
    seed(workspace)
    api = "/api/w/" + workspace["id"] + "/api/review/overlay-pdf?review_id=" + RID
    pdf = client.get(api, headers={"Cookie": "alice"}).json()["files"][0]["url"]
    for url in (api, pdf):
        for teacher, status in [("", 401), ("bob", 404), ("charlie", 404)]:
            assert client.get(url, headers={"Cookie": teacher}).status_code == status


def test_photo_uploads_generate_pdf_and_cached_result_reuses_file(http_service, scan_only, monkeypatch):
    client, store, root = http_service
    workspace = store.create(ALICE, "Photos")
    folder, review = seed(workspace, image_only=True)
    url = "/api/w/" + workspace["id"] + "/api/review/overlay-pdf?review_id=" + RID
    response = client.get(url, headers={"Cookie": "alice"})
    assert response.status_code == 200, response.text
    file = next((folder/"output/scan_overlay").glob("*.pdf"))
    before = file.stat().st_mtime_ns
    def unexpected(*args, **kwargs):
        pytest.fail("Cached overlays should reuse the generated file")
    monkeypatch.setattr(overlay, "build_overlay_pdf", unexpected)
    cached = client.get(url, headers={"Cookie": "alice"})
    assert cached.json() == response.json()
    assert file.stat().st_mtime_ns == before


def test_crop_or_recognition_change_invalidates_pdf_cache(tmp_path):
    source = tmp_path / "answer.pdf"; source.write_bytes(b"pdf")
    review = review_data()
    first = overlay.overlay_fingerprint([source], review)
    review["objective"][0]["recognized"] = "B"
    second = overlay.overlay_fingerprint([source], review)
    review["crop_adjustments"]["page1_program_y_pt"] = 4
    third = overlay.overlay_fingerprint([source], review)
    assert len({first, second, third}) == 3


def test_legacy_metadata_finds_originals_and_missing_scans_report_error(http_service, scan_only):
    client, store, root = http_service
    workspace = store.create(ALICE, "Legacy")
    folder, review = seed(workspace)
    review.pop("card_files")
    report = folder / "output/review.json"
    report.write_text(json.dumps(review), encoding="utf-8")
    url = "/api/w/" + workspace["id"] + "/api/review/overlay-pdf?review_id=" + RID
    assert client.get(url, headers={"Cookie": "alice"}).status_code == 200
    review["card_files"] = ["missing.pdf"]
    report.write_text(json.dumps(review), encoding="utf-8")
    response = client.get(url, headers={"Cookie": "alice"})
    assert response.status_code == 400
    assert "扫描文件缺失" in response.json()["error"]


def test_both_server_routes_and_entry_use_the_overlay_endpoint():
    root = Path(__file__).resolve().parents[2]
    assert 'route == "/api/review/overlay-pdf"' in (root/"scan_ui.py").read_text(encoding="utf-8")
    assert '@app.get("/api/review/overlay-pdf")' in (root/"backend/app.py").read_text(encoding="utf-8")
    assert "/api/review/overlay-pdf?review_id=" in (root/"ui/review-source.js").read_text(encoding="utf-8")


def test_result_pdf_uses_shared_current_correction_without_changing_saved_results(tmp_path,monkeypatch):
    image=np.full((842,595),255,dtype=np.uint8)
    review=review_data(); review['crop_adjustments']['regions']={'program':{'matrix':[[1,0,99],[0,1,99]]}}
    before=json.dumps(review,sort_keys=True)
    monkeypatch.setattr(exam_review,'_load_page',lambda path:[('page',image)])
    calls=[]
    def corrected(pages,reference=None):
        calls.append(reference)
        return pages,[],[],{}, {'program':{'status':'已校正','matrix':[[1,0,6],[0,1,0]]}},0,0
    monkeypatch.setattr(exam_review,'prepare_card_alignment',corrected)
    overlay.build_overlay_pdf([tmp_path/'input.pdf'],review,tmp_path/'out.pdf','reference.pdf')
    assert calls==['reference.pdf']
    assert json.dumps(review,sort_keys=True)==before
    with fitz.open(tmp_path/'out.pdf') as doc:
        assert any(d['color'][2]>0.8 and d['rect'].x0<80 for d in doc[0].get_drawings())
