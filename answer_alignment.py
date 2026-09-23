"""用印刷题号做局部配准；所有坐标采用 A4 页面左上角原点，单位 pt。"""
from __future__ import annotations

import cv2
import numpy as np

PAGE_W = 595.2756
PAGE_H = 841.8898


def region_anchors(region):
    """返回与固定答题卡版式对应的题号模板框。"""
    if region == "program":
        return [(42 + c * 174, 645 + r * 29, 12, 12) for c in range(3) for r in range(5)]
    if region == "single":
        return [(42.5 + c * 174, 342 + r * 20.5, 12, 12) for c in range(3) for r in range(5)]
    if region == "multiple_tf":
        return [(x, 497 + r * 20.5, 12, 12) for x in (52.5, 327.5, 439.5) for r in range(5)]
    if region == "correction":
        return [(44, 193 + r * 23.5, 12, 12) for r in range(15)]
    if region == "material":
        return [(44, 577 + r * 21.5, 18, 12) for r in range(3)]
    raise ValueError("未知答题区域：" + region)


def align_printed_region(image, reference, region, initial_shift_y=0.0):
    """匹配题号并稳健拟合局部仿射变换；证据不足时返回原图与待复核状态。"""
    info = {"method": "printed_anchors_v2", "status": "待复核", "matched": 0,
            "inliers": 0, "match_score": 0.0, "reason": "题号定位证据不足"}
    if image is None or reference is None or image.size == 0 or reference.size == 0:
        return image, info
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    ref = reference if reference.ndim == 2 else cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    if min(gray.shape[:2]) < 100 or min(ref.shape[:2]) < 100:
        return image, info
    if ref.shape != gray.shape:
        ref = cv2.resize(ref, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_AREA)
    sx, sy = gray.shape[1] / PAGE_W, gray.shape[0] / PAGE_H
    search_image = cv2.GaussianBlur(gray, (3, 3), 0)
    sources, targets, scores, indices = [], [], [], []
    anchors = region_anchors(region)
    for index, (x, y, width, height) in enumerate(anchors):
        x0, y0 = round(x * sx), round(y * sy)
        x1, y1 = round((x + width) * sx), round((y + height) * sy)
        template = ref[y0:y1, x0:x1]
        if template.size == 0 or np.count_nonzero(template < 180) < 12:
            continue
        left, right = max(0, x0 - round(35 * sx)), min(gray.shape[1], x1 + round(35 * sx))
        search_y = 12 if region == "material" else 22
        top = max(0, y0 + round((initial_shift_y - search_y) * sy))
        bottom = min(gray.shape[0], y1 + round((initial_shift_y + search_y) * sy))
        search = search_image[top:bottom, left:right]
        best_score, best_point = -1.0, None
        for scale in (0.85, 1.0, 1.15):
            scaled = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            if search.shape[0] < scaled.shape[0] or search.shape[1] < scaled.shape[1]:
                continue
            response = cv2.matchTemplate(search, cv2.GaussianBlur(scaled, (3, 3), 0), cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(response)
            if np.isfinite(score) and score > best_score:
                best_score = score
                best_point = (left + location[0] + scaled.shape[1] / 2,
                              top + location[1] + scaled.shape[0] / 2)
        if best_score >= 0.72 and best_point is not None:
            sources.append(((x0 + x1) / 2, (y0 + y1) / 2))
            targets.append(best_point)
            scores.append(best_score)
            indices.append(index)
    info["matched"] = len(sources)
    minimum = 3 if region == "material" else 8
    if len(sources) < minimum:
        return image, info
    source, target = np.asarray(sources, np.float32), np.asarray(targets, np.float32)
    # 单列题号采用相似变换，避免用共线点求完整仿射矩阵。
    fitter = cv2.estimateAffinePartial2D if region in {"correction", "material"} else cv2.estimateAffine2D
    matrix, mask = fitter(source, target, method=cv2.RANSAC, ransacReprojThreshold=3 * sy,
                          maxIters=2000, confidence=0.995, refineIters=10)
    if matrix is None or mask is None or not np.isfinite(matrix).all():
        return image, info
    valid = mask.ravel().astype(bool)
    info["inliers"] = int(valid.sum())
    if info["inliers"] < minimum or info["inliers"] / len(sources) < 0.65:
        return image, info
    accepted = source[valid]
    if np.ptp(accepted[:, 1]) < (30 if region == "material" else 60) * sy:
        return image, info
    if region not in {"correction", "material"} and np.ptp(accepted[:, 0]) < 200 * sx:
        return image, info
    singular = np.linalg.svd(matrix[:, :2], compute_uv=False)
    delta = cv2.transform(source[None], matrix)[0] - source
    if (np.linalg.det(matrix[:, :2]) <= 0 or singular.min() < 0.85 or singular.max() > 1.15
            or np.max(np.abs(delta[:, 0])) > 35 * sx or np.max(np.abs(delta[:, 1])) > 40 * sy):
        info["reason"] = "配准变形超出验证范围"
        return image, info
    residual = np.linalg.norm(cv2.transform(source[None], matrix)[0] - target, axis=1)
    center = accepted.mean(axis=0)
    center_delta = matrix[:, :2] @ center + matrix[:, 2] - center
    info.update(status="已校正", reason="印刷题号多点配准通过", match_score=round(float(np.mean(np.asarray(scores)[valid])), 4),
                residual_pt=round(float(np.median(residual[valid])) / sy, 3),
                shift_x_pt=round(float(center_delta[0]) / sx, 2), shift_y_pt=round(float(center_delta[1]) / sy, 2),
                scale_x=round(float(np.linalg.norm(matrix[:, 0])), 4), scale_y=round(float(np.linalg.norm(matrix[:, 1])), 4),
                matrix=matrix.tolist())
    aligned = cv2.warpAffine(gray, matrix, (gray.shape[1], gray.shape[0]),
                             flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP, borderValue=255)
    return aligned, info
