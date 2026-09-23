"""Scanned objective page and inverse-aligned annotation geometry; no OCR/AI calls."""
import json
from pathlib import Path
import cv2
import numpy as np

VIEW_VERSION = 2

def objective_layout():
    rows = []
    for index in range(15):
        col, row = divmod(index, 5)
        left = [52, 226, 400][col]
        rows.append((str(index + 1), 'single', left - 13, 348 + row * 20.5,
                     left + 139, left + 28, 27, 'ABCD'))
    for row in range(5):
        rows.append((str(16 + row), 'multiple_tf', 49, 503 + row * 20.5,
                     208, 90, 29, 'ABCD'))
    for index in range(10):
        col, row = divmod(index, 5)
        left = 337 + col * 112
        rows.append((str(index + 21), 'multiple_tf', left - 13, 503 + row * 20.5,
                     left + 97, left + 28, 37, 'TF'))
    return rows


def build_objective_view(image, destination, regions=None, page_width=595.2755905511812,
                         page_height=841.8897637795277):
    """Map canonical bubble coordinates back onto the actual normalized scan."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    height, width = image.shape[:2]
    sx, sy = width / page_width, height / page_height
    questions = []
    regions = regions or {}
    for question, region, left, center_y, right, first, spacing, choices in objective_layout():
        correction = regions.get(region, {})
        matrix = np.asarray(correction.get('matrix', [[1, 0, 0], [0, 1, 0]]), dtype=float)
        if matrix.shape != (2, 3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix[:, :2])) < 1e-8:
            matrix = np.array([[1., 0., 0.], [0., 1., 0.]])
        inverse = cv2.invertAffineTransform(matrix)
        def points(values):
            scaled = np.asarray(values, dtype=float) * [sx, sy]
            mapped = scaled @ inverse[:, :2].T + inverse[:, 2]
            return [[round(float(x), 3), round(float(y), 3)] for x, y in mapped]
        polygon = points([(left, center_y - 12), (right, center_y - 12),
                          (right, center_y + 12), (left, center_y + 12)])
        bubbles = []
        for index, choice in enumerate(choices):
            x = first + spacing * index
            bubbles.append({'choice': choice, 'polygon': points([(x - 6, center_y - 6),
                (x + 6, center_y - 6), (x + 6, center_y + 6), (x - 6, center_y + 6)])})
        questions.append({'question': question, 'polygon': polygon, 'bubbles': bubbles,
                          'alignment_status': correction.get('status', '页面配准')})
    ys = [p[1] for question in questions for p in question['polygon']]
    xs = [p[0] for question in questions for p in question['polygon']]
    x1 = max(0, int(min(xs) - 18 * sx)); x2 = min(width, int(max(xs) + 18 * sx))
    y1 = max(0, int(min(ys) - 42 * sy)); y2 = min(height, int(max(ys) + 24 * sy))
    assert x2 > x1 and y2 > y1, '答题区坐标超出扫描件'
    for filename, frame in [('page.png', image), ('objective.png', image[y1:y2, x1:x2])]:
        ok, buffer = cv2.imencode('.png', frame)
        if not ok:
            raise ValueError('答题卡预览图生成失败')
        (destination / filename).write_bytes(buffer.tobytes())
    manifest = {'version': VIEW_VERSION,
                'page': {'image': 'page.png', 'width': width, 'height': height, 'x': 0, 'y': 0},
                'focus': {'image': 'objective.png', 'width': x2-x1, 'height': y2-y1, 'x': x1, 'y': y1},
                'questions': questions}
    temporary = destination / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(destination / 'manifest.json')
    return manifest
