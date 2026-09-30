"""Exam-specific candidate rosters and student-ID-only attendance matching."""
import csv
import io
import json
import re
import unicodedata
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

MAX_ROSTER_BYTES = 2 * 1024 * 1024
MAX_ROSTER_ROWS = 10000
ID_PATTERN = re.compile(r"[0-9]{6,12}")


def student_id(value):
    """Keep leading zeroes; never guess missing digits or match by name."""
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def parse_roster(content):
    if not isinstance(content, (str, bytes)):
        raise ValueError("请上传 CSV / TSV 名单，或粘贴学号与姓名两列")
    if isinstance(content, bytes):
        if len(content) > MAX_ROSTER_BYTES:
            raise ValueError("名单文件最大 2 MB")
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                content = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("请将名单保存为 UTF-8 CSV 后导入")
    if len(content.encode("utf-8")) > MAX_ROSTER_BYTES:
        raise ValueError("名单文件最大 2 MB")
    content = content.lstrip("\ufeff")
    lines = content.splitlines()
    first = next((line for line in lines if line.strip()), "")
    delimiter = "\t" if "\t" in first else ","
    reader = csv.reader(io.StringIO(content), delimiter=delimiter, strict=True)
    aliases = {"学号": "student_id", "考号": "student_id", "student_id": "student_id",
               "姓名": "student_name", "考生姓名": "student_name", "student_name": "student_name"}
    header = None
    records, seen = [], set()
    try:
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            if header is None:
                header = [aliases.get(cell.strip().lower(), "") for cell in row]
                if header.count("student_id") != 1 or header.count("student_name") != 1:
                    raise ValueError("名单表头须包含唯一的“学号”和“姓名”列")
                id_column, name_column = header.index("student_id"), header.index("student_name")
                continue
            if len(row) != len(header):
                raise ValueError("第 {} 行列数与表头不一致".format(reader.line_num))
            sid, name = student_id(row[id_column]), row[name_column].strip()
            if not ID_PATTERN.fullmatch(sid):
                raise ValueError("第 {} 行学号须为 6—12 位完整数字；请将学号列设为文本以保留前导零".format(reader.line_num))
            if not name or len(name) > 40 or any(ord(char) < 32 for char in name):
                raise ValueError("第 {} 行姓名须为 1—40 字的文本".format(reader.line_num))
            if sid in seen:
                raise ValueError("第 {} 行学号 {} 重复，请每位考生保留一行".format(reader.line_num, sid))
            seen.add(sid)
            records.append({"student_id": sid, "student_name": name})
            if len(records) > MAX_ROSTER_ROWS:
                raise ValueError("每场考试最多导入 10000 名考生")
    except csv.Error as error:
        raise ValueError("名单 CSV 格式错误，请检查引号与分隔符") from error
    if not records:
        raise ValueError("名单须包含至少一名考生")
    return records


def exam_directory(root, import_id):
    if not isinstance(import_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", import_id):
        raise ValueError("请先选择考试")
    directory = Path(root) / import_id
    path = directory / "normalized_exam.json"
    if not path.is_file():
        raise ValueError("所选考试已更新，请刷新考试列表")
    imported = json.loads(path.read_text(encoding="utf-8"))
    if imported.get("deleted_at"):
        raise ValueError("所选考试已归档，请选择当前考试")
    return directory


def load_roster(root, import_id):
    directory = exam_directory(root, import_id)
    path = directory / "candidate_roster.json"
    if not path.is_file():
        return {"import_id": import_id, "students": [], "imported_at": "", "filename": ""}
    return _read_roster(path)


def _read_roster(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("import_id") != path.parent.name
            or not isinstance(data.get("students"), list)):
        raise ValueError("考生名单读取异常，请重新导入")
    seen = set()
    for row in data["students"]:
        if not isinstance(row, dict):
            raise ValueError("考生名单读取异常，请重新导入")
        sid, name = row.get("student_id"), row.get("student_name")
        if (not isinstance(sid, str) or not ID_PATTERN.fullmatch(sid)
                or not isinstance(name, str) or not name.strip() or sid in seen):
            raise ValueError("考生名单读取异常，请重新导入")
        seen.add(sid)
    return data


def roster_index(root, import_id):
    # Historical records can outlive an archived exam; its roster still names them.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(import_id or "")):
        return {}
    path = Path(root) / import_id / "candidate_roster.json"
    if not path.is_file():
        return {}
    data = _read_roster(path)
    return {row["student_id"]: row["student_name"] for row in data["students"]}


def save_roster(root, import_id, content, filename="", sync=None):
    directory = exam_directory(root, import_id)
    rows = parse_roster(content)
    data = {"import_id": import_id, "students": rows,
            "filename": Path(str(filename).replace("\\", "/")).name[:200],
            "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    destination = directory / "candidate_roster.json"
    temporary = directory / (".roster-" + uuid.uuid4().hex + ".json")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if sync:
            sync(temporary, Path(import_id) / destination.name)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return data


def apply_roster_identity(record, names):
    """Project names without changing OCR, manual identity, or scores on disk."""
    result = dict(record)
    sid = student_id(record.get("student_id"))
    matched = bool(ID_PATTERN.fullmatch(sid) and sid in names)
    result["roster_matched"] = matched
    if matched:
        result.update(student_name=names[sid], student_name_source="roster",
                      student_name_status="已确认")
    return result


def attendance(roster, records, skipped=0):
    by_id = defaultdict(list)
    unmatched = []
    names = {row["student_id"]: row["student_name"] for row in roster["students"]}
    for record in records:
        if record.get("import_id") != roster["import_id"]:
            continue
        sid = student_id(record.get("student_id"))
        if ID_PATTERN.fullmatch(sid) and sid in names:
            by_id[sid].append(record["review_id"])
        else:
            unmatched.append({"review_id": record["review_id"], "student_id": record.get("student_id", ""),
                              "student_name": record.get("student_name", ""),
                              "reason": "学号待核对" if not ID_PATTERN.fullmatch(sid) else "名单外学号"})
    students = [{**row, "attendance_status": "已交卷" if row["student_id"] in by_id else "缺考",
                 "review_ids": by_id[row["student_id"]]} for row in roster["students"]]
    present = sum(bool(row["review_ids"]) for row in students)
    duplicates = [{"student_id": sid, "student_name": names[sid], "review_ids": ids}
                  for sid, ids in by_id.items() if len(ids) > 1]
    return {"ok": True, "import_id": roster["import_id"], "has_roster": bool(students),
            "filename": roster.get("filename", ""), "imported_at": roster.get("imported_at", ""),
            "students": students, "unmatched": unmatched, "duplicates": duplicates, "skipped": skipped,
            "summary": {"expected": len(students), "present": present, "absent": len(students) - present,
                        "unmatched": len(unmatched), "duplicate_ids": len(duplicates)}}
