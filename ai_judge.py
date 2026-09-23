"""通过 OpenAI 兼容接口对 OCR 手写答案做语义判分。"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions"
MAX_SOURCE_CHARS = 800
MAX_RECOGNIZED_CHARS = 1200
_AI_PRINT_LOCK = threading.Lock()


def _env_value(*names, default=""):
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def ai_config():
    endpoint = _env_value(
        "HANDWRITING_AI_ENDPOINT",
        "OPENAI_CHAT_COMPLETIONS_URL",
        default=DEFAULT_ENDPOINT,
    )
    model = _env_value("HANDWRITING_AI_MODEL", "OPENAI_MODEL", default=DEFAULT_MODEL)
    api_key = _env_value("HANDWRITING_AI_API_KEY", "OPENAI_API_KEY")
    timeout = _env_value("HANDWRITING_AI_TIMEOUT", default="180")
    concurrency = _env_value("HANDWRITING_AI_CONCURRENCY", default="3")
    try:
        timeout_seconds = max(10, min(300, int(timeout)))
    except ValueError:
        timeout_seconds = 180
    try:
        concurrency_count = max(1, min(8, int(concurrency)))
    except ValueError:
        concurrency_count = 3
    return {
        "endpoint": endpoint, "model": model, "api_key": api_key,
        "timeout": timeout_seconds, "concurrency": concurrency_count,
    }


def ai_is_configured():
    return bool(ai_config()["api_key"])


def _clip(value, limit):
    return str(value or "").strip()[:limit]


def _clean_visual_text(question, value):
    """移除答题卡印刷的小题标签，避免把 (a)/(b)/(c) 当作改错行号。"""
    text = str(value or "").strip()
    if _is_correction_question(question):
        text = re.sub(
            r"^\s*[（(\[【]\s*[A-Ca-c]\s*[）)\]】]\s*(?:[.:：、)）-]\s*)?",
            "",
            text,
        ).strip()
    return text

def _is_correction_question(question):
    match = re.match(r"\s*(\d+)", str(question or ""))
    return bool(match and 46 <= int(match.group(1)) <= 60)


def _is_algorithm_question(question):
    match = re.match(r"\s*(\d+)", str(question or ""))
    return bool(match and int(match.group(1)) == 64)


def _prompt_items(items):
    payload = []
    for item in items:
        question = str(item.get("question", ""))
        is_correction = _is_correction_question(question)
        is_algorithm = _is_algorithm_question(question)
        payload.append({
            "question": question,
            "question_kind": "correction" if is_correction else "algorithm" if is_algorithm else "general",
            "strict_requirements": (
                "必须同时包含与参考答案一致的数字行号和完整改错内容；答题卡印刷的(a)/(b)/(c)小题标签属于版面内容，忽略它们，不把它们当作行号" if is_correction else
                "开放算法题，参考答案可能为空；必须依据题干、手写思路、代码正确性、边界条件和复杂度自主判分" if is_algorithm else ""
            ),
            "visual_text_rule": (
                "只转录考生实际手写内容；忽略印刷的小题标签、题号、横线和印刷代码。若图片只出现印刷标签而未出现考生手写数字行号，visual_text 保留手写内容并按缺少行号处理"
                if is_correction else "只转录图片中的实际手写内容"
            ),
            "source_content": _clip(item.get("source_content", ""), MAX_SOURCE_CHARS),
            "expected_answer": "" if is_algorithm else _clip(item.get("expected_answer", ""), MAX_SOURCE_CHARS),
            "recognized_text": _clip(item.get("recognized_text", ""), MAX_RECOGNIZED_CHARS),
            "ocr_confidence": round(float(item.get("confidence", 0) or 0), 3),
            "has_handwriting_image": bool(item.get("handwriting_images")),
        })
    return payload


SYSTEM_PROMPT = """你是考试答题卡的手写答案判分助手。每次请求对应一道大题（例如 1.、2.、3.），包含该大题的全部填空位或小问。你会收到大题号、每个小问的题号、试卷题干、参考答案、OCR提取结果和OCR置信度。
结合整道大题的上下文，逐个小问独立判定；返回每个输入题号各一条结果。请先直接阅读对应题号的手写区域图片，再参考 OCR 提取结果，逐题比较参考答案的语义、数学关系、程序逻辑和关键运算符。
图片中的真实笔迹优先于 OCR 文本；OCR 可能漏字、错字或在空白处产生幻觉。对于图片中空白、写明放弃、逻辑相反或关键运算符错误的答案返回 fail。
允许等价表达、空格、标点和 OCR 常见字符误识别；程序题仔细检查运算符、数组下标、变量、常量、函数名和控制关系。图片模糊、关键内容有歧义或题目缺少参考答案时返回 review；第64题按开放算法题规则继续自主判分。
答题卡扫描框可能包含印刷的(a)/(b)/(c)小题标签、印刷题号、横线或印刷代码。上述内容属于版面参照信息，visual_text 只保留考生实际手写内容；印刷的(a)/(b)/(c)永远不作为改错行号。
第46至60题属于改错题，实行严格双项校验。visual_text 必须包含考生实际手写的数字行号和完整改错内容。图片只出现印刷小题标签时，visual_text 记录手写改错内容，reason 明确写出“缺少手写数字行号”，status 返回 fail。行号错误、改错内容缺失、改错内容错误均返回 fail。参考答案用于核对行号和内容，corrected_answer 只做规范化展示，不补充手写缺项。禁止根据题干或参考答案替考生补全行号，禁止用 corrected_answer 弥补手写缺项。
第64题属于开放算法题，参考答案可能为空。请根据题干要求和图片中的实际手写内容自主判分：检查算法思路、正确性、关键步骤、边界条件、复杂度和代码实现。满足题目要求且核心逻辑正确返回 pass；核心逻辑错误、无法完成题目或与题意相反返回 fail；图片或手写内容存在关键歧义返回 review。参考答案为空时仍然必须完成判断，不能仅因缺少固定答案返回 review。
每题返回识别出的真实手写内容 visual_text、判定依据 reason 和 0 到 1 的置信度。只返回 JSON，格式为 {"results":[{"question":"题号","status":"pass|fail|review","confidence":0到1,"visual_text":"图片中实际手写文字","reason":"中文理由","corrected_answer":"可选的规范化答案"}]}。
"""


def _content_from_response(payload):
    choices = payload.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        return str(content)
    output = payload.get("output") or []
    chunks = []
    for item in output:
        for content in item.get("content", []) if isinstance(item, dict) else []:
            if isinstance(content, dict):
                chunks.append(str(content.get("text", "")))
    return "".join(chunks)


def _parse_json(text):
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _status(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"pass", "correct", "通过", "正确", "ok"}:
        return "AI通过"
    if normalized in {"fail", "incorrect", "不通过", "错误", "wrong"}:
        return "AI不通过"
    return "AI需复核"


def _image_content(items):
    content = [{"type": "text", "text": json.dumps({
        "major_question": str(items[0].get("major_question") or _group_key(items[0])),
        "items": _prompt_items(items),
    }, ensure_ascii=False)}]
    for item in items:
        question = str(item.get("question", ""))
        for image_path in item.get("handwriting_images", [])[:2]:
            path = Path(str(image_path))
            if not path.is_file():
                continue
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "text", "text": "题目{}的手写区域：".format(question)})
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded, "detail": "high"}})
    return content


def _request(config, items):
    body = {
        "model": config["model"],
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _image_content(items)},
        ],
        "response_format": {"type": "json_object"},
    }
    request = Request(
        config["endpoint"],
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + config["api_key"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    group_label = str(items[0].get("major_question") or _group_key(items[0]))
    try:
        with urlopen(request, timeout=config["timeout"]) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            with _AI_PRINT_LOCK:
                print("[AI响应][大题 {}] HTTP{}\n{}".format(
                    group_label, getattr(response, "status", "?"), raw_body
                ), flush=True)
            return json.loads(raw_body)
    except HTTPError as error:
        try:
            detail = error.read().decode("utf-8", errors="replace")
        except OSError:
            detail = "响应正文读取超时"
        with _AI_PRINT_LOCK:
            print("[AI响应][大题 {}] HTTP{}\n{}".format(group_label, error.code, detail), flush=True)
        raise RuntimeError("AI接口返回HTTP{}：{}".format(error.code, detail[:500])) from error
    except URLError as error:
        with _AI_PRINT_LOCK:
            print("[AI响应][大题 {}] 连接失败：{}".format(group_label, error.reason), flush=True)
        raise RuntimeError("AI接口连接失败：{}".format(error.reason)) from error
    except TimeoutError as error:
        with _AI_PRINT_LOCK:
            print("[AI响应][大题 {}] 响应超时：{}".format(group_label, error), flush=True)
        raise RuntimeError("AI接口响应超时（{}秒），请稍后重试或调整 HANDWRITING_AI_TIMEOUT".format(config["timeout"])) from error
    except OSError as error:
        with _AI_PRINT_LOCK:
            print("[AI响应][大题 {}] 读取失败：{}".format(group_label, error), flush=True)
        raise RuntimeError("AI接口读取失败：{}".format(error)) from error


def _group_key(item):
    """Prefer the structured exam question; fall back to the leading main number."""
    explicit = str(item.get("ai_group") or item.get("major_question") or "").strip()
    if explicit:
        return explicit
    question = str(item.get("question", "")).strip()
    match = re.match(r"^(\d+)", question)
    return match.group(1) if match else question


def judge_handwritten_items(items, progress_callback=None):
    """按大题分组并发请求 AI，单组失败不会阻塞其他大题。"""
    config = ai_config()
    eligible = [item for item in items if str(item.get("recognized_text", "")).strip() or item.get("handwriting_images")]
    if not config["api_key"]:
        return {
            "status": "未配置", "enabled": False, "model": config["model"],
            "concurrency": config["concurrency"], "processed": 0,
            "message": "请配置 HANDWRITING_AI_API_KEY 或 OPENAI_API_KEY", "results": {},
        }
    if not eligible:
        return {
            "status": "已跳过", "enabled": True, "model": config["model"],
            "concurrency": config["concurrency"], "processed": 0,
            "message": "没有可供 AI 判断的手写内容", "results": {},
        }
    groups = {}
    for item in eligible:
        groups.setdefault(_group_key(item), []).append(item)
    group_list = list(groups.values())
    result_map = {}
    errors = []
    processed = 0
    completed_groups = 0
    group_count = len(group_list)
    worker_count = min(config["concurrency"], group_count)

    def report_progress(phase, completed, current_group):
        if progress_callback is not None:
            progress_callback({
                "phase": phase,
                "group_count": group_count,
                "completed_groups": completed,
                "current_group": current_group,
                "processed": processed,
                "error_groups": len(errors),
                "concurrency": worker_count,
            })

    def process_group(group):
        group_label = str(group[0].get("major_question") or _group_key(group[0]))
        requested = {str(item.get("question", "")).strip() for item in group}
        try:
            response = _request(config, group)
            parsed = _parse_json(_content_from_response(response))
            raw_results = parsed.get("results", []) if isinstance(parsed, dict) else parsed
            if not isinstance(raw_results, list):
                raise ValueError("AI结果需要是题目数组")
            group_results = {}
            for result in raw_results:
                if not isinstance(result, dict):
                    continue
                question = str(result.get("question", "")).strip()
                if question not in requested:
                    continue
                group_results[question] = {
                    "status": _status(result.get("status")),
                    "confidence": max(0.0, min(1.0, float(result.get("confidence", 0) or 0))),
                    "visual_text": _clean_visual_text(question, result.get("visual_text", "")),
                    "reason": str(result.get("reason", "")).strip(),
                    "corrected_answer": str(result.get("corrected_answer", "")).strip(),
                }
            return group_label, group_results, ""
        except (RuntimeError, TimeoutError, ValueError, TypeError, json.JSONDecodeError) as error:
            fallback = {}
            for item in group:
                question = str(item.get("question", "")).strip()
                fallback[question] = {
                    "status": "AI需复核", "confidence": 0.0,
                    "reason": "大题{}请求失败：{}".format(group_label, error),
                    "visual_text": "", "corrected_answer": "",
                }
            return group_label, fallback, "大题{}：{}".format(group_label, error)

    report_progress("准备中", 0, "")
    for group in group_list:
        label = str(group[0].get("major_question") or _group_key(group[0]))
        report_progress("审核中", completed_groups, label)
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="omr-ai-group") as executor:
        futures = [executor.submit(process_group, group) for group in group_list]
        for future in as_completed(futures):
            group_label, group_results, error_message = future.result()
            result_map.update(group_results)
            processed += sum(1 for result in group_results.values() if result.get("status") != "AI需复核" or not error_message)
            if error_message:
                errors.append(error_message)
            completed_groups += 1
            report_progress("本组完成", completed_groups, group_label)

    for item in eligible:
        result_map.setdefault(str(item.get("question", "")).strip(), {
            "status": "AI需复核", "confidence": 0.0, "reason": "AI未返回该题结果",
            "visual_text": "", "corrected_answer": "",
        })
    missing = len(eligible) - processed
    return {
        "status": ("部分完成" if processed else "异常") if errors else ("部分完成" if processed else "异常") if missing else "已完成",
        "enabled": True, "model": config["model"], "concurrency": worker_count,
        "processed": processed,
        "message": "；".join(errors) if errors else ("{}个小问等待复核".format(missing) if missing else "AI已按大题并发完成手写答案语义判断"),
        "results": result_map,
        "group_count": group_count,
        "completed_groups": completed_groups,
        "current_group": "",
        "error_groups": len(errors),
    }
