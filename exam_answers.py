"""Versioned reference-answer editing and source-question-only regrading."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import exam_review
from exam_import import answer_map, _normalize_answer
from exam_regrade import _busy, _prepare_review
from review_collaboration import record_action


def _revision(imported):
    return hashlib.sha256(json.dumps(imported, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _select(imported, paper_type):
    variants = imported.get("paper_variants") or {}
    key = str(paper_type or "").strip().upper()
    if variants:
        key = key or ("A" if "A" in variants else next(iter(variants)))
        if key not in variants:
            raise ValueError("请选择有效卷型")
        return key, variants[key]
    if key:
        raise ValueError("该试卷使用统一答案，请刷新卷型")
    return "", imported


def _questions(selected):
    for si, section in enumerate(selected["exam"]["sections"], 1):
        for qi, question in enumerate(section["questions"], 1):
            yield f"{si}:{qi}", section, question


def _find(selected, key):
    for group, section, question in _questions(selected):
        if group == str(key):
            return section, question
    raise ValueError("题目已更新，请重新打开题目与答案")


def _check_revision(imported, revision):
    if revision != _revision(imported):
        raise ValueError("参考答案版本已更新，请刷新后核对并重新保存")


def _view(import_id, imported, paper_type):
    key, selected = _select(imported, paper_type)
    flat = selected.get("answer_map") or {}
    questions = []
    for group, section, question in _questions(selected):
        ids = [str(value) for value in question.get("question_ids") or [question["id"]]]
        questions.append({"key": group, "id": question["id"], "question_ids": ids,
                          "section": section.get("name", ""), "title": question.get("title", ""),
                          "description": question.get("description", ""), "type": question["type"],
                          "score": question.get("score", 0),
                          "answers": {qid: str(flat.get(qid, "")) for qid in ids}})
    return {"ok": True, "import_id": import_id, "name": imported.get("name") or import_id,
            "revision": _revision(imported), "paper_type": key,
            "paper_types": list(imported.get("paper_variants") or {}),
            "summary": selected.get("summary", {}), "questions": questions}


def read_exam_answers(import_id, paper_type=""):
    import scan_ui as service
    with service.EXAM_IMPORT_LOCK, service.AI_REVIEW_LOCK:
        import_id, _path, imported = service._review_import({"import_id": import_id})
        return _view(import_id, imported, paper_type)


def _validate_answers(question, values):
    ids = [str(value) for value in question.get("question_ids") or [question["id"]]]
    if not isinstance(values, dict) or set(values) != set(ids):
        raise ValueError("请提交本题全部答案项")
    result = {}
    for key in ids:
        value = values[key]
        if not isinstance(value, str) or len(value) > 2000:
            raise ValueError("每项答案需要使用 2000 字以内的文本")
        value = value.strip()
        kind = question["type"]
        if value and kind == "single":
            value = value.upper()
            if value not in {"A", "B", "C", "D"}:
                raise ValueError("单选题答案请填写 A、B、C 或 D")
        elif value and kind == "multiple":
            import re
            if not re.fullmatch(r"[A-Da-d\s,，、;；]+", value):
                raise ValueError("多选题答案请填写 A 至 D 的组合")
        elif value and kind == "true_false":
            if value.upper() not in {"T", "F", "TRUE", "FALSE", "对", "错", "正确", "错误"}:
                raise ValueError("判断题答案请填写 T / F 或 对 / 错")
        result[key] = _normalize_answer(value, kind)
    return result


def save_exam_answers(payload):
    import scan_ui as service
    with service.EXAM_IMPORT_LOCK, service.AI_REVIEW_LOCK:
        import_id, path, imported = service._review_import(payload)
        _check_revision(imported, payload.get("revision"))
        updated = copy.deepcopy(imported)
        key, selected = _select(updated, payload.get("paper_type"))
        _section, question = _find(selected, payload.get("question_key"))
        values = _validate_answers(question, payload.get("answers"))
        ids = list(values)
        previous = {qid: str((selected.get("answer_map") or {}).get(qid, "")) for qid in ids}
        if values == previous:
            return {**_view(import_id, imported, key), "changed": False}
        question["answer"] = values[ids[0]] if len(ids) == 1 else values
        selected["answer_map"] = answer_map(selected["exam"])
        summary = selected["summary"]
        old_labels = dict(zip(summary.get("answer_missing", []), summary.get("answer_missing_labels", [])))
        summary["answer_count"] = len(selected["answer_map"])
        summary["answer_missing"] = [qid for qid in selected.get("source_map", {}) if qid not in selected["answer_map"]]
        summary["answer_missing_labels"] = [old_labels.get(qid, qid) for qid in summary["answer_missing"]]
        variants = updated.get("paper_variants") or {}
        if variants:
            default = variants["A" if "A" in variants else next(iter(variants))]
            for field in ("exam", "answer_map", "source_map", "summary"):
                updated[field] = copy.deepcopy(default[field])
            updated["summary"].update(paper_types=list(variants), variant_count=len(variants))
        stamp = datetime.now(timezone.utc).isoformat()
        updated["answers_updated_at"] = stamp
        updated["answers_updated_by"] = str(payload.get("_actor_user_id") or "")
        history = path.parent / "answer_history" / (uuid.uuid4().hex + ".json")
        owner = str(payload.get("_actor_user_id") or "")
        service._write_json_atomic(history, {"at": stamp, "actor_user_id": owner,
            "paper_type": key, "question_key": payload["question_key"],
            "previous_answers": previous, "answers": values, "original": imported})
        service.PLATFORM_PERSISTENCE.sync_file(history, "exam_import", owner, history.relative_to(Path(service.IMPORT_ROOT)))
        writes = [(path.parent / "exam_with_answers.json", updated["exam"]["sections"]),
                  (path.parent / "answer_map.json", {"answer_map": updated["answer_map"]}), (path, updated)]
        originals = {target: target.read_bytes() if target.exists() else None for target, _ in writes}
        try:
            for target, content in writes:
                service._write_json_atomic(target, content)
                service.PLATFORM_PERSISTENCE.sync_file(target, "exam_import", owner, target.relative_to(Path(service.IMPORT_ROOT)))
        except Exception:
            for target, content in originals.items():
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    restore = target.with_name(target.name + ".restore-" + uuid.uuid4().hex)
                    restore.write_bytes(content)
                    restore.replace(target)
            raise
        return {**_view(import_id, updated, key), "changed": True}


def regrade_exam_question(payload):
    """Refresh only the selected source question on matching saved answer sheets."""
    import scan_ui as service
    with service.EXAM_IMPORT_LOCK, service.AI_REVIEW_LOCK:
        import_id, _path, imported = service._review_import(payload)
        _check_revision(imported, payload.get("revision"))
        paper_type, selected = _select(imported, payload.get("paper_type"))
        _section, question = _find(selected, payload.get("question_key"))
        required = [str(qid) for qid in question.get("question_ids") or [question["id"]]]
        if any(not str(selected.get("answer_map", {}).get(qid, "")).strip() for qid in required):
            raise ValueError("请补全本题全部参考答案后再重判")
        objective = question["type"] in {"single", "multiple", "true_false"}
        if not objective and not service.ai_is_configured():
            raise ValueError("请先在系统设置配置 AI，再启动本题重判；已保存的答案会保留")
        ids = {str(qid) for qid in question.get("question_ids") or [question["id"]]}
        ids.update(exam_review._structured_question_labels(question))
        sources = {}
        groups = exam_review._structured_ai_group_map(selected["exam"])
        for qid, group in groups.items():
            if group[0] == str(payload["question_key"]):
                sources[qid] = group
        ids.update(sources)
        matched, processing, completed, busy, errors, affected = 0, 0, 0, [], [], []
        run_id = uuid.uuid4().hex
        for path in sorted(Path(service.REVIEW_ROOT).glob("*/output/review.json")):
            try:
                review = service._read_json_file(path, "复核结果")
                if review.get("import_id") != import_id or review.get("deleted_at"):
                    continue
                if imported.get("paper_variants") and review.get("answer_paper_type") != paper_type:
                    continue
                target_items = [item for kind in ("items", "objective") for item in review.get(kind, [])
                                if str(item.get("question")) in ids]
                if not target_items:
                    continue
                matched += 1
                review_id = service.valid_review_id(path.parent.parent.name)
                if _busy(service, review):
                    busy.append(review_id)
                    continue
                # Use established score/sign/manual-review logic on a target-only copy.
                target = copy.deepcopy(review)
                for field in ("items", "objective"):
                    target[field] = [item for item in target.get(field, []) if str(item.get("question")) in ids]
                prepared = _prepare_review(target, imported, run_id)
                refreshed = copy.deepcopy(review)
                for field in ("items", "objective"):
                    replacements = {str(item["question"]): item for item in prepared[field]}
                    refreshed[field] = [replacements.get(str(item["question"]), item) for item in refreshed.get(field, [])]
                refreshed["objective_summary"] = exam_review._objective_summary(refreshed.get("objective", []))
                refreshed["review_summary"] = exam_review._review_summary(refreshed.get("items", []))
                refreshed["score_summary"] = exam_review._score_summary(refreshed)
                service._set_review_unconfirmed(refreshed)
                history = path.parent / "regrade_history" / (run_id + "-question.json")
                service._write_json_atomic(history, review)
                service.PLATFORM_PERSISTENCE.sync_file(history, "review", str(review.get("owner_user_id") or ""), history.relative_to(Path(service.REVIEW_ROOT)))
                record_action(refreshed, payload, "review.question_regraded")
                refreshed["answer_question_regrade"] = {"question_key": payload["question_key"],
                    "revision": _revision(imported), "at": datetime.now(timezone.utc).isoformat(), "history": history.relative_to(path.parent).as_posix()}
                service._write_review(path, refreshed)
                if prepared["items"]:
                    result = service.start_ai_question_review({"review_id": review_id, "question": str(prepared["items"][0]["question"])})
                    if result.get("ai_question_judgment", {}).get("status") == "处理中":
                        processing += 1
                    else:
                        completed += 1
                else:
                    completed += 1
                affected.append(review_id)
            except (ValueError, OSError) as error:
                errors.append({"review_id": path.parent.parent.name, "error": str(error)})
        return {"ok": True, "import_id": import_id, "question_key": payload["question_key"],
                "paper_type": paper_type, "matched": matched, "completed": completed,
                "ai_processing": processing, "skipped_busy": busy, "errors": errors,
                "review_ids": affected, "mode": "objective" if objective else "ai",
                "message": f"本题已处理 {completed} 份，AI 排队 {processing} 份，处理中跳过 {len(busy)} 份，失败 {len(errors)} 份"}
