import json
from pathlib import Path

from scan_templates import TemplateManager


def _legacy_scan_root(tmp_path):
    root = tmp_path / "inputs" / "phone_scan"
    root.mkdir(parents=True)
    (root / "template.json").write_text(json.dumps({"preProcessors": []}), encoding="utf-8")
    (root / "config.json").write_text("{}", encoding="utf-8")
    return root


def test_template_manager_bootstraps_multiple_templates_and_persists_active(tmp_path):
    manager = TemplateManager(tmp_path, _legacy_scan_root(tmp_path))
    listed = manager.list()
    assert listed["active_template_id"] == "software-16th-abc"
    assert {item["id"] for item in listed["templates"]} >= {"software-16th-abc", "software-15th-a"}

    activated = manager.activate({"template_id": "software-15th-a"})
    assert activated["active_template_id"] == "software-15th-a"
    reopened = TemplateManager(tmp_path, tmp_path / "inputs" / "phone_scan")
    assert reopened.list()["active_template_id"] == "software-15th-a"
    assert reopened.get()["recognition"]["material_mode"] == "legacy_subfields"


def test_template_manager_creates_updates_and_deletes_copy(tmp_path):
    manager = TemplateManager(tmp_path, _legacy_scan_root(tmp_path))
    created = manager.create({
        "name": "机考补录模板",
        "description": "复制第十六届切割配置",
        "source_template_id": "software-16th-abc",
    })
    template_id = created["template"]["id"]
    assert created["template"]["recognition"]["layout"] == "16th_abc_61_63_fill_64_algorithm"

    updated = manager.update({"template_id": template_id, "name": "机考补录模板 V2", "description": "已校准"})
    assert updated["template"]["name"] == "机考补录模板 V2"
    manager.activate({"template_id": "software-15th-a"})
    deleted = manager.delete({"template_id": template_id})
    assert template_id not in {item["id"] for item in deleted["templates"]}


def test_scan_template_assets_follow_selected_template(tmp_path):
    legacy = _legacy_scan_root(tmp_path)
    (legacy / "evaluation.json").write_text("{}", encoding="utf-8")
    manager = TemplateManager(tmp_path, legacy)
    selected = manager.get("software-16th-abc")
    assert manager.scan_root(selected["id"]) == legacy.resolve()


def test_template_http_endpoints_list_and_activate(tmp_path, monkeypatch):
    import threading
    from urllib.request import Request, urlopen
    import scan_ui

    manager = TemplateManager(tmp_path, _legacy_scan_root(tmp_path))
    monkeypatch.setattr(scan_ui, "TEMPLATE_MANAGER", manager)
    server = scan_ui.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = "http://127.0.0.1:{}".format(server.server_address[1])
        with urlopen(base + "/api/templates", timeout=5) as response:
            listed = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert len(listed["templates"]) >= 2
        request = Request(
            base + "/api/templates/activate",
            data=json.dumps({"template_id": "software-15th-a"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            activated = json.loads(response.read().decode("utf-8"))
        assert activated["active_template_id"] == "software-15th-a"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_legacy_recognition_template_uses_subfield_crops(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import numpy as np
    import exam_review

    page = np.full((1684, 1190), 255, dtype=np.uint8)
    monkeypatch.setattr(exam_review, "_load_page", lambda _path: [("p1", page), ("p2", page)])
    monkeypatch.setattr(exam_review, "_align_and_order_pages", lambda images, **_kwargs: (images, []))
    monkeypatch.setattr(exam_review, "_reference_pages", lambda *_args: [])
    monkeypatch.setattr(exam_review, "_read_student_id", lambda *_args: "202619240110")
    monkeypatch.setattr(exam_review, "_bubble_scores", lambda *args, **_kwargs: [(choice, 0.0) for choice in args[3]])
    captured = []
    def recognize(crops, labels):
        captured.extend(labels)
        return [SimpleNamespace(text="", confidence=0.0, error=None) for _ in labels]
    monkeypatch.setattr(exam_review, "_recognize_crops", recognize)
    report = exam_review.extract_answer_card(
        [tmp_path / "legacy.pdf"],
        template_config={"material_mode": "legacy_subfields", "layout": "legacy-layout"},
    )
    assert {"61(1)", "61(2)", "63(3)", "64思路", "64代码"}.issubset(set(captured))
    assert report["crop_adjustments"]["answer_card_layout"] == "legacy-layout"
    assert report["crop_adjustments"]["material_mode"] == "legacy_subfields"
