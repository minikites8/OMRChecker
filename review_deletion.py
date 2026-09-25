"""Scoped removal of one answer-card record, its identity, and cached indexes."""
from pathlib import Path
import json
import os
import re
import shutil
import stat
import uuid


class ReviewDeletionError(ValueError):
    def __init__(self, status, message, deleted=False):
        super().__init__(message)
        self.status = status
        self.deleted = deleted


def valid_review_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ReviewDeletionError(400, "请提供有效的批改编号")
    return value


def _guard(path, root):
    path, root = Path(path), Path(root).resolve()
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ReviewDeletionError(400, "批改文件路径校验失败")
    relative = path.absolute().relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ReviewDeletionError(400, "批改文件需要使用工作区内的普通目录和文件")
    return resolved


def _guard_tree(path, root):
    _guard(path, root)
    pending = [Path(path)]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                _guard(Path(entry.path), root)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))


def _authorize(review, actor, enforce_ownership):
    if enforce_ownership:
        actor = actor or {}
        actor_id = str(actor.get("sub") or actor.get("id") or "")
        if not actor_id:
            raise ReviewDeletionError(401, "请先登录")
        if actor.get("role") != "admin" and (
            not review.get("owner_user_id") or str(review["owner_user_id"]) != actor_id
        ):
            raise ReviewDeletionError(403, "请使用记录所属账号或管理员账号删除")


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _atomic_write(path, content):
    if content is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(".deleting-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _references(root, identifier):
    changes = []
    batch_ids = []
    batches = root / "batches"
    if batches.exists():
        _guard_tree(batches, root)
        for path in sorted(batches.glob("*/batch.json")):
            original = path.read_bytes()
            batch = json.loads(original.decode("utf-8"))
            entries = batch.get("reviews", [])
            kept = [entry for entry in entries if entry.get("review_id") != identifier]
            if len(kept) == len(entries):
                continue
            batch_ids.append(path.parent.name)
            batch["reviews"] = kept
            batch["total"] = len(kept)
            batch["completed"] = sum(entry.get("status") == "已完成" for entry in kept)
            batch["failed"] = sum(entry.get("status") == "失败" for entry in kept)
            batch["message"] = "当前保留 {} 份批改记录".format(len(kept))
            changes.append((path, original, _encoded(batch)))
    latest = root / "latest.json"
    if latest.exists():
        _guard(latest, root)
        original = latest.read_bytes()
        if json.loads(original.decode("utf-8")).get("review_id") == identifier:
            remaining = sorted((p.parent.parent.name for p in root.glob("*/output/review.json")
                                if p.parent.parent.name != identifier), reverse=True)
            content = _encoded({"review_id": remaining[0]}) if remaining else None
            changes.append((latest, original, content))
    return changes, batch_ids


def delete_record(root, identifier, *, actor=None, enforce_ownership=False,
                  is_busy=None, persistence=None):
    """Caller holds the review, preview, and batch locks for this transaction."""
    identifier = valid_review_id(identifier)
    root = Path(root).resolve()
    folder = _guard(root / identifier, root)
    trash_root = root.parent / ("." + root.name + "-deleted")
    _guard(trash_root, root.parent)
    trash = trash_root / identifier
    journal = trash_root / (identifier + ".json")
    _guard(trash, root.parent)
    _guard(journal, root.parent)

    # A cleanup retry uses the same record ID and repeats the owner check.
    if journal.is_file():
        deletion = json.loads(journal.read_text(encoding="utf-8"))
        _authorize(deletion, actor, enforce_ownership)
        if folder.exists():
            raise ReviewDeletionError(409, "删除事务待恢复，请稍后重试")
    else:
        report_path = _guard(folder / "output/review.json", root)
        if not report_path.is_file():
            raise ReviewDeletionError(404, "批改记录已删除或编号已失效")
        review = json.loads(report_path.read_text(encoding="utf-8"))
        _authorize(review, actor, enforce_ownership)
        _guard_tree(folder, root)
        changes, batch_ids = _references(root, identifier)
        if is_busy and is_busy(review, batch_ids):
            raise ReviewDeletionError(409, "批改或姓名识别正在运行，请在任务完成后删除")
        if trash.exists():
            raise ReviewDeletionError(409, "批改文件清理正在等待处理，请稍后重试")
        if persistence is not None:
            persistence.delete_review(identifier)
        trash_root.mkdir(parents=True, exist_ok=True)
        journal.write_bytes(_encoded({"review_id": identifier, "owner_user_id": review.get("owner_user_id", "")}))
        try:
            folder.replace(trash)
        except OSError:
            journal.unlink(missing_ok=True)
            raise
        try:
            for path, _before, after in changes:
                _atomic_write(path, after)
        except Exception:
            # Restore all index files, including the file whose replacement failed.
            for path, before, _after in changes:
                path.write_bytes(before)
            trash.replace(folder)
            journal.unlink(missing_ok=True)
            raise
    try:
        if trash.exists():
            _guard_tree(trash, trash_root)
            shutil.rmtree(trash)
        journal.unlink(missing_ok=True)
    except OSError as error:
        raise ReviewDeletionError(503, "记录已移除，文件清理暂时失败，请重试删除", deleted=True) from error
    return {"ok": True, "review_id": identifier, "deleted": True, "candidate_deleted": True}
