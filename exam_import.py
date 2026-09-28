"""Structured exam and answer JSON import, validation, and normalization."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


QUESTION_TYPES = {"single", "multiple", "true_false", "blank", "essay"}
TYPE_ALIASES = {
    "choice": "single", "single_choice": "single", "multiple_choice": "multiple", "multi": "multiple",
    "\u5224\u65ad": "true_false", "truefalse": "true_false", "tf": "true_false",
    "\u586b\u7a7a": "blank", "\u7a0b\u5e8f\u586b\u7a7a": "blank", "\u6539\u9519": "essay",
    "\u95ee\u7b54": "essay", "\u6750\u6599\u95ee\u7b54": "essay",
}
ANSWER_KEYS = (
    "answer", "answers", "correct_answer", "correct_answers", "answer_key", "key",
    "standard_answer", "reference_answer", "expected_answer", "solution",
)

PAPER_TYPE_KEYS = ("A", "B", "C")
PAPER_VARIANT_CONTAINER_KEYS = ("papers", "paper_types", "paperTypes", "variants", "versions", "sets", "卷子")


def normalize_paper_type(value):
    text = str(value or "").strip().upper()
    match = re.search(r"(?:卷|TYPE|PAPER)?\s*([ABC])(?:卷)?$", text)
    return match.group(1) if match else (text if text in PAPER_TYPE_KEYS else "")


def _paper_variant_entries(value):
    """读取 A/B/C 试卷变体，兼容 papers/variants 及顶层 A、B、C 键。"""
    raw = _unwrap(value) if isinstance(value, dict) and not any(
        key in value for key in PAPER_VARIANT_CONTAINER_KEYS
    ) else value
    if not isinstance(raw, dict):
        return {}
    for container_key in PAPER_VARIANT_CONTAINER_KEYS:
        container = raw.get(container_key)
        if isinstance(container, dict):
            entries = {}
            for key, payload in container.items():
                paper_type = normalize_paper_type(key)
                if paper_type and isinstance(payload, (list, dict)):
                    entries[paper_type] = payload
            if entries:
                return entries
    entries = {}
    for key, payload in raw.items():
        paper_type = normalize_paper_type(key)
        if paper_type and isinstance(payload, (list, dict)):
            entries[paper_type] = payload
    return entries


def _variant_exam_payload(payload):
    if isinstance(payload, dict):
        for key in ("exam", "sections", "data"):
            if key in payload and isinstance(payload[key], (list, dict)):
                return payload[key]
    return payload


def _variant_answer_payload(payload):
    if isinstance(payload, dict):
        for key in ("answer_map", "answers", "answer", "key", "exam", "sections", "data"):
            if key in payload:
                return payload[key]
    return payload


NUMBER_FIELDS = (
    "local_question_ids", "question_numbers", "number", "question_number",
    "question_no", "questionNumber", "questionNo", "qno", "local_number",
)
_NUMBER = r"(?:\d+|[零〇一二两三四五六七八九十百千]+)"
_NUMBER_ATOM = _NUMBER + r"(?:\s*\(\s*" + _NUMBER + r"\s*\))?"
_NUMBER_EXPRESSION = _NUMBER_ATOM + r"(?:\s*(?:[-~至到]|[,、;])\s*" + _NUMBER_ATOM + r")*"
_NUMBER_LABEL = r"(?:题目编号|题号|编号|question|ques|q)\s*[:.#]?\s*"
_NUMBER_BOUNDARY = r"(?=$|\s|[、:;)\]]|\.(?!\d))"


def _numbering_text(value):
    """Normalize numbering syntax only; retain answer/code text verbatim."""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    return text.translate(str.maketrans({
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
        "【": "[", "】": "]", "〔": "[", "〕": "]",
    }))


def _question_integer(text):
    text = text.strip()
    if text.isdecimal():
        return int(text)
    digits = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    if all(char in digits for char in text):
        return int("".join(str(digits[char]) for char in text))
    units = {"十": 10, "百": 100, "千": 1000}
    total, digit, last_unit = 0, 0, 10000
    for char in text:
        if char in digits:
            digit = digits[char]
        elif char in units and units[char] < last_unit:
            last_unit = units[char]
            total += (digit or 1) * last_unit
            digit = 0
        else:
            return None
    return total + digit


def _number_atom(text):
    match = re.fullmatch(r"(" + _NUMBER + r")(?:\s*\(\s*(" + _NUMBER + r")\s*\))?", text.strip())
    if not match:
        return None
    parts = [_question_integer(part) for part in match.groups() if part is not None]
    if any(part is None for part in parts):
        return None
    return str(parts[0]) if len(parts) == 1 else "{}({})".format(*parts)


def _parse_question_ids(value):
    """Parse a complete number/list/range expression with bounded expansion."""
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, (list, tuple)):
        groups = [_parse_question_ids(item) for item in value]
        if not all(groups):
            return []
        result = list(dict.fromkeys(item for group in groups for item in group))
        if len(result) > 101:
            raise ValueError("题号分组最多包含101项")
        return result
    text = _numbering_text(value)
    while len(text) > 1 and (text[0], text[-1]) in (("(", ")"), ("[", "]")):
        text = text[1:-1].strip()
    text = re.sub(r"^(?:第\s*|" + _NUMBER_LABEL + r")", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*(?:小题|题)$", "", text).strip()
    if not re.fullmatch(_NUMBER_EXPRESSION, text):
        return []
    result = []
    for part in re.split(r"[,、;]", text):
        bounds = re.split(r"[-~至到]", part)
        atoms = [_number_atom(item) for item in bounds]
        if any(atom is None for atom in atoms) or len(atoms) > 2:
            return []
        if len(atoms) == 1:
            result.extend(atoms)
        else:
            first = re.fullmatch(r"(\d+)(?:\((\d+)\))?", atoms[0])
            last = re.fullmatch(r"(\d+)(?:\((\d+)\))?", atoms[1])
            if first.group(2) is None and last.group(2) is None:
                low, high = int(first.group(1)), int(last.group(1))
                prefix = None
            elif first.group(1) == last.group(1) and first.group(2) and last.group(2):
                low, high = int(first.group(2)), int(last.group(2))
                prefix = first.group(1)
            else:
                return []
            if high < low or high - low > 100:
                raise ValueError("题目范围无效：{}".format(part))
            result.extend(str(i) if prefix is None else "{}({})".format(prefix, i) for i in range(low, high + 1))
        if len(result) > 101:
            raise ValueError("题号分组最多包含101项")
    return list(dict.fromkeys(result))


def _title_question_ids(title):
    """Read structural labels; descriptions and embedded numeric/code literals stay intact."""
    text = _numbering_text(title or "")
    patterns = (
        r"^第\s*(" + _NUMBER_EXPRESSION + r")\s*题",
        r"^" + _NUMBER_LABEL + r"(" + _NUMBER_EXPRESSION + r")(?:\s*题)?" + _NUMBER_BOUNDARY,
        r"^[\[(]\s*((?:第\s*)?" + _NUMBER_EXPRESSION + r"(?:\s*题)?)\s*[)\]]",
        r"^(" + _NUMBER_EXPRESSION + r")" + _NUMBER_BOUNDARY,
    )
    for pattern in patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            ids = _parse_question_ids(match.group(1))
            if ids:
                return ids
    match = re.search(r"[\[(]\s*((?:第\s*)?" + _NUMBER_EXPRESSION + r"(?:\s*题)?)\s*[)\]]\s*$", text)
    if match and not (match.start() and re.match(r"[A-Za-z0-9_\])]+", text[match.start() - 1])):
        return _parse_question_ids(match.group(1))
    match = re.search(r"(?:第\s*(" + _NUMBER_EXPRESSION + r")\s*题|" + _NUMBER_LABEL + r"(" + _NUMBER_EXPRESSION + r"))$", text, re.IGNORECASE)
    return _parse_question_ids(match.group(1) or match.group(2)) if match else []


def _local_question_ids(question):
    """Prefer explicit local-number fields, then common title conventions."""
    for field in NUMBER_FIELDS:
        ids = _parse_question_ids(question.get(field))
        if ids:
            return ids
    return _title_question_ids(question.get("title", ""))


def _canonical_question_id(value):
    ids = _parse_question_ids(value)
    return ids[0] if len(ids) == 1 else _numbering_text(value)


def _merge_answer_values(exam, answer_values):
    answer_values = {str(key): value for key, value in (answer_values or {}).items()}
    answer_aliases = defaultdict(list)
    for key, value in answer_values.items():
        answer_aliases[_canonical_question_id(key)].append((key, value))
    entries, owners, native_owners = [], defaultdict(set), defaultdict(set)
    for section in exam["sections"]:
        for question in section["questions"]:
            ids = [str(item) for item in question.get("question_ids", [question["id"]])]
            local_ids = _local_question_ids(question)
            if len(local_ids) == 1 and len(ids) > 1:
                children = [re.fullmatch(r"\d+\((\d+)\)", item) for item in ids]
                if all(children):
                    local_ids = ["{}({})".format(local_ids[0], child.group(1)) for child in children]
            group_index = len(entries)
            group = []
            for index, question_id in enumerate(ids):
                local = local_ids[index] if index < len(local_ids) else ""
                aliases = list(dict.fromkeys([_canonical_question_id(question_id)] + ([local] if local else [])))
                token = (group_index, index)
                native_owners[question_id].add(token)
                for alias in aliases:
                    owners[alias].add(token)
                group.append((question_id, local, aliases, token))
            entries.append((question, group))
    used, warnings, labels = set(), [], {}
    counts = Counter()
    embedded = answer_map(exam)
    for question, group in entries:
        resolved = {}
        for question_id, local, aliases, token in group:
            labels[question_id] = "{}（ID {}）".format(local, question_id) if local and local != question_id else question_id
            if len(native_owners[question_id]) > 1:
                warnings.append("题目ID {} 重复，请提供唯一ID".format(question_id))
                continue
            if question_id in answer_values:
                resolved[question_id] = answer_values[question_id]
                used.add(question_id)
                expected = _normalize_answer(answer_values[question_id], question["type"])
                for key, value in answer_aliases[_canonical_question_id(question_id)]:
                    if _normalize_answer(value, question["type"]) == expected:
                        used.add(key)
                    else:
                        warnings.append("题目ID {} 的别名答案存在差异，已采用原生ID答案，请核对".format(question_id))
                counts["native_id"] += 1
                continue
            for alias in aliases:
                candidates = answer_aliases.get(alias, [])
                if not candidates:
                    continue
                if len(owners[alias]) > 1:
                    warnings.append("题号 {} 对应多题，请使用各题的唯一题目ID".format(alias))
                    continue
                values = [_normalize_answer(value, question["type"]) for _, value in candidates]
                if any(value != values[0] for value in values[1:]):
                    warnings.append("题号 {} 的多个答案键内容冲突，请核对".format(alias))
                    break
                resolved[question_id] = candidates[0][1]
                used.update(key for key, _ in candidates)
                counts["normalized_id" if alias == aliases[0] else "local_number"] += 1
                break
        if resolved:
            ids = [item[0] for item in group]
            if len(ids) == 1:
                question["answer"] = _normalize_answer(resolved[ids[0]], question["type"])
            else:
                combined = {key: embedded[key] for key in ids if key in embedded}
                combined.update(resolved)
                question["answer"] = {key: _clean_text(value, 2000) for key, value in combined.items()}
    flat = answer_map(exam)
    sources = source_map(exam)
    summary = dict(exam["summary"])
    summary["answer_count"] = len(flat)
    summary["answer_missing"] = [key for key in sources if key not in flat]
    summary["answer_missing_labels"] = [labels.get(key, key) for key in summary["answer_missing"]]
    summary["answer_unmatched_keys"] = [key for key in answer_values if key not in used]
    if summary["answer_unmatched_keys"]:
        warnings.append("待核对应答题号：{}".format("、".join(summary["answer_unmatched_keys"])))
    summary["answer_warnings"] = list(dict.fromkeys(warnings))
    summary["answer_match_counts"] = dict(counts)
    return {"exam": exam, "answer_map": flat, "source_map": sources, "summary": summary}


def _normalize_paper_variants(exam_value, answer_value=None, answer_docx=None):
    exam_entries = _paper_variant_entries(exam_value)
    answer_entries = _paper_variant_entries(answer_value)
    if not exam_entries and not answer_entries:
        return {}
    keys = sorted(set(exam_entries) | set(answer_entries), key=lambda item: PAPER_TYPE_KEYS.index(item))
    variants = {}
    for paper_type in keys:
        exam_payload = _variant_exam_payload(exam_entries.get(paper_type, exam_value))
        exam = normalize_exam_data(exam_payload, "{}卷试卷".format(paper_type))
        answer_payload = answer_entries.get(paper_type, answer_value if not answer_entries else {})
        answer_values = parse_answers(_variant_answer_payload(answer_payload)) if answer_payload not in (None, "", {}) else {}
        if answer_docx is not None and len(keys) == 1:
            from exam_review import extract_answer_key
            answer_values.update(extract_answer_key(Path(answer_docx)))
        variants[paper_type] = _merge_answer_values(exam, answer_values)
    return variants


def _next_nonspace(text, index):
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _looks_like_string_end(text, index):
    index = _next_nonspace(text, index)
    if index >= len(text) or text[index] in "]}":
        return True
    if text[index] != ",":
        return text[index] == ":"
    next_index = _next_nonspace(text, index + 1)
    if next_index >= len(text) or text[next_index] in "]}":
        return True
    return text[next_index] in "[{\""


def repair_relaxed_json(text):
    source = str(text or "").lstrip("\ufeff").strip()
    if source.startswith("```"):
        source = re.sub(r"^```(?:json)?\s*|\s*```$", "", source, flags=re.IGNORECASE | re.DOTALL).strip()
    output = []
    in_string = False
    escaped = False
    for index, character in enumerate(source):
        if not in_string:
            output.append(character)
            if character == '"':
                in_string = True
            continue
        if escaped:
            output.append(character)
            escaped = False
            continue
        if character == "\\":
            output.append(character)
            escaped = True
            continue
        if character == '"':
            if _looks_like_string_end(source, index + 1):
                output.append(character)
                in_string = False
            else:
                output.append('\\"')
            continue
        if character in "\r\n":
            output.append("\\n")
        else:
            output.append(character)
    return "".join(output)


def parse_json_text(text):
    source = str(text or "").lstrip("\ufeff").strip()
    if not source:
        raise ValueError("\u005b\u0053\u004f\u004e \u5185\u5bb9\u4e3a\u7a7a")
    try:
        return json.loads(source)
    except json.JSONDecodeError as first_error:
        repaired = repair_relaxed_json(source)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError as second_error:
            raise ValueError("JSON \u683c\u5f0f\u65e0\u6cd5\u89e3\u6790\uff1a\u7b2c{}\u884c\u7b2c{}\u5217\uff1b\u5df2\u5c1d\u8bd5\u4fee\u590d\u4ee3\u7801\u7247\u6bb5\u4e2d\u7684\u5f15\u53f7\u3002".format(second_error.lineno, second_error.colno)) from first_error


def _unwrap(value):
    if isinstance(value, dict):
        for key in ("sections", "data", "exam", "answer", "answers", "paper"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
            if isinstance(nested, dict):
                return _unwrap(nested)
    return value


def _clean_text(value, limit=20000):
    return str(value or "").replace("\x00", "").strip()[:limit]


QUESTION_RANGE_PATTERN = re.compile(r"(?<!\d)(\d+)\s*[-~\u81f3\u2013\u2014]\s*(\d+)(?!\d)")


def _question_number(title, fallback):
    match = re.match(r"\s*(\d+(?:\s*[-~\u81f3\u2013\u2014]\s*\d+)?|[A-Za-z])", str(title or ""))
    return re.sub(r"\s+", "", match.group(1)) if match else str(fallback)


def _question_range(*values):
    for value in values:
        ids = _parse_question_ids(value) or _title_question_ids(value)
        if len(ids) > 1:
            return ids
    return []


def _normalize_type(value):
    key = re.sub(r"[\s-]+", "_", str(value or "single").strip().lower())
    return TYPE_ALIASES.get(key, key if key in QUESTION_TYPES else "single")


def _answer_value(question):
    for key in ANSWER_KEYS:
        if key in question and question[key] not in (None, "", []):
            return list(question[key]) if isinstance(question[key], tuple) else question[key]
    return ""


def _normalize_answer(value, question_type):
    if isinstance(value, list):
        values = [_clean_text(item, 2000) for item in value if _clean_text(item, 2000)]
        if question_type == "multiple" and values and all(re.fullmatch(r"[A-DＴＦTF]", item.upper()) for item in values):
            return "".join(dict.fromkeys(item.upper() for item in values))
        return values
    if isinstance(value, dict):
        return {str(key).strip(): _clean_text(item, 2000) for key, item in value.items()}
    text = _clean_text(value, 2000)
    if question_type == "multiple":
        pieces = re.findall(r"[A-DT\uff26\uff34]", text.upper())
        return "".join(dict.fromkeys(pieces)) if pieces else text
    if question_type == "true_false":
        upper = text.upper()
        if upper in {"\u6b63\u786e", "\u5bf9", "TRUE", "T"}:
            return "T"
        if upper in {"\u9519\u8bef", "\u9519", "FALSE", "F"}:
            return "F"
    return text


def normalize_exam_data(value, source_name="\u8bd5\u5377"):
    raw = _unwrap(value)
    if not isinstance(raw, list):
        raise ValueError("{} \u9700\u8981\u662f\u7ae0\u8282\u6570\u7ec4".format(source_name))
    sections = []
    question_count = 0
    total_score = 0.0
    type_counter = Counter()
    for section_index, section in enumerate(raw):
        if not isinstance(section, dict):
            raise ValueError("\u7b2c{}\u4e2a\u7ae0\u8282\u9700\u8981\u662f\u5bf9\u8c61".format(section_index + 1))
        questions = section.get("questions") or []
        if not isinstance(questions, list):
            raise ValueError("\u7ae0\u8282 {} \u7684 questions \u9700\u8981\u662f\u6570\u7ec4".format(section.get("name", section_index + 1)))
        normalized_questions = []
        native_section_id = section.get("id")
        native_session_id = section.get("session_id")
        for question_index, question in enumerate(questions):
            if not isinstance(question, dict):
                raise ValueError("\u7ae0\u8282 {} \u7b2c{}\u9898\u9700\u8981\u662f\u5bf9\u8c61".format(section.get("name", section_index + 1), question_index + 1))
            question_type = _normalize_type(question.get("type"))
            title = _clean_text(question.get("title"), 2000)
            description = _clean_text(question.get("description"), 20000)
            if question_type == "single" and re.search(r"(?:^|\n)\s*T[.\u3001]?\s*\S+.*\n\s*F[.\u3001]?", description, re.IGNORECASE):
                question_type = "true_false"
            if not title:
                raise ValueError("\u7ae0\u8282 {} \u7b2c{}\u9898\u7f3a\u5c11 title".format(section.get("name", section_index + 1), question_index + 1))
            try:
                score = float(question.get("score", 0) or 0)
            except (TypeError, ValueError) as error:
                raise ValueError("\u9898\u76ee {} \u7684 score \u9700\u8981\u662f\u6570\u5b57".format(title)) from error
            native_question_id = question.get("id")
            local_ids = _local_question_ids(question)
            normalized_question = {
                "id": native_question_id if native_question_id is not None else _clean_text(",".join(local_ids) if local_ids else _question_number(title, question_index + 1), 80),
                "type": question_type,
                "title": title,
                "score": int(score) if score.is_integer() else score,
                "description": description,
                "sort_order": int(question.get("sort_order", question_index) or question_index),
                "answer": _normalize_answer(_answer_value(question), question_type),
                **({"local_question_ids": local_ids} if local_ids else {}),
                **({"question_ids": [str(item) for item in question["question_ids"]]} if isinstance(question.get("question_ids"), list) else {}),
                **({key: question[key] for key in ("analysis", "explanation", "reference", "tags", "score_map", "sub_scores") if key in question}),
            }
            if native_section_id is not None or "section_id" in question:
                normalized_question["section_id"] = question.get("section_id", native_section_id)
            if native_session_id is not None or "session_id" in question:
                normalized_question["session_id"] = question.get("session_id", native_session_id)
            normalized_questions.append(normalized_question)
            question_count += 1
            total_score += score
            type_counter[question_type] += 1
        normalized_section = {
            "name": _clean_text(section.get("name"), 200),
            "description": _clean_text(section.get("description"), 5000),
            "sort_order": int(section.get("sort_order", section_index) or section_index),
            "total_score": section.get("total_score", sum(float(q["score"]) for q in normalized_questions)),
            "questions": normalized_questions,
        }
        if native_section_id is not None:
            normalized_section["id"] = native_section_id
        if native_session_id is not None:
            normalized_section["session_id"] = native_session_id
        sections.append(normalized_section)
    sections.sort(key=lambda item: item["sort_order"])
    next_number = 1
    for section in sections:
        section["questions"].sort(key=lambda item: item["sort_order"])
        for question in section["questions"]:
            explicit = question.get("question_ids")
            question_identifier = str(question["id"])
            if explicit:
                question_ids = [str(item) for item in explicit]
            else:
                local_ids = _local_question_ids(question)
                inferred_range = local_ids if len(local_ids) > 1 else _question_range(question_identifier)
                single_match = re.fullmatch(r"\d+", question_identifier)
                if inferred_range:
                    question_ids = inferred_range
                elif single_match:
                    number = int(question_identifier)
                    markers = sorted(set(re.findall(r"\*+\s*\((\d+)\)", question["description"])), key=int)
                    question_ids = ["{}({})".format(number, marker) for marker in markers] if number >= 61 and markers else [str(number)]
                elif len(question_identifier) == 1 and question_identifier.isalpha() and question["type"] == "essay":
                    size = max(1, int(float(question["score"])))
                    question_ids = [str(number) for number in range(next_number, next_number + size)]
                else:
                    question_ids = [question_identifier]
            question["question_ids"] = question_ids
            numeric_ids = [int(re.match(r"\d+", key).group()) for key in question_ids if re.match(r"\d+", key)]
            if numeric_ids:
                next_number = max(next_number, max(numeric_ids) + 1)
    grouped_question_count = question_count
    grouped_types = dict(type_counter)
    expanded_types = Counter()
    question_count = 0
    for section in sections:
        for question in section["questions"]:
            field_count = len(question.get("question_ids") or [question["id"]])
            question_count += field_count
            expanded_types[question["type"]] += field_count
    return {
        "version": 1,
        "source_name": source_name,
        "sections": sections,
        "summary": {
            "section_count": len(sections),
            "question_count": question_count,
            "grouped_question_count": grouped_question_count,
            "total_score": int(total_score) if total_score.is_integer() else total_score,
            "types": dict(expanded_types),
            "grouped_types": grouped_types,
        },
    }


def _split_numbered_answers(value, question_ids):
    text = _clean_text(value, 20000)
    if not text:
        return {}
    matches = list(re.finditer(r"(?:^|\n)\s*(\d+(?:\(\d+\))?)\s*[.\u3001:：]\s*", text))
    result = {}
    valid = {str(item) for item in question_ids}
    for index, match in enumerate(matches):
        key = match.group(1)
        if key not in valid:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        answer = text[match.end():end].strip()
        if answer:
            result[key] = answer
    return result


def answer_map(normalized):
    result = {}
    for section in normalized.get("sections", []):
        for question in section.get("questions", []):
            answer = question.get("answer", "")
            question_ids = [str(item) for item in question.get("question_ids", [question["id"]])]
            if isinstance(answer, dict):
                direct = {str(key): value for key, value in answer.items() if value not in ("", None)}
                for key, value in direct.items():
                    if key in question_ids:
                        result[key] = value
                for index, key in enumerate(question_ids, 1):
                    ordinal = str(index)
                    if key not in result and ordinal in direct:
                        result[key] = direct[ordinal]
            elif isinstance(answer, list):
                if len(question_ids) == 1 and answer:
                    result[question_ids[0]] = answer
                else:
                    for key, value in zip(question_ids, answer):
                        if value not in ("", None):
                            result[key] = value
            elif answer not in ("", None):
                numbered = _split_numbered_answers(answer, question_ids)
                if numbered:
                    result.update(numbered)
                elif question_ids:
                    result[question_ids[0]] = answer
    return result


def source_map(normalized):
    result = {}
    for section in normalized.get("sections", []):
        for question in section.get("questions", []):
            source = "{}\n{}".format(question["title"], question.get("description", "")).strip()
            for key in question.get("question_ids", [question["id"]]):
                result[str(key)] = source
    return result


def parse_answers(value):
    if value is None:
        return {}
    if isinstance(value, str):
        value = parse_json_text(value)
    if isinstance(value, dict):
        for key in ("answer_map", "answers", "answer", "key"):
            if key in value and isinstance(value[key], dict):
                return {str(number): answer for number, answer in value[key].items()}
        if "sections" in value or "exam" in value or "data" in value:
            return answer_map(normalize_exam_data(value, "\u7b54\u6848"))
        return {str(number): answer for number, answer in value.items()}
    return answer_map(normalize_exam_data(value, "\u7b54\u6848"))


def import_exam_and_answers(exam_text, answer_text="", answer_docx=None):
    exam_value = parse_json_text(exam_text) if isinstance(exam_text, str) else exam_text
    answer_value = parse_json_text(answer_text) if isinstance(answer_text, str) and answer_text.strip() else answer_text
    variants = _normalize_paper_variants(exam_value, answer_value, answer_docx=answer_docx)
    if variants:
        default_type = "A" if "A" in variants else sorted(variants)[0]
        selected = variants[default_type]
        summary = dict(selected["summary"])
        summary["paper_types"] = list(variants)
        summary["variant_count"] = len(variants)
        return {
            **selected,
            "exam": selected["exam"],
            "summary": summary,
            "paper_variants": variants,
            "paper_types": list(variants),
        }

    exam = normalize_exam_data(exam_value, "试卷")
    answer_values = parse_answers(answer_value) if answer_value not in (None, "", {}) else {}
    if answer_docx is not None:
        from exam_review import extract_answer_key
        answer_values.update(extract_answer_key(Path(answer_docx)))
    return _merge_answer_values(exam, answer_values)


def load_import_file(path: Path):
    return parse_json_text(path.read_text(encoding="utf-8-sig"))
