"""随仓库交付的定位资源、历史路径迁移及部署完整性检查。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
RESOURCE_REL = "assets/recognition"
REFERENCE_16TH_REL = RESOURCE_REL + "/software-16th-abc.pdf"
REFERENCE_15TH_REL = RESOURCE_REL + "/software-15th-a.pdf"
MARKER_REL = RESOURCE_REL + "/omr_marker.jpg"
SCAN_REL = RESOURCE_REL + "/phone_scan"
REFERENCE_PDF = PROJECT_ROOT / REFERENCE_16TH_REL
REFERENCE_15TH_PDF = PROJECT_ROOT / REFERENCE_15TH_REL
MARKER_ASSET = PROJECT_ROOT / MARKER_REL
DEFAULT_SCAN_ROOT = PROJECT_ROOT / SCAN_REL
LEGACY_SCAN_REL = "inputs/phone_scan"
LEGACY_REFERENCES = {
    "output/pdf/exam_16th_answer_card_unified_2026/第十六届软件方向二面试题A_B_C通用答题卡_定位标记版.pdf": REFERENCE_16TH_REL,
    "output/pdf/exam_answer_cards/marker_version/15th软件方向二面试题A卷答题卡_定位标记版.pdf": REFERENCE_15TH_REL,
}
REQUIRED_FILES = (
    REFERENCE_16TH_REL, REFERENCE_15TH_REL, MARKER_REL,
    *(SCAN_REL + "/" + name for name in ("template.json", "config.json", "evaluation.json", "reference_blank.png")),
)


def migrate_reference(value, project_root=PROJECT_ROOT):
    """只迁移已知的旧内置路径，保留自定义参考文件。"""
    original = str(value or "").strip()
    normalized = original.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    root_prefix = Path(project_root).resolve().as_posix().rstrip("/") + "/"
    if normalized.startswith(root_prefix):
        normalized = normalized[len(root_prefix):]
    return LEGACY_REFERENCES.get(normalized, original)


def require_file(path, label):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"缺少{label}：{path}。请完整部署 assets/recognition 目录并检查模板参考路径。")
    return path


def scan_resource_errors(scan_root):
    """包含预处理器引用图片的就绪检查，供模板列表和部署检查共用。"""
    root = Path(scan_root).resolve()
    errors = []
    for name in ("template.json", "config.json"):
        path = root / name
        if not path.is_file():
            errors.append(f"缺少扫描配置：{path}")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("配置根节点应为对象")
            if name == "template.json":
                for processor in data.get("preProcessors", []):
                    for key in ("reference", "relativePath"):
                        value = processor.get("options", {}).get(key)
                        if not value:
                            continue
                        reference = (root / value).resolve()
                        if root not in reference.parents:
                            errors.append(f"扫描参考文件路径超出模板目录：{value}")
                        elif not reference.is_file():
                            errors.append(f"缺少扫描参考文件：{reference}")
        except (OSError, ValueError, TypeError, AttributeError) as error:
            errors.append(f"扫描配置格式错误：{path}（{error}）")
    return errors


def validate_recognition_assets(project_root=PROJECT_ROOT):
    """启动和构建时核对文件、SHA-256、扫描依赖及两套参考 PDF 页数。"""
    root = Path(project_root).resolve()
    manifest_path = require_file(root / RESOURCE_REL / "manifest.json", "定位资源清单")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        files = manifest["files"]
        if manifest.get("version") != 1 or not isinstance(files, dict) or set(files) != set(REQUIRED_FILES):
            raise ValueError("资源清单版本或文件集合错误")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"定位资源清单格式错误：{manifest_path}（{error}）") from error
    errors = []
    for relative in REQUIRED_FILES:
        path = root / relative
        if not path.is_file():
            errors.append(f"缺少定位资源：{relative}")
            continue
        expected = files[relative]
        if (not isinstance(expected, dict)
                or path.stat().st_size != expected.get("bytes")
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected.get("sha256")):
            errors.append(f"定位资源校验失败：{relative}")
    errors.extend(scan_resource_errors(root / SCAN_REL))
    if errors:
        raise ValueError("定位资源检查失败，请完整部署 assets/recognition 目录：\n" + "\n".join(errors))
    import pymupdf

    page_counts = {}
    for template_id, relative in (("software-16th-abc", REFERENCE_16TH_REL), ("software-15th-a", REFERENCE_15TH_REL)):
        try:
            with pymupdf.open(str(root / relative)) as document:
                page_counts[template_id] = len(document)
        except Exception as error:
            raise ValueError(f"定位参考 PDF 读取失败：{relative}（{error}）") from error
        if page_counts[template_id] != 2:
            raise ValueError(f"定位参考 PDF 应为两页：{relative}")
    return {"ok": True, "files": len(files), "reference_pages": page_counts}


def main():
    parser = argparse.ArgumentParser(description="检查定位资源交付完整性")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    try:
        result = validate_recognition_assets(args.root)
    except (ValueError, OSError) as error:
        print(str(error))
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
