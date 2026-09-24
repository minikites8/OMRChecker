"""默认定位资源在干净部署、旧数据迁移与缺失场景下的回归测试。"""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import exam_review
import recognition_assets as assets
from scan_templates import TemplateManager


@pytest.fixture
def bundled_root(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(assets.PROJECT_ROOT / assets.RESOURCE_REL, project / assets.RESOURCE_REL)
    return project


def test_delivered_resources_have_expected_checksums_and_pages():
    assert assets.validate_recognition_assets() == {
        "ok": True, "files": 7,
        "reference_pages": {"software-16th-abc": 2, "software-15th-a": 2},
    }


def test_clean_deployment_uses_only_bundled_defaults(bundled_root, tmp_path):
    data_root = tmp_path / "persistent-data"
    manager = TemplateManager(bundled_root, bundled_root / assets.LEGACY_SCAN_REL, storage_root=data_root)
    for item in manager.list()["templates"]:
        assert item["scan_ready"] and item["review_ready"]
        assert manager.scan_root(item["id"]) == bundled_root / assets.SCAN_REL
        assert len(exam_review._reference_pages(item["recognition"]["reference_pdf"])) == 2
    assert not (bundled_root / "output").exists()
    assert not (bundled_root / "inputs").exists()
    assert manager.registry_path.parent == data_root


@pytest.mark.parametrize("relative", assets.REQUIRED_FILES)
def test_missing_delivery_resource_reports_exact_file(bundled_root, relative):
    (bundled_root / relative).unlink()
    with pytest.raises(ValueError, match="定位资源检查失败") as captured:
        assets.validate_recognition_assets(bundled_root)
    assert relative in str(captured.value)


def test_modified_resource_reports_checksum_error(bundled_root):
    (bundled_root / assets.MARKER_REL).write_bytes(b"broken marker")
    with pytest.raises(ValueError, match="定位资源校验失败"):
        assets.validate_recognition_assets(bundled_root)


def test_missing_manifest_reports_chinese_error(bundled_root):
    (bundled_root / assets.RESOURCE_REL / "manifest.json").unlink()
    with pytest.raises(ValueError, match="缺少定位资源清单"):
        assets.validate_recognition_assets(bundled_root)


def test_manifest_cannot_omit_required_resources(bundled_root):
    path = bundled_root / assets.RESOURCE_REL / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["files"][assets.MARKER_REL]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="定位资源清单格式错误"):
        assets.validate_recognition_assets(bundled_root)


def test_scan_ready_checks_preprocessor_reference(bundled_root):
    manager = TemplateManager(bundled_root, bundled_root / assets.LEGACY_SCAN_REL)
    (bundled_root / assets.SCAN_REL / "reference_blank.png").unlink()
    item = manager.get()
    assert item["scan_ready"] is False
    assert "reference_blank.png" in " ".join(item["resource_errors"])


def test_scan_ready_checks_reference_and_relative_path(bundled_root):
    folder = bundled_root / assets.SCAN_REL
    path = folder / "template.json"
    path.write_text(json.dumps({"preProcessors": [{"options": {"relativePath": "missing.jpg"}}]}), encoding="utf-8")
    assert "missing.jpg" in " ".join(assets.scan_resource_errors(folder))
    (folder / "missing.jpg").write_bytes(b"image")
    assert assets.scan_resource_errors(folder) == []


def test_migration_preserves_active_template_custom_settings_and_persistent_root(bundled_root, tmp_path):
    data = tmp_path / "persistent-data"
    manager = TemplateManager(bundled_root, bundled_root / assets.LEGACY_SCAN_REL, storage_root=data)
    copy_id = manager.create({"id": "copied-template", "name": "用户复制模板"})["template"]["id"]
    custom_id = manager.create({"id": "custom-template", "name": "自定义参考页"})["template"]["id"]
    manager.activate({"template_id": custom_id})
    registry = manager._read_registry()
    for entry in registry["templates"]:
        if entry.get("builtin"):
            entry["scan_source"] = assets.LEGACY_SCAN_REL
    manager._write_registry(registry)
    previous = {}
    for template_id in ("software-16th-abc", "software-15th-a", copy_id, custom_id):
        file = data / template_id / "recognition.json"
        config = json.loads(file.read_text(encoding="utf-8"))
        old_path = list(assets.LEGACY_REFERENCES)[template_id == "software-15th-a"]
        config["reference_pdf"] = "custom/reference.pdf" if template_id == custom_id else old_path
        config["custom_layout_setting"] = {"row": 39, "y": 128}
        previous[template_id] = dict(config)
        file.write_text(json.dumps(config), encoding="utf-8")
    reopened = TemplateManager(bundled_root, bundled_root / assets.LEGACY_SCAN_REL, storage_root=data)
    assert reopened.list()["active_template_id"] == custom_id
    assert reopened.get(copy_id)["name"] == "用户复制模板"
    for template_id, before in previous.items():
        actual = json.loads((data / template_id / "recognition.json").read_text(encoding="utf-8"))
        assert actual == {**before, "reference_pdf": assets.migrate_reference(before["reference_pdf"], bundled_root)}
    assert reopened.scan_root(copy_id) == data / copy_id
    assert reopened.scan_root("software-16th-abc") == bundled_root / assets.SCAN_REL
    stored = {p: p.read_bytes() for p in data.rglob("*.json")}
    TemplateManager(bundled_root, bundled_root / assets.LEGACY_SCAN_REL, storage_root=data)
    assert all(p.read_bytes() == content for p, content in stored.items())


@pytest.mark.parametrize("old,new", assets.LEGACY_REFERENCES.items())
def test_known_reference_paths_migrate_with_both_separators(tmp_path, old, new):
    assert assets.migrate_reference(old, tmp_path) == new
    assert assets.migrate_reference(old.replace("/", "\\"), tmp_path) == new
    assert assets.migrate_reference(str(tmp_path / old), tmp_path) == new
    assert assets.migrate_reference("./" + old, tmp_path) == new
    assert assets.migrate_reference("custom/my-reference.pdf", tmp_path) == "custom/my-reference.pdf"


def test_reference_and_marker_missing_fail_before_alignment(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="缺少答题卡定位参考 PDF"):
        exam_review._reference_pages(tmp_path / "missing.pdf")
    monkeypatch.setattr(exam_review, "MARKER_ASSET", tmp_path / "missing.jpg")
    with pytest.raises(ValueError, match="缺少定位标记图片"):
        exam_review._warp_marker_page(np.full((20, 20), 255, dtype=np.uint8))


def test_single_page_reference_reports_error(tmp_path):
    import fitz
    path = tmp_path / "single.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(path)
    with pytest.raises(ValueError, match="至少需要两页"):
        exam_review._reference_pages(path)


def test_invalid_reference_reports_chinese_error(tmp_path):
    path = tmp_path / "invalid.pdf"
    path.write_bytes(b"invalid")
    with pytest.raises(ValueError, match="定位参考 PDF 读取失败"):
        exam_review._reference_pages(path)


def test_ui_copies_all_bundled_scan_assets(bundled_root, tmp_path, monkeypatch):
    import scan_ui
    manager = TemplateManager(bundled_root, bundled_root / assets.LEGACY_SCAN_REL)
    monkeypatch.setattr(scan_ui, "TEMPLATE_MANAGER", manager)
    target = tmp_path / "job"
    scan_ui.copy_template_assets(target)
    for name in ("template.json", "config.json", "evaluation.json", "reference_blank.png"):
        assert (target / name).read_bytes() == (bundled_root / assets.SCAN_REL / name).read_bytes()


def test_packaged_pdf_recovers_perspective_page_order_and_filled_answers(tmp_path, monkeypatch):
    first, second = exam_review._reference_pages()
    for x, y in [(80, 348), (107, 368.5), (90, 503), (148, 503), (402, 503), (477, 503)]:
        cv2.circle(first, (round(x * 2), round(y * 2)), 7, 0, -1)
    cv2.circle(first, (round(486 * 2), round(259 * 2)), 8, 0, -1)
    height, width = first.shape
    source = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    destination = np.float32([[146, 115], [1268, 70], [1312, 1755], [74, 1815]])
    transform = cv2.getPerspectiveTransform(source, destination)
    photos = []
    for index, page in enumerate((second, first)):
        photo = cv2.warpPerspective(page, transform, (1400, 1900), borderValue=190)
        path = tmp_path / f"photo{index}.png"
        assert cv2.imwrite(str(path), photo)
        photos.append(path)
    monkeypatch.setattr(exam_review, "_recognize_crops", lambda crops, labels: [
        SimpleNamespace(text="", confidence=0.0, error=None) for _ in labels
    ])
    report = exam_review.extract_answer_card(photos)
    assert report["alignment_scores"][1] > report["alignment_scores"][0]
    for number, answer in {"1": "A", "2": "B", "16": "AC", "21": "F", "26": "T"}.items():
        assert report["objective"][number] == answer
    assert report["paper_type"] == "B"


def test_generators_use_delivered_inputs():
    import generate_16th_answer_cards as generator
    assert generator.MARKER_PATH == assets.MARKER_ASSET
    assert generator.REFERENCE_15TH_PDF == assets.REFERENCE_15TH_PDF


def test_startup_and_docker_build_validate_assets():
    root = assets.PROJECT_ROOT
    assert "validate_recognition_assets(PROJECT_ROOT)" in (root / "scan_ui.py").read_text(encoding="utf-8")
    assert "validate_recognition_assets(legacy.PROJECT_ROOT)" in (root / "backend/app.py").read_text(encoding="utf-8")
    assert "RUN python -m recognition_assets" in (root / "Dockerfile.backend").read_text(encoding="utf-8")
