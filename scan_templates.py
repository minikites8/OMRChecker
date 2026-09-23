"""扫描与答题卡识别模板注册表。"""

from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path


class TemplateManager:
    def __init__(self, project_root: Path, legacy_scan_root: Path, storage_root: Path | None = None):
        self.project_root = Path(project_root).resolve()
        self.legacy_scan_root = Path(legacy_scan_root).resolve()
        self.storage_root = (Path(storage_root) if storage_root else self.project_root / "inputs" / "scan_templates").resolve()
        self.registry_path = self.storage_root / "registry.json"
        self.lock = threading.RLock()
        self.ensure_initialized()

    def _now(self):
        return datetime.now().isoformat(timespec="seconds")

    def _builtins(self):
        return [
            {
                "id": "software-16th-abc",
                "name": "第十六届软件方向 A/B/C 通用答题卡",
                "description": "两页答题卡；61—63填空，64算法题。",
                "scan_source": "inputs/phone_scan",
                "builtin": True,
                "recognition": {
                    "reference_pdf": "output/pdf/exam_16th_answer_card_unified_2026/第十六届软件方向二面试题A_B_C通用答题卡_定位标记版.pdf",
                    "question_order": [str(i) for i in range(31, 65)],
                    "layout": "16th_abc_61_63_fill_64_algorithm",
                    "material_mode": "grouped_61_63",
                    "page_count": 2,
                    "question_range": "1—64",
                },
            },
            {
                "id": "software-15th-a",
                "name": "第十五届软件方向 A 卷答题卡",
                "description": "两页定位标记版；61—63按子空识别，64算法题。",
                "scan_source": "inputs/phone_scan",
                "builtin": True,
                "recognition": {
                    "reference_pdf": "output/pdf/exam_answer_cards/marker_version/15th软件方向二面试题A卷答题卡_定位标记版.pdf",
                    "question_order": [*(str(i) for i in range(31, 61)), "61(1)", "61(2)", "62(1)", "62(2)", "63(1)", "63(2)", "63(3)", "64"],
                    "layout": "15th_a_legacy_subfields_64_algorithm",
                    "material_mode": "legacy_subfields",
                    "page_count": 2,
                    "question_range": "1—64",
                },
            },
        ]

    def ensure_initialized(self):
        with self.lock:
            self.storage_root.mkdir(parents=True, exist_ok=True)
            if self.registry_path.is_file():
                registry = self._read_registry()
            else:
                now = self._now()
                templates = []
                for item in self._builtins():
                    entry = {key: value for key, value in item.items() if key != "recognition"}
                    entry["created_at"] = now
                    entry["updated_at"] = now
                    templates.append(entry)
                    self._write_recognition(item["id"], item["recognition"])
                registry = {"active_template_id": templates[0]["id"], "templates": templates}
                self._write_registry(registry)
            changed = False
            known = {item.get("id") for item in registry.get("templates", [])}
            now = self._now()
            for item in self._builtins():
                if item["id"] not in known:
                    entry = {key: value for key, value in item.items() if key != "recognition"}
                    entry["created_at"] = now
                    entry["updated_at"] = now
                    registry.setdefault("templates", []).append(entry)
                    changed = True
                recognition_path = self.storage_root / item["id"] / "recognition.json"
                if not recognition_path.is_file():
                    self._write_recognition(item["id"], item["recognition"])
            if not registry.get("active_template_id") and registry.get("templates"):
                registry["active_template_id"] = registry["templates"][0]["id"]
                changed = True
            if changed:
                self._write_registry(registry)

    def _read_registry(self):
        data = json.loads(self.registry_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data.get("templates"), list):
            raise ValueError("模板注册表格式错误")
        return data

    def _write_registry(self, data):
        self.storage_root.mkdir(parents=True, exist_ok=True)
        temporary = self.registry_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.registry_path)

    def _write_recognition(self, template_id, recognition):
        folder = self.storage_root / template_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "recognition.json").write_text(
            json.dumps(recognition, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _clean_id(self, value):
        template_id = str(value or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", template_id):
            raise ValueError("模板编号格式错误")
        return template_id

    def _entry(self, template_id=None, registry=None):
        registry = registry or self._read_registry()
        clean_id = self._clean_id(template_id or registry.get("active_template_id"))
        for entry in registry["templates"]:
            if entry.get("id") == clean_id:
                return entry
        raise ValueError("识别模板不存在")

    def _recognition(self, template_id):
        path = self.storage_root / template_id / "recognition.json"
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        reference = str(data.get("reference_pdf") or "").strip()
        if reference:
            resolved = (self.project_root / reference).resolve()
            if resolved != self.project_root and self.project_root not in resolved.parents:
                raise ValueError("模板参考文件路径超出项目目录")
            data["reference_pdf"] = str(resolved)
        return data

    def get(self, template_id=None):
        with self.lock:
            registry = self._read_registry()
            entry = dict(self._entry(template_id, registry))
            entry["active"] = entry["id"] == registry.get("active_template_id")
            entry["recognition"] = self._recognition(entry["id"])
            scan_root = (self.project_root / entry.get("scan_source", "inputs/phone_scan")).resolve()
            entry["scan_ready"] = (scan_root / "template.json").is_file() and (scan_root / "config.json").is_file()
            entry["review_ready"] = bool(entry["recognition"].get("reference_pdf")) and Path(entry["recognition"].get("reference_pdf", "")).is_file()
            return entry

    def list(self):
        with self.lock:
            registry = self._read_registry()
            items = [self.get(entry["id"]) for entry in registry["templates"]]
            return {"ok": True, "active_template_id": registry.get("active_template_id", ""), "templates": items}

    def scan_root(self, template_id=None):
        entry = self.get(template_id)
        root = (self.project_root / entry.get("scan_source", "inputs/phone_scan")).resolve()
        if root != self.project_root and self.project_root not in root.parents:
            raise ValueError("扫描模板路径超出项目目录")
        if not root.is_dir():
            raise ValueError("扫描模板资源不存在")
        return root

    def create(self, payload):
        name = str(payload.get("name") or "").strip()
        description = str(payload.get("description") or "").strip()
        if not name or len(name) > 100:
            raise ValueError("模板名称需为1—100个字符")
        if len(description) > 500:
            raise ValueError("模板说明最多500个字符")
        with self.lock:
            registry = self._read_registry()
            source = self._entry(payload.get("source_template_id") or registry.get("active_template_id"), registry)
            base = re.sub(r"[^a-z0-9_-]+", "-", str(payload.get("id") or "").lower()).strip("-")
            template_id = base if re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", base or "") else "template-" + uuid.uuid4().hex[:8]
            if any(item.get("id") == template_id for item in registry["templates"]):
                raise ValueError("模板编号已存在")
            source_folder = self.storage_root / source["id"]
            target_folder = self.storage_root / template_id
            if source_folder.is_dir():
                shutil.copytree(source_folder, target_folder)
            else:
                target_folder.mkdir(parents=True)
                self._write_recognition(template_id, {})
            now = self._now()
            entry = {
                "id": template_id,
                "name": name,
                "description": description,
                "scan_source": source.get("scan_source", "inputs/phone_scan"),
                "builtin": False,
                "created_at": now,
                "updated_at": now,
            }
            registry["templates"].append(entry)
            self._write_registry(registry)
            return {"ok": True, "template": self.get(template_id), **self.list()}

    def update(self, payload):
        template_id = self._clean_id(payload.get("template_id"))
        name = str(payload.get("name") or "").strip()
        description = str(payload.get("description") or "").strip()
        if not name or len(name) > 100:
            raise ValueError("模板名称需为1—100个字符")
        if len(description) > 500:
            raise ValueError("模板说明最多500个字符")
        with self.lock:
            registry = self._read_registry()
            entry = self._entry(template_id, registry)
            entry["name"] = name
            entry["description"] = description
            entry["updated_at"] = self._now()
            self._write_registry(registry)
            return {"ok": True, "template": self.get(template_id), **self.list()}

    def activate(self, payload):
        template_id = self._clean_id(payload.get("template_id"))
        with self.lock:
            registry = self._read_registry()
            self._entry(template_id, registry)
            registry["active_template_id"] = template_id
            self._write_registry(registry)
            return self.list()

    def delete(self, payload):
        template_id = self._clean_id(payload.get("template_id"))
        with self.lock:
            registry = self._read_registry()
            entry = self._entry(template_id, registry)
            if entry.get("builtin"):
                raise ValueError("内置模板支持复制和切换")
            if registry.get("active_template_id") == template_id:
                raise ValueError("请先切换到其他模板")
            registry["templates"] = [item for item in registry["templates"] if item.get("id") != template_id]
            self._write_registry(registry)
            folder = self.storage_root / template_id
            if folder.is_dir():
                shutil.rmtree(folder)
            return self.list()
