"""本地维护 Scout 题目与阅卷题号的映射表。"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path


class ScoutMappingManager:
    def __init__(self, storage_path: Path):
        self.storage_path = Path(storage_path).resolve()
        self.lock = threading.RLock()
        self.ensure_initialized()

    @staticmethod
    def _now():
        return datetime.now().isoformat(timespec="seconds")

    def ensure_initialized(self):
        with self.lock:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.storage_path.is_file():
                self._write({"version": 1, "updated_at": "", "sessions": {}})

    def _read(self):
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Scout 题目映射表读取失败") from error
        if not isinstance(data, dict) or not isinstance(data.get("sessions", {}), dict):
            raise ValueError("Scout 题目映射表格式错误")
        return data

    def _write(self, data):
        data["version"] = 1
        data["updated_at"] = self._now()
        temporary = self.storage_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.storage_path)

    @staticmethod
    def _session_id(value):
        value = str(value or "").strip()
        if not value or not re.fullmatch(r"\d+", value):
            raise ValueError("Scout session_id 需填写数字")
        return int(value)

    @staticmethod
    def _question_label(title, fallback):
        match = re.match(r"\s*(\d+(?:\s*[-—]\s*\d+)?)\s*[.、:：)）]?", str(title or ""))
        if match:
            return re.sub(r"\s*[-—]\s*", "-", match.group(1))
        return str(fallback)

    @classmethod
    def _source_items(cls, payload):
        source = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(source, dict):
            source = source.get("items", source.get("sections", source))
        if not isinstance(source, list):
            raise ValueError("Scout 题目数据缺少 data.items")
        return source

    @classmethod
    def normalize_mappings(cls, session_id, sections, target_session_id=None):
        source_session_id = cls._session_id(session_id)
        target = cls._session_id(target_session_id or session_id)
        mappings = []
        fallback = 1
        for section_index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            section_id = section.get("id", "")
            section_name = str(section.get("name") or "")[:100]
            questions = section.get("questions") or []
            for question_index, question in enumerate(questions):
                if not isinstance(question, dict):
                    continue
                scout_id = question.get("id")
                try:
                    scout_id = int(scout_id)
                except (TypeError, ValueError) as error:
                    raise ValueError("Scout 题目缺少有效 question_id") from error
                title = str(question.get("title") or "").strip()
                local_question = str(question.get("local_question") or "").strip()
                if not local_question:
                    local_question = cls._question_label(title, fallback)
                mappings.append({
                    "local_question": local_question,
                    "scout_session_id": target,
                    "source_session_id": source_session_id,
                    "scout_question_id": scout_id,
                    "section_id": section_id,
                    "section_name": section_name,
                    "title": title[:200],
                    "type": str(question.get("type") or ""),
                    "score": question.get("score", 0) or 0,
                    "sort_order": question.get("sort_order", question_index),
                    "section_sort_order": section.get("sort_order", section_index),
                })
                fallback += 1
        if not mappings:
            raise ValueError("Scout 题目数据为空")
        return target, mappings

    @staticmethod
    def _validate_mappings(session_id, mappings):
        target = ScoutMappingManager._session_id(session_id)
        if not isinstance(mappings, list) or not mappings:
            raise ValueError("映射表至少需要一条题目映射")
        normalized = []
        seen_local = set()
        seen_scout = set()
        for index, raw in enumerate(mappings):
            if not isinstance(raw, dict):
                raise ValueError("第{}条映射格式错误".format(index + 1))
            local_question = str(raw.get("local_question") or raw.get("localQuestion") or "").strip()
            if not local_question:
                raise ValueError("第{}条映射缺少本地题号".format(index + 1))
            try:
                scout_id = int(raw.get("scout_question_id") or raw.get("question_id"))
            except (TypeError, ValueError) as error:
                raise ValueError("第{}条映射缺少 Scout question_id".format(index + 1)) from error
            if local_question in seen_local:
                raise ValueError("本地题号重复：{}".format(local_question))
            if scout_id in seen_scout:
                raise ValueError("Scout question_id 重复：{}".format(scout_id))
            seen_local.add(local_question)
            seen_scout.add(scout_id)
            entry = dict(raw)
            entry["local_question"] = local_question
            entry["scout_session_id"] = target
            entry["scout_question_id"] = scout_id
            entry["title"] = str(raw.get("title") or "")[:200]
            entry["section_name"] = str(raw.get("section_name") or "")[:100]
            entry["type"] = str(raw.get("type") or "")
            normalized.append(entry)
        return target, normalized

    def list(self, session_id=None):
        with self.lock:
            data = self._read()
            if session_id in (None, ""):
                sessions = data.get("sessions", {})
                return {"ok": True, "updated_at": data.get("updated_at", ""), "sessions": list(sessions.values())}
            target = str(self._session_id(session_id))
            session = data.get("sessions", {}).get(target)
            return {"ok": True, "session": session or {"session_id": int(target), "mappings": []}, "mapping_path": str(self.storage_path)}

    def import_questions(self, payload):
        sections = self._source_items(payload)
        source_session = payload.get("source_session_id") or next(
            (section.get("session_id") for section in sections if isinstance(section, dict) and section.get("session_id")), None
        )
        target_session = payload.get("session_id") or source_session
        if source_session in (None, ""):
            raise ValueError("Scout 题目数据缺少 session_id")
        target, mappings = self.normalize_mappings(source_session, sections, target_session)
        with self.lock:
            data = self._read()
            data.setdefault("sessions", {})[str(target)] = {
                "session_id": target,
                "source_session_id": self._session_id(source_session),
                "updated_at": self._now(),
                "mappings": mappings,
            }
            self._write(data)
            return {"ok": True, "session": data["sessions"][str(target)], "mapping_path": str(self.storage_path)}

    def save(self, payload):
        target, mappings = self._validate_mappings(payload.get("session_id"), payload.get("mappings"))
        with self.lock:
            data = self._read()
            data.setdefault("sessions", {})[str(target)] = {
                "session_id": target,
                "source_session_id": payload.get("source_session_id", target),
                "updated_at": self._now(),
                "mappings": mappings,
            }
            self._write(data)
            return {"ok": True, "session": data["sessions"][str(target)], "mapping_path": str(self.storage_path)}

    def resolve(self, session_id, local_question):
        target = str(self._session_id(session_id))
        with self.lock:
            data = self._read()
            session = data.get("sessions", {}).get(target, {})
            for mapping in session.get("mappings", []):
                if str(mapping.get("local_question")) == str(local_question):
                    return mapping
        return None
