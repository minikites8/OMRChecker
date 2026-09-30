"""Regrade saved answers against their imported paper; preserve manual decisions."""
from __future__ import annotations

import copy
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import exam_review
from review_collaboration import record_action


AI_RESULT_FIELDS = (
    "ai_status", "ai_score", "ai_confidence", "ai_visual_text", "ai_reason",
    "ai_corrected_answer", "ai_review_policy", "ai_visual_evidence", "ai_previous_judgment",
)


def _prepare_review(review, imported, run_id):
    selected = exam_review._import_variant_for_review(imported, review.get("answer_paper_type", ""))
    variants = imported.get("paper_variants") or {}
    if variants and review.get("answer_paper_type") not in variants:
        raise ValueError("请先确认答卷对应的卷型")
    exam = selected.get("exam", {})
    sources, answers = exam_review._structured_alias_maps(
        exam, selected.get("source_map", {}), selected.get("answer_map", {}))
    scores = exam_review._structured_score_map(exam)
    metadata = exam_review._structured_question_metadata(exam)
    refreshed = copy.deepcopy(review)
    for item in refreshed.get("objective", []):
        question = str(item["question"])
        item["expected"] = exam_review._objective_expected(answers.get(question, ""))
        marked = item.get("recognized", "")
        warning = item.get("recognition_warning", "")
        item["auto_status"] = ("需人工复核" if warning else "自动通过"
                               if exam_review._objective_match(question, marked, item["expected"])
                               else "需人工复核" if marked else "空白")
        item["final_answer"] = (item.get("reviewed_answer", "") if item.get("override_answer")
                                else "" if warning else marked)
        status = item.get("manual_status", "")
        if item.get("override_answer") and not status:
            status = ("待复核" if not item["expected"] else "通过"
                      if exam_review._objective_match(question, item["final_answer"], item["expected"])
                      else "不通过")
        item["final_status"] = status or item["auto_status"]
        item["score"] = scores.get(question, 0)
        item.update(metadata.get(question, {}))
        item["regrade_id"] = run_id
    for item in refreshed.get("items", []):
        question = str(item["question"])
        item["source_content"] = sources.get(question, "")
        item["expected_answer"] = exam_review._normalize_correction_expected(
            question, answers.get(question, ""), item["source_content"])
        item["score"] = scores.get(question, 0)
        item.update(metadata.get(question, {}))
        for key in AI_RESULT_FIELDS:
            item.pop(key, None)
        item["ai_status"] = "AI待处理"
        item["regrade_id"] = run_id
    exam_review.attach_structured_ai_groups(refreshed, exam)
    refreshed.pop("ai_question_judgment", None)
    refreshed["ai_judgment"] = {"status": "待运行", "enabled": False, "processed": 0,
                                "message": "已更新题号、参考答案和分值，等待重新审核"}
    refreshed["source"] = {**refreshed.get("source", {}), **selected.get("summary", {})}
    exam_review.refresh_rule_judgments(refreshed)
    refreshed["objective_summary"] = exam_review._objective_summary(refreshed.get("objective", []))
    refreshed["review_summary"] = exam_review._review_summary(refreshed.get("items", []))
    refreshed["score_summary"] = exam_review._score_summary(refreshed)
    return refreshed


def _busy(service, review):
    review_id = review["review_id"]
    if (review.get("ai_judgment") or {}).get("status") == "处理中":
        return True
    if (review.get("ai_question_judgment") or {}).get("status") == "处理中":
        return True
    future = service.AI_REVIEW_WORKERS.get(review_id)
    if future and not future.done():
        return True
    return any(key.startswith(review_id + ":") and not task.done()
               for key, task in service.AI_QUESTION_WORKERS.items())


def regrade_exam(payload):
    """Reuse persisted recognition/crops, update scores, and enqueue fresh AI judgments."""
    import scan_ui as service

    import_id, _path, imported = service._review_import(payload)
    requested = payload.get("review_ids")
    if requested is not None and (not isinstance(requested, list) or not requested):
        raise ValueError("请选择需要重新批改的答卷")
    ids = []
    if requested is not None:
        for value in requested:
            try:
                review_id = service.valid_review_id(value)
            except service.ReviewDeletionError as error:
                raise ValueError(str(error)) from error
            _id, _path, review = service._load_review(review_id)
            if review.get("import_id") != import_id:
                raise ValueError("所选答卷与试卷不匹配")
            if review_id not in ids:
                ids.append(review_id)
    else:
        for path in sorted(Path(service.REVIEW_ROOT).glob("*/output/review.json")):
            review = service._read_json_file(path, "复核结果")
            if review.get("import_id") == import_id and not review.get("deleted_at"):
                ids.append(service.valid_review_id(path.parent.parent.name))
    if not ids:
        raise ValueError("该试卷暂无已保存答卷，请先使用开始批改录入答卷")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    entries, busy, failures = [], [], []
    for review_id in ids:
        try:
            with service.AI_REVIEW_LOCK:
                _id, path, review = service._load_review(review_id)
                if review.get("import_id") != import_id or review.get("deleted_at"):
                    raise ValueError("答卷归属已更新，请刷新列表")
                if _busy(service, review):
                    busy.append(review_id)
                    continue
                refreshed = _prepare_review(review, imported, run_id)
                history = path.parent / "regrade_history" / (run_id + ".json")
                service._write_json_atomic(history, review)
                service.PLATFORM_PERSISTENCE.sync_file(
                    history, "review", str(review.get("owner_user_id") or ""),
                    history.relative_to(Path(service.REVIEW_ROOT)))
                service._set_review_unconfirmed(refreshed)
                refreshed["regrade"] = {"id": run_id, "at": datetime.now(timezone.utc).isoformat(),
                                        "history": history.relative_to(path.parent).as_posix(),
                                        "manual_decisions_preserved": True}
                record_action(refreshed, payload, "review.regraded")
                service._write_review(path, refreshed)
                refreshed = service.start_ai_review({"review_id": review_id})
                entries.append(service._compact_review(refreshed, review.get("student_name") or review_id))
        except (ValueError, OSError) as error:
            failures.append({"review_id": review_id, "error": str(error)})
    batch = {"ok": True, "batch_id": run_id, "import_id": import_id,
             "import_name": imported.get("name") or import_id, "kind": "regrade",
             "status": "已完成", "created_at": time.time(),
             "owner_user_id": str(payload.get("_actor_user_id") or ""),
             "total": len(entries) + len(failures), "completed": len(entries), "failed": len(failures),
             "skipped_busy": busy, "regrade_errors": failures, "concurrency": 1,
             "reviews": entries + [{"status": "失败", "label": e["review_id"],
                                      "review_id": "", "error": e["error"]} for e in failures]}
    batch["ai_processing"] = sum(e.get("ai_judgment", {}).get("status") == "处理中" for e in entries)
    batch["phase"] = "AI处理中" if batch["ai_processing"] else "重新批改完成"
    batch["message"] = "已重新批改 {} 份；处理中跳过 {} 份；失败 {} 份".format(len(entries), len(busy), len(failures))
    _id, batch_path = service._batch_path(run_id)
    with service.BATCH_REVIEW_LOCK:
        service._write_batch(batch_path, batch)
    return batch
