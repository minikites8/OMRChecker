"""Shared-workspace review transactions and optimistic, per-question revisions."""

import errno
import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


class ReviewConflict(Exception):
    status = 409

    def __init__(self, review, questions=None):
        self.questions = questions or []
        self.revision = describe_review(review)["revision"]
        super().__init__("其他成员已更新这份试卷，请查看最新版本后重新提交")

    def response(self):
        return {"ok": False, "error": str(self), "code": "review_conflict",
                "revision": self.revision, "questions": self.questions}


class ReviewPreconditionRequired(Exception):
    status = 428

    def response(self):
        return {"ok": False, "error": "请先加载试卷最新版本再提交复核",
                "code": "review_revision_required"}


class SharedReviewLock:
    """Reentrant thread + OS file lock, shared by workers using the same data root.

    The lock file lives outside individual review directories so deletion and
    replacement of review.json cannot change the lock's identity.
    """

    def __init__(self, path):
        self.path = path
        self.thread_lock = threading.RLock()
        self.local = threading.local()

    def __enter__(self):
        self.thread_lock.acquire()
        depth = getattr(self.local, "depth", 0)
        if depth:
            self.local.depth = depth + 1
            return self
        stream = None
        try:
            path = Path(self.path())
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = path.open("a+b")
            if os.name == "nt":
                import msvcrt
                if path.stat().st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                deadline = time.monotonic() + 60
                while True:
                    stream.seek(0)
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as error:
                        if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or time.monotonic() >= deadline:
                            raise
                        time.sleep(0.02)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            self.local.stream = stream
            self.local.depth = 1
            return self
        except BaseException:
            if stream:
                stream.close()
            self.thread_lock.release()
            raise

    def __exit__(self, *_args):
        self.local.depth -= 1
        try:
            if self.local.depth == 0:
                stream = self.local.stream
                try:
                    if os.name == "nt":
                        import msvcrt
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()
                    del self.local.stream
        finally:
            self.thread_lock.release()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def question_revision(item):
    # Runtime paths and derived scores can be refreshed by saving another question.
    derived = {"handwriting_images", "final_status", "score_basis", "awarded_score"}
    return _digest({key: value for key, value in item.items() if key not in derived})


def describe_review(review):
    questions = {kind: {str(item["question"]): question_revision(item)
                        for item in review.get(field, [])}
                 for kind, field in (("subjective", "items"), ("objective", "objective"))}
    grade = {key: review.get(key) for key in (
        "student_name", "student_id", "identity_status", "paper_type", "answer_paper_type",
        "score_summary", "grade_confirmed", "grade_confirmed_at", "grade_confirmed_by")}
    from workspace_context import current_workspace
    workspace = current_workspace() or {"id": "shared", "name": "共享阅卷工作区"}
    return {"workspace_id": workspace["id"], "workspace_name": workspace["name"],
            "revision": _digest({"questions": questions, "grade": grade}),
            "questions": questions, "last_action": (review.get("review_activity") or [None])[-1]}


def attach_collaboration(review):
    review["collaboration"] = describe_review(review)
    return review


def check_revision(payload, review):
    expected = payload.get("expected_revision")
    if expected is None:
        if payload.get("_require_revision"):
            raise ReviewPreconditionRequired()
    elif expected != describe_review(review)["revision"]:
        raise ReviewConflict(review)


def check_decisions(payload, review):
    snapshot = describe_review(review)
    if payload.get("for_confirmation"):
        check_revision(payload, review)
    changed = []
    for field, kind in (("decisions", "subjective"), ("objective_decisions", "objective")):
        decisions = payload.get(field, [])
        if not isinstance(decisions, list):
            raise ValueError("复核结果须为题目列表")
        seen = set()
        for decision in decisions:
            if not isinstance(decision, dict):
                raise ValueError("复核题目格式无效")
            question = str(decision.get("question", ""))
            if question not in snapshot["questions"][kind] or question in seen:
                raise ValueError("复核题号无效或重复")
            seen.add(question)
            allowed_statuses = (None, "", "通过", "不通过", "待复核")
            if kind == "subjective" and question == "64":
                allowed_statuses += ("部分得分",)
            if decision.get("status", "") not in allowed_statuses:
                raise ValueError("复核结论无效")
            expected = decision.get("expected_revision")
            if expected is None:
                check_revision(payload, review)
            elif expected != snapshot["questions"][kind][question]:
                changed.append({"kind": kind, "question": question})
    if changed:
        raise ReviewConflict(review, changed)


def record_action(review, payload, action):
    actor = {"id": str(payload.get("_actor_user_id") or "local"),
             "display_name": str(payload.get("_actor_display_name") or "本地用户")}
    event = {"id": uuid.uuid4().hex, "action": action, "actor": actor,
             "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
             "questions": []}
    if action == "review.saved":
        for field, kind in (("decisions", "items"), ("objective_decisions", "objective")):
            ids = {str(item["question"]) for item in payload.get(field, [])}
            for item in review.get(kind, []):
                if str(item["question"]) in ids:
                    item["reviewed_by"] = actor
                    item["reviewed_at"] = event["at"]
                    item["review_event_id"] = event["id"]
                    event["questions"].append({"kind": kind, "question": str(item["question"])})
    elif action == "grade.confirmed":
        review["grade_confirmed_by"] = actor
    review["review_activity"] = (review.get("review_activity", []) + [event])[-100:]
    return event
