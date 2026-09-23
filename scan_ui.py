"""Local browser UI for OMRChecker."""

import argparse
import base64
import binascii
import csv
import json
import inspect
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import urlopen

from sheet_designer import generate_sheet_package
from ai_judge import ai_config, ai_is_configured
from exam_import import import_exam_and_answers
from exam_review import apply_ai_review, apply_manual_review, attach_structured_scores, build_review_from_structured, refresh_rule_judgments, _import_variant_for_review as exam_review_variant_for_review
from scan_templates import TemplateManager

PROJECT_ROOT = Path(__file__).resolve().parent
UI_ROOT = PROJECT_ROOT / "ui"
TEMPLATE_ROOT = PROJECT_ROOT / "inputs" / "phone_scan"
TEMPLATE_MANAGER = TemplateManager(PROJECT_ROOT, TEMPLATE_ROOT)
JOBS_ROOT = PROJECT_ROOT / "outputs" / "scan_ui"
SHEETS_ROOT = PROJECT_ROOT / "output" / "pdf" / "web_designer"
REVIEW_ROOT = PROJECT_ROOT / "outputs" / "answer_review"
IMPORT_ROOT = PROJECT_ROOT / "outputs" / "exam_imports"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
MAX_REQUEST_BYTES = 256 * 1024 * 1024
MAX_FILE_BYTES = 24 * 1024 * 1024
AI_REVIEW_LOCK = threading.RLock()
BATCH_REVIEW_LOCK = threading.RLock()
AI_REVIEW_WORKERS = {}
BATCH_REVIEW_WORKERS = {}


def _configured_workers(name, default, maximum=4):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(maximum, value))


DEFAULT_REVIEW_WORKERS = _configured_workers(
    "OMR_REVIEW_WORKERS", min(4, max(2, (os.cpu_count() or 2) // 2))
)
DEFAULT_AI_WORKERS = _configured_workers("OMR_AI_WORKERS", 3)
REVIEW_EXECUTOR = ThreadPoolExecutor(
    max_workers=DEFAULT_REVIEW_WORKERS, thread_name_prefix="omr-review"
)
AI_REVIEW_EXECUTOR = ThreadPoolExecutor(
    max_workers=DEFAULT_AI_WORKERS, thread_name_prefix="omr-ai"
)


class ScanFailure(RuntimeError):
    def __init__(self, message, log=""):
        super().__init__(message)
        self.log = log


def safe_filename(name, index=1):
    candidate = Path(str(name or "")).name.strip()
    candidate = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", candidate)
    suffix = Path(candidate).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("支持 JPG、JPEG、PNG 和 PDF 文件")
    stem = Path(candidate).stem.strip(" .") or "scan_{:02d}".format(index)
    return "{}{}".format(stem[:96], suffix)


def resolve_under(root, relative_path):
    base = root.resolve()
    candidate = (base / relative_path).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError("路径超出允许目录")
    return candidate


def template_references(template_path):
    data = json.loads(template_path.read_text(encoding="utf-8-sig"))
    references = set()
    for processor in data.get("preProcessors", []):
        options = processor.get("options", {})
        for key in ("reference", "relativePath"):
            value = options.get(key)
            if value:
                references.add(Path(value).name)
    return references


def copy_template_assets(destination, template_id=None):
    template_root = TEMPLATE_MANAGER.scan_root(template_id)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("template.json", "config.json"):
        source = template_root / name
        if not source.exists():
            raise ScanFailure("缺少扫描模板文件：{}".format(source))
        shutil.copy2(str(source), str(destination / name))
    evaluation = template_root / "evaluation.json"
    if evaluation.exists():
        shutil.copy2(str(evaluation), str(destination / evaluation.name))
    for name in template_references(template_root / "template.json"):
        source = template_root / name
        if not source.exists():
            raise ScanFailure("缺少模板参考文件：{}".format(source))
        shutil.copy2(str(source), str(destination / name))


def decode_upload(file_object):
    encoded = str(file_object.get("data", ""))
    if "," in encoded and encoded.lstrip().startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("图片数据格式错误") from error
    if not payload:
        raise ValueError("图片内容为空")
    if len(payload) > MAX_FILE_BYTES:
        raise ValueError("单个文件大小上限为 24 MB")
    return payload


def save_uploads(files, destination):
    if not files:
        raise ValueError("请选择至少一张试卷图片")
    saved = []
    used_names = set()
    for index, file_object in enumerate(files, 1):
        name = safe_filename(file_object.get("name"), index)
        stem, suffix = Path(name).stem, Path(name).suffix
        unique_name = name
        counter = 2
        while unique_name.lower() in used_names:
            unique_name = "{}_{}{}".format(stem, counter, suffix)
            counter += 1
        used_names.add(unique_name.lower())
        path = destination / unique_name
        path.write_bytes(decode_upload(file_object))
        saved.append(path)
    return saved


def copy_demo_images(destination, template_id=None):
    template_root = TEMPLATE_MANAGER.scan_root(template_id)
    references = template_references(template_root / "template.json")
    images = sorted(
        path for path in template_root.iterdir()
        if path.is_file()
        and path.suffix.lower() in ALLOWED_EXTENSIONS
        and path.name not in references
    )
    if not images:
        raise ScanFailure("示例目录中缺少试卷图片")
    for source in images:
        shutil.copy2(str(source), str(destination / source.name))
    return images


def latest_result_csv(output_dir):
    files = list((output_dir / "Results").glob("*.csv"))
    if not files:
        raise ScanFailure("扫描程序未生成结果 CSV")
    return max(files, key=lambda path: path.stat().st_mtime)


def job_url(job_id, job_root, file_path):
    try:
        relative = file_path.resolve().relative_to(job_root.resolve())
    except ValueError:
        return ""
    return "/jobs/{}/{}".format(job_id, quote(relative.as_posix()))


def sheet_url(file_path):
    relative = file_path.resolve().relative_to(SHEETS_ROOT.resolve())
    return "/sheets/{}".format(quote(relative.as_posix()))


def read_results(csv_path, job_id, job_root):
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        rows = []
        for row in reader:
            preview = Path(row.get("output_path", ""))
            row["_preview_url"] = (
                job_url(job_id, job_root, preview) if preview.exists() else ""
            )
            rows.append(row)
    return columns, rows


def run_exam_import(payload):
    exam_text = str(payload.get("exam_text", ""))
    answer_text = str(payload.get("answer_text", ""))
    if not exam_text.strip():
        raise ValueError("\u8bf7\u63d0\u4f9b\u8bd5\u5377 JSON")
    imported = import_exam_and_answers(exam_text, answer_text)
    import_id = "{}-{}".format(datetime.now().strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
    import_root = IMPORT_ROOT / import_id
    import_root.mkdir(parents=True, exist_ok=False)
    normalized_path = import_root / "normalized_exam.json"
    normalized_path.write_text(json.dumps(imported, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    exam_path = import_root / "exam_with_answers.json"
    exam_path.write_text(json.dumps(imported["exam"]["sections"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    answers_path = import_root / "answer_map.json"
    answers_path.write_text(json.dumps({"answer_map": imported["answer_map"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "import_id": import_id,
        "summary": imported["summary"],
        "sections": imported["exam"]["sections"],
        "answer_map": imported["answer_map"],
        "answer_count": len(imported["answer_map"]),
        "answer_missing": imported["summary"].get("answer_missing", []),
        "paper_types": imported.get("paper_types", []),
        "variant_count": len(imported.get("paper_variants") or {}),
        "normalized_url": "/imports/{}/normalized_exam.json".format(import_id),
        "exam_url": "/imports/{}/exam_with_answers.json".format(import_id),
        "answers_url": "/imports/{}/answer_map.json".format(import_id),
    }


def list_exam_imports(limit=50):
    """Return saved structured imports in newest-first order for the review picker."""
    imports = []
    if not IMPORT_ROOT.is_dir():
        return imports
    for directory in sorted(IMPORT_ROOT.iterdir(), key=lambda path: path.name, reverse=True):
        if not directory.is_dir() or not re.fullmatch(r"[A-Za-z0-9_-]+", directory.name):
            continue
        normalized = directory / "normalized_exam.json"
        if not normalized.is_file():
            continue
        try:
            data = json.loads(normalized.read_text(encoding="utf-8"))
            summary = data["summary"]
            if not isinstance(summary, dict) or not isinstance(data["exam"]["sections"], list):
                continue
        except (OSError, KeyError, TypeError, ValueError):
            continue
        imports.append({
            "import_id": directory.name,
            "summary": summary,
            "answer_count": len(data.get("answer_map") or {}),
            "answer_missing": summary.get("answer_missing", []),
            "paper_types": data.get("paper_types", []),
            "variant_count": len(data.get("paper_variants") or {}),
        })
        if len(imports) >= limit:
            break
    return imports


def _review_import(payload):
    import_id = str(payload.get("import_id") or "").strip()
    if not import_id:
        raise ValueError("请先导入结构化试卷与答案")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", import_id):
        raise ValueError("导入编号格式错误")
    normalized_path = resolve_under(IMPORT_ROOT, Path(import_id) / "normalized_exam.json")
    if not normalized_path.is_file():
        raise ValueError("导入的试卷不存在")
    imported = json.loads(normalized_path.read_text(encoding="utf-8"))
    return import_id, normalized_path, imported


def _create_review_report(import_id, normalized_path, imported, card_files,
                          batch_id="", batch_index=0, label="", update_latest=True,
                          template_id=None):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    review_id = "{}-{}".format(stamp, uuid.uuid4().hex[:6])
    review_root = REVIEW_ROOT / review_id
    input_dir = review_root / "input"
    output_dir = review_root / "output"
    input_dir.mkdir(parents=True, exist_ok=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    card_paths = save_uploads(card_files, input_dir)
    shutil.copy2(normalized_path, input_dir / "normalized_exam.json")
    selected_template = TEMPLATE_MANAGER.get(template_id)
    review_arguments = {
        "image_dir": output_dir / "handwriting",
    }
    if "template_config" in inspect.signature(build_review_from_structured).parameters:
        review_arguments["template_config"] = selected_template.get("recognition", {})
    report = build_review_from_structured(
        imported["exam"],
        {"answer_map": imported.get("answer_map", {})},
        card_paths,
        **review_arguments,
    )
    report.setdefault("paper_type", "")
    report.setdefault("paper_type_status", "待复核")
    report["review_id"] = review_id
    report["import_id"] = import_id
    report["template_id"] = selected_template["id"]
    report["template_name"] = selected_template["name"]
    report["card_files"] = [path.name for path in card_paths]
    if batch_id:
        report["batch_id"] = batch_id
        report["batch_index"] = batch_index
        report["batch_label"] = label or "第{}份".format(batch_index + 1)
    for item in report.get("items", []):
        item["handwriting_urls"] = [
            "/reviews/{}/output/handwriting/{}".format(review_id, Path(path).name)
            for path in item.get("handwriting_images", [])
        ]
        item.pop("handwriting_images", None)
    for objective in report.get("objective", []):
        image = objective.pop("bubble_image", "")
        if image:
            objective["bubble_url"] = "/reviews/{}/output/handwriting/objective/{}".format(
                review_id, Path(image).name
            )
    report_path = output_dir / "review.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if update_latest:
        latest_path = REVIEW_ROOT / "latest.json"
        latest_path.write_text(json.dumps({"review_id": review_id}, ensure_ascii=False) + "\n", encoding="utf-8")
    if ai_is_configured():
        return start_ai_review({"review_id": review_id})
    report["ai_judgment"] = {"status": "未配置", "enabled": False, "processed": 0, "message": "AI接口等待配置"}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report_url"] = "/reviews/{}/output/review.json".format(review_id)
    return report


def run_review_job(payload):
    import_id, normalized_path, imported = _review_import(payload)
    card_files = payload.get("card_files") or []
    if not card_files:
        raise ValueError("请选择答题卡照片或 PDF")
    return _create_review_report(
        import_id, normalized_path, imported, card_files, update_latest=True,
        template_id=payload.get("template_id")
    )


def _normalize_card_groups(payload):
    explicit = payload.get("card_groups") or []
    groups = []
    if explicit:
        for index, group in enumerate(explicit):
            files = group.get("files") if isinstance(group, dict) else None
            if not isinstance(files, list) or not files:
                raise ValueError("第{}份答题卡缺少文件".format(index + 1))
            groups.append({
                "label": str(group.get("label") or "第{}份".format(index + 1))[:80],
                "files": files,
            })
    else:
        files = payload.get("card_files") or []
        if not files:
            raise ValueError("请选择答题卡照片或 PDF")
        pages_per_card = max(1, min(4, int(payload.get("pages_per_card") or 2)))
        pending_images = []

        def flush_images():
            while pending_images:
                chunk = pending_images[:pages_per_card]
                del pending_images[:pages_per_card]
                groups.append({"label": "第{}份".format(len(groups) + 1), "files": chunk})

        for file_object in files:
            extension = Path(str(file_object.get("name") or "")).suffix.lower()
            if extension == ".pdf":
                flush_images()
                groups.append({
                    "label": Path(str(file_object.get("name") or "答题卡")).stem[:80],
                    "files": [file_object],
                })
            else:
                pending_images.append(file_object)
        flush_images()
    if len(groups) > 24:
        raise ValueError("一次最多并发批改 24 份答题卡")
    return groups


def _batch_path(batch_id):
    clean_id = re.sub(r"[^A-Za-z0-9_-]", "", str(batch_id or ""))
    if not clean_id:
        raise ValueError("缺少批量任务编号")
    return clean_id, resolve_under(REVIEW_ROOT, Path("batches") / clean_id / "batch.json")


def _write_batch(path, batch):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _compact_review(report, label=""):
    return {
        "status": "已完成",
        "label": label or report.get("batch_label") or report.get("review_id", ""),
        "review_id": report.get("review_id", ""),
        "student_id": report.get("student_id", ""),
        "student_name": report.get("student_name", ""),
        "student_name_status": report.get("student_name_status", "待识别"),
        "paper_type": report.get("paper_type", ""),
        "answer_paper_type": report.get("answer_paper_type", ""),
        "answer_selection_status": report.get("answer_selection_status", ""),
        "available_paper_types": report.get("available_paper_types", []),
        "score_summary": report.get("score_summary", {}),
        "review_summary": report.get("review_summary", {}),
        "objective_summary": report.get("objective_summary", {}),
        "ai_judgment": report.get("ai_judgment", {}),
        "report_url": report.get("report_url", ""),
    }


def _batch_review_worker(batch_id, import_id, normalized_path, imported, groups, concurrency, template_id):
    _clean_id, batch_path = _batch_path(batch_id)
    started = time.perf_counter()
    try:
        with BATCH_REVIEW_LOCK:
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            batch["status"] = "处理中"
            for entry in batch["reviews"]:
                entry["status"] = "处理中"
            _write_batch(batch_path, batch)
        results = [None] * len(groups)
        batch_slots = threading.Semaphore(concurrency)

        def run_group(index, group):
            with batch_slots:
                return _create_review_report(
                    import_id,
                    normalized_path,
                    imported,
                    group["files"],
                    batch_id,
                    index,
                    group["label"],
                    False,
                    template_id,
                )

        futures = {
            REVIEW_EXECUTOR.submit(run_group, index, group): index
            for index, group in enumerate(groups)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                report = future.result()
                entry = _compact_review(report, groups[index]["label"])
                results[index] = entry
            except Exception as error:
                entry = {
                    "status": "失败",
                    "label": groups[index]["label"],
                    "review_id": "",
                    "error": str(error),
                }
                results[index] = entry
            with BATCH_REVIEW_LOCK:
                latest = json.loads(batch_path.read_text(encoding="utf-8"))
                latest["reviews"][index] = entry
                latest["completed"] = sum(item.get("status") == "已完成" for item in latest["reviews"])
                latest["failed"] = sum(item.get("status") == "失败" for item in latest["reviews"])
                latest["message"] = "已完成 {}/{} 份".format(
                    latest["completed"] + latest["failed"], latest["total"]
                )
                _write_batch(batch_path, latest)
        successful = [item for item in results if item and item.get("review_id")]
        with BATCH_REVIEW_LOCK:
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            batch["status"] = "已完成" if batch.get("failed", 0) == 0 else "部分完成"
            batch["duration_seconds"] = round(time.perf_counter() - started, 2)
            batch["message"] = "并发批改完成：成功 {} 份，失败 {} 份".format(
                batch.get("completed", 0), batch.get("failed", 0)
            )
            _write_batch(batch_path, batch)
        if successful:
            (REVIEW_ROOT / "latest.json").write_text(
                json.dumps({"review_id": successful[0]["review_id"]}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    except Exception as error:
        with BATCH_REVIEW_LOCK:
            try:
                batch = json.loads(batch_path.read_text(encoding="utf-8"))
                batch["status"] = "异常"
                batch["message"] = "批量批改失败：{}".format(error)
                batch["duration_seconds"] = round(time.perf_counter() - started, 2)
                _write_batch(batch_path, batch)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
    finally:
        with BATCH_REVIEW_LOCK:
            BATCH_REVIEW_WORKERS.pop(batch_id, None)


def start_batch_review(payload):
    import_id, normalized_path, imported = _review_import(payload)
    groups = _normalize_card_groups(payload)
    concurrency = max(1, min(DEFAULT_REVIEW_WORKERS, int(payload.get("concurrency") or DEFAULT_REVIEW_WORKERS)))
    concurrency = min(concurrency, len(groups))
    selected_template = TEMPLATE_MANAGER.get(payload.get("template_id"))
    batch_id = "{}-{}".format(datetime.now().strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
    _clean_id, batch_path = _batch_path(batch_id)
    batch = {
        "ok": True,
        "batch_id": batch_id,
        "import_id": import_id,
        "template_id": selected_template["id"],
        "template_name": selected_template["name"],
        "status": "等待中",
        "message": "正在准备并发批改",
        "total": len(groups),
        "completed": 0,
        "failed": 0,
        "concurrency": concurrency,
        "duration_seconds": 0,
        "reviews": [
            {"status": "等待中", "label": group["label"], "review_id": ""}
            for group in groups
        ],
    }
    with BATCH_REVIEW_LOCK:
        _write_batch(batch_path, batch)
        worker = threading.Thread(
            target=_batch_review_worker,
            args=(batch_id, import_id, normalized_path, imported, groups, concurrency, selected_template["id"]),
            daemon=True,
            name="omr-batch-{}".format(batch_id),
        )
        BATCH_REVIEW_WORKERS[batch_id] = worker
        worker.start()
    return batch


def read_batch_status(batch_id):
    clean_id, batch_path = _batch_path(batch_id)
    if not batch_path.is_file():
        raise ValueError("批量任务不存在")
    with BATCH_REVIEW_LOCK:
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
    ai_running = 0
    refreshed_entries = {}
    for index, entry in enumerate(batch.get("reviews", [])):
        review_id = entry.get("review_id")
        if not review_id:
            continue
        try:
            report = read_review_status(review_id)
        except ValueError:
            continue
        refreshed = _compact_review(report, entry.get("label", ""))
        if refreshed != entry:
            refreshed_entries[index] = refreshed
        if (report.get("ai_judgment") or {}).get("status") == "处理中":
            ai_running += 1
    if refreshed_entries:
        with BATCH_REVIEW_LOCK:
            latest = json.loads(batch_path.read_text(encoding="utf-8"))
            for index, entry in refreshed_entries.items():
                if index < len(latest.get("reviews", [])):
                    latest["reviews"][index] = entry
            _write_batch(batch_path, latest)
            batch = latest
    batch["ai_processing"] = ai_running
    batch["phase"] = "AI处理中" if ai_running else batch.get("status", "")
    batch["batch_id"] = clean_id
    return batch


def _load_review(review_id):
    clean_id = re.sub(r"[^A-Za-z0-9_-]", "", str(review_id or ""))
    if not clean_id:
        raise ValueError("缺少复核任务编号")
    review_path = resolve_under(REVIEW_ROOT, Path(clean_id) / "output" / "review.json")
    if not review_path.is_file():
        raise ValueError("复核任务不存在")
    return clean_id, review_path, json.loads(review_path.read_text(encoding="utf-8"))


def _write_review(path, review):
    path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_review_scores(review):
    changed = False
    summary = review.get("score_summary") or {}
    if float(summary.get("possible_score", 0) or 0) <= 0:
        import_id = str(review.get("import_id") or "").strip()
        if import_id and re.fullmatch(r"[A-Za-z0-9_-]+", import_id):
            normalized_path = resolve_under(IMPORT_ROOT, Path(import_id) / "normalized_exam.json")
            if normalized_path.is_file():
                imported = json.loads(normalized_path.read_text(encoding="utf-8"))
                selected_import = exam_review_variant_for_review(imported, review.get("answer_paper_type", ""))
                attach_structured_scores(review, selected_import.get("exam", {}))
                changed = True
    return refresh_rule_judgments(review) or changed


def read_review_status(review_id):
    with AI_REVIEW_LOCK:
        clean_id, path, review = _load_review(review_id)
        if _ensure_review_scores(review):
            _write_review(path, review)
    review["report_url"] = "/reviews/{}/output/review.json".format(clean_id)
    return review


OBJECTIVE_VIEW_LOCK = threading.Lock()


def read_objective_view(review_id):
    """Return cached scan geometry, building old reviews once without OCR or AI."""
    from objective_view import build_objective_view, VIEW_VERSION
    from exam_review import _load_page, _align_and_order_pages, _reference_pages, PAGE_W, PAGE_H
    from answer_alignment import align_printed_region
    clean_id, path, review = _load_review(review_id)
    destination = path.parent / "handwriting" / "objective_view"
    manifest_path = destination / "manifest.json"
    with OBJECTIVE_VIEW_LOCK:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
        if not manifest or manifest.get("version") != VIEW_VERSION:
            input_dir = path.parent.parent / "input"
            names = review.get("card_files", [])
            files = [resolve_under(input_dir, name) for name in names] if names else sorted(
                p for p in input_dir.iterdir() if p.suffix.lower() in ALLOWED_EXTENSIONS)
            pages = []
            for file in files:
                pages.extend(image for _name, image in _load_page(file))
            if not pages:
                raise ValueError("答题卡原始文件缺失，请重新上传扫描件")
            normalized, _scores = _align_and_order_pages(pages)
            references = _reference_pages()
            regions = {}
            if references:
                for region in ("single", "multiple_tf"):
                    _aligned, regions[region] = align_printed_region(normalized[0], references[0], region)
            manifest = build_objective_view(normalized[0], destination, regions, PAGE_W, PAGE_H)
    return {"ok": True, "review_id": clean_id, **manifest,
            "asset_base": "/reviews/{}/output/handwriting/objective_view/".format(clean_id)}


def confirm_review_grade(payload):
    with AI_REVIEW_LOCK:
        review_id, review_path, review = _load_review(payload.get("review_id"))
        _ensure_review_scores(review)
        blockers = grade_confirmation_blockers(review)
        review["grade_blockers"] = blockers
        if blockers:
            review["grade_confirmation_status"] = "待处理"
            _write_review(review_path, review)
            raise ValueError("确认成绩前请完成：{}".format("；".join(blockers[:12])))
        review["grade_confirmed"] = True
        review["grade_confirmation_status"] = "已确认"
        review["grade_confirmed_at"] = datetime.now().isoformat(timespec="seconds")
        review["grade_blockers"] = []
        _write_review(review_path, review)
    review["report_url"] = "/reviews/{}/output/review.json".format(review_id)
    return review


def export_confirmed_grades(selected_ids=None):
    records, _skipped = CANDIDATE_MANAGER.records()
    selected = {str(value).strip() for value in (selected_ids or []) if str(value).strip()}
    if selected_ids is not None and not selected:
        raise ValueError("请选择至少一名考生")
    grades = []
    for record in records:
        if selected and record.get("review_id") not in selected:
            continue
        if not record.get("grade_confirmed"):
            continue
        summary = record.get("score_summary") or {}
        possible = float(summary.get("possible_score", 0) or 0)
        total = float(summary.get("total_score", 0) or 0)
        grades.append({
            "review_id": record.get("review_id", ""),
            "student_name": record.get("student_name", ""),
            "student_id": record.get("student_id", ""),
            "paper_type": record.get("paper_type", ""),
            "score": summary.get("total_score", 0),
            "possible_score": summary.get("possible_score", 0),
            "percentage": round(total / possible * 100, 2) if possible else 0,
            "objective_score": summary.get("objective_score", 0),
            "text_score": summary.get("text_score", 0),
            "failed_score": summary.get("failed_score", 0),
            "confirmed_at": record.get("grade_confirmed_at", ""),
            "question_scores": record.get("question_scores", []),
        })
    if not grades:
        raise ValueError("所选考生暂无已确认成绩")
    return {"ok": True, "exported_at": datetime.now().isoformat(timespec="seconds"), "count": len(grades), "grades": grades}

def save_manual_review(payload):
    with AI_REVIEW_LOCK:
        review_id, review_path, review = _load_review(payload.get("review_id"))
        _ensure_review_scores(review)
        review = apply_manual_review(review, payload.get("decisions", []), payload.get("objective_decisions", []))
        review["grade_confirmed"] = False
        review["grade_confirmed_at"] = ""
        review["grade_confirmation_status"] = "待处理" if grade_confirmation_blockers(review) else "待确认"
        review["grade_blockers"] = grade_confirmation_blockers(review)
        _write_review(review_path, review)
    review["report_url"] = "/reviews/{}/output/review.json".format(review_id)
    return review


def run_ai_review(payload):
    review_id, review_path, review = _load_review(payload.get("review_id"))
    _ensure_review_scores(review)
    for item in review.get("items", []):
        item["handwriting_images"] = [
            str((review_path.parent / "handwriting" / Path(urlparse(url).path).name).resolve())
            for url in item.get("handwriting_urls", [])
        ]

    def save_progress(progress):
        with AI_REVIEW_LOCK:
            _id, path, latest = _load_review(review_id)
            label = progress["current_group"]
            done = progress["completed_groups"]
            total = progress["group_count"]
            message = ("正在审核大题 {}（已完成 {}/{} 组）".format(label, done, total)
                       if progress["phase"] == "审核中" else
                       "已完成 {}/{} 组{}".format(done, total, "，最近处理大题 " + label if label else ""))
            latest["ai_judgment"] = {
                **latest.get("ai_judgment", {}),
                "status": "处理中", "enabled": True,
                "group_count": total, "completed_groups": done,
                "current_group": label if progress["phase"] == "审核中" else "",
                "processed": progress["processed"],
                "error_groups": progress["error_groups"],
                "concurrency": progress.get("concurrency", latest.get("ai_judgment", {}).get("concurrency", 1)),
                "message": message,
            }
            _write_review(path, latest)

    updated = apply_ai_review(review, progress_callback=save_progress)
    with AI_REVIEW_LOCK:
        _id, _path, latest = _load_review(review_id)
        by_question = {str(item.get("question")): item for item in updated.get("items", [])}
        ai_fields = ("ai_status", "ai_confidence", "ai_visual_text", "ai_reason", "ai_corrected_answer", "final_status", "score_basis", "awarded_score")
        for item in latest.get("items", []):
            source = by_question.get(str(item.get("question")), {})
            item.update({field: source[field] for field in ai_fields if field in source})
        latest["ai_judgment"] = updated["ai_judgment"]
        latest["review_summary"] = updated["review_summary"]
        if "score_summary" in updated:
            latest["score_summary"] = updated["score_summary"]
        _write_review(review_path, latest)
    latest["report_url"] = "/reviews/{}/output/review.json".format(review_id)
    return latest


def _ai_review_worker(review_id):
    try:
        run_ai_review({"review_id": review_id})
    except Exception as error:
        with AI_REVIEW_LOCK:
            try:
                _id, path, review = _load_review(review_id)
                review["ai_judgment"] = {**review.get("ai_judgment", {}), "status": "异常", "enabled": True, "message": "AI处理失败：{}".format(error)}
                _write_review(path, review)
            except (OSError, ValueError):
                pass
    finally:
        with AI_REVIEW_LOCK:
            AI_REVIEW_WORKERS.pop(review_id, None)


def start_ai_review(payload):
    with AI_REVIEW_LOCK:
        review_id, review_path, review = _load_review(payload.get("review_id"))
        if _ensure_review_scores(review):
            _write_review(review_path, review)
        if not ai_is_configured():
            review["ai_judgment"] = {"status": "未配置", "enabled": False, "processed": 0, "message": "请配置 HANDWRITING_AI_API_KEY 或 OPENAI_API_KEY"}
            _write_review(review_path, review)
        else:
            running = AI_REVIEW_WORKERS.get(review_id)
            if not running or running.done():
                review["ai_judgment"] = {"status": "处理中", "enabled": True, "processed": 0, "group_count": 0, "completed_groups": 0, "current_group": "", "error_groups": 0, "message": "正在等待 AI 并发队列"}
                _write_review(review_path, review)
                AI_REVIEW_WORKERS[review_id] = AI_REVIEW_EXECUTOR.submit(
                    _ai_review_worker, review_id
                )
    review["report_url"] = "/reviews/{}/output/review.json".format(review_id)
    return review


def run_scan_job(files=None, demo=False, template_id=None):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    job_id = "{}-{}".format(stamp, uuid.uuid4().hex[:6])
    job_root = JOBS_ROOT / job_id
    input_dir = job_root / "input"
    output_dir = job_root / "output"
    input_dir.mkdir(parents=True, exist_ok=False)
    selected_template = TEMPLATE_MANAGER.get(template_id)
    copy_template_assets(input_dir, selected_template["id"])
    uploaded = copy_demo_images(input_dir, selected_template["id"]) if demo else save_uploads(files, input_dir)
    command = [
        sys.executable,
        str(PROJECT_ROOT / "main.py"),
        "-a", "-i", str(input_dir), "-o", str(output_dir),
    ]
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ScanFailure("扫描处理超过 10 分钟", str(error)) from error
    duration = round(time.perf_counter() - started, 2)
    log = completed.stdout or ""
    if completed.returncode != 0:
        raise ScanFailure("扫描程序退出状态为 {}".format(completed.returncode), log)
    csv_path = latest_result_csv(output_dir)
    columns, rows = read_results(csv_path, job_id, job_root)
    return {
        "ok": True,
        "job_id": job_id,
        "template_id": selected_template["id"],
        "template_name": selected_template["name"],
        "file_count": len(uploaded),
        "duration_seconds": duration,
        "columns": columns,
        "rows": rows,
        "download_url": job_url(job_id, job_root, csv_path),
        "log_tail": "\n".join(log.splitlines()[-100:]),
    }


from candidate_manager import CandidateManager, grade_confirmation_blockers
CANDIDATE_MANAGER = CandidateManager(lambda: REVIEW_ROOT, _load_review, _write_review,
                                     AI_REVIEW_LOCK, read_objective_view)


class ScanUIHandler(BaseHTTPRequestHandler):
    server_version = "OMRScanUI/1.0"

    def log_message(self, format_string, *args):
        sys.stdout.write("[scan-ui] {}\n".format(format_string % args))

    def send_bytes(self, status, content, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, status, payload):
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_bytes(status, content, "application/json; charset=utf-8")

    def send_json_download(self, status, payload, filename):
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''{}".format(quote(filename)))
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def send_file(self, path):
        if not path.is_file():
            self.send_json(404, {"ok": False, "error": "文件不存在"})
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_bytes(200, path.read_bytes(), content_type)

    def do_GET(self):
        route = urlparse(self.path).path
        try:
            if route == "/":
                self.send_file(UI_ROOT / "index.html")
            elif route == "/api/health":
                ai_settings = ai_config()
                self.send_json(200, {"ok": True, "service": "OMR 扫描台", "review_concurrency": DEFAULT_REVIEW_WORKERS, "ai_judgment": {"configured": ai_is_configured(), "model": ai_settings["model"], "concurrency": ai_settings["concurrency"], "review_workers": DEFAULT_AI_WORKERS}})
            elif route == "/api/templates":
                self.send_json(200, TEMPLATE_MANAGER.list())
            elif route == "/api/candidates":
                self.send_json(200, CANDIDATE_MANAGER.list())
            elif route == "/api/candidates/export.json":
                query = parse_qs(urlparse(self.path).query)
                selected_ids = (query.get("review_id") or []) + (query.get("review_ids") or [])
                selected_ids = [item for value in selected_ids for item in str(value).split(",") if item.strip()]
                self.send_json_download(200, export_confirmed_grades(selected_ids if selected_ids else None), "已确认成绩.json")
            elif route == "/api/exam/imports":
                self.send_json(200, {"ok": True, "imports": list_exam_imports()})
            elif route == "/api/review/objective-view":
                query = parse_qs(urlparse(self.path).query)
                self.send_json(200, read_objective_view((query.get("review_id") or [""])[0]))
            elif route == "/api/review/status":
                query = parse_qs(urlparse(self.path).query)
                self.send_json(200, read_review_status((query.get("review_id") or [""])[0]))
            elif route == "/api/review/batch/status":
                query = parse_qs(urlparse(self.path).query)
                self.send_json(200, read_batch_status((query.get("batch_id") or [""])[0]))
            elif route.startswith("/static/"):
                self.send_file(resolve_under(UI_ROOT, unquote(route[8:])))
            elif route.startswith("/jobs/"):
                self.send_file(resolve_under(JOBS_ROOT, unquote(route[6:])))
            elif route.startswith("/sheets/"):
                self.send_file(resolve_under(SHEETS_ROOT, unquote(route[8:])))
            elif route.startswith("/reviews/"):
                self.send_file(resolve_under(REVIEW_ROOT, unquote(route[9:])))
            elif route.startswith("/imports/"):
                self.send_file(resolve_under(IMPORT_ROOT, unquote(route[9:])))
            else:
                self.send_json(404, {"ok": False, "error": "接口不存在"})
        except ValueError as error:
            self.send_json(400, {"ok": False, "error": str(error)})

    def do_POST(self):
        route = urlparse(self.path).path
        if route not in ("/api/scan", "/api/demo", "/api/sheets", "/api/exam/import", "/api/review", "/api/review/batch", "/api/review/ai-judge", "/api/review/confirm", "/api/review/confirm-grade", "/api/candidates/save", "/api/candidates/recognize", "/api/templates/create", "/api/templates/update", "/api/templates/activate", "/api/templates/delete"):
            self.send_json(404, {"ok": False, "error": "接口不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_REQUEST_BYTES:
                raise ValueError("本次上传大小上限为 256 MB")
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            if route == "/api/templates/create":
                result = TEMPLATE_MANAGER.create(payload)
            elif route == "/api/templates/update":
                result = TEMPLATE_MANAGER.update(payload)
            elif route == "/api/templates/activate":
                result = TEMPLATE_MANAGER.activate(payload)
            elif route == "/api/templates/delete":
                result = TEMPLATE_MANAGER.delete(payload)
            elif route == "/api/candidates/save":
                result = CANDIDATE_MANAGER.save(payload)
            elif route == "/api/candidates/recognize":
                result = CANDIDATE_MANAGER.start(payload)
            elif route == "/api/exam/import":
                result = run_exam_import(payload)
            elif route == "/api/sheets":
                package = generate_sheet_package(payload, output_root=SHEETS_ROOT)
                result = {
                    "ok": True,
                    "sheet_id": package["sheet_id"],
                    "spec": package["spec"],
                    "pdf_url": sheet_url(package["pdf_path"]),
                    "template_url": sheet_url(package["template_path"]),
                    "reference_url": sheet_url(package["reference_path"]),
                    "package_url": sheet_url(package["package_path"]),
                }
            elif route == "/api/review":
                result = run_review_job(payload)
            elif route == "/api/review/batch":
                result = start_batch_review(payload)
            elif route == "/api/review/ai-judge":
                result = start_ai_review(payload)
            elif route == "/api/review/confirm":
                result = save_manual_review(payload)
            elif route == "/api/review/confirm-grade":
                result = confirm_review_grade(payload)
            else:
                result = run_scan_job(
                    files=payload.get("files", []), demo=route == "/api/demo",
                    template_id=payload.get("template_id")
                )
            self.send_json(200, result)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(400, {"ok": False, "error": str(error)})
        except ScanFailure as error:
            self.send_json(500, {
                "ok": False, "error": str(error), "log_tail": error.log[-12000:]
            })
        except Exception as error:
            self.send_json(500, {"ok": False, "error": str(error)})


def create_server(host="127.0.0.1", port=8765):
    if port == 0:
        return ThreadingHTTPServer((host, 0), ScanUIHandler)
    last_error = None
    for candidate in range(port, port + 10):
        try:
            return ThreadingHTTPServer((host, candidate), ScanUIHandler)
        except OSError as error:
            last_error = error
    raise last_error


def run_server(host="127.0.0.1", port=8765, open_browser=False):
    UI_ROOT.mkdir(parents=True, exist_ok=True)
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    SHEETS_ROOT.mkdir(parents=True, exist_ok=True)
    IMPORT_ROOT.mkdir(parents=True, exist_ok=True)
    REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    server = create_server(host, port)
    url = "http://{}:{}".format(host, server.server_address[1])
    print("OMR_SCAN_UI_READY url={}".format(url), flush=True)
    if open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("OMR_SCAN_UI_STOPPED", flush=True)
    finally:
        server.server_close()


def self_test():
    for name in ("index.html", "app.css", "app.js"):
        if not (UI_ROOT / name).is_file():
            raise RuntimeError("缺少 UI 文件：{}".format(name))
    temporary = PROJECT_ROOT / "tmp" / "scan_ui_self_test_assets"
    copy_template_assets(temporary)
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:{}/api/health".format(server.server_address[1])
    try:
        with urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            status = response.status
        if status != 200 or not payload.get("ok"):
            raise RuntimeError("健康检查失败")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print("SCAN_UI_SELF_TEST_OK assets=3 health=200 template_assets={}".format(
        len(list(temporary.iterdir()))
    ))


def parse_args():
    parser = argparse.ArgumentParser(description="OMRChecker 本地扫描界面")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    if arguments.self_test:
        self_test()
    else:
        run_server(arguments.host, arguments.port, arguments.open_browser)
