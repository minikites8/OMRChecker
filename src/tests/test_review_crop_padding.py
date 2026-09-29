"""History/crop regression: synthetic scans, deterministic local recognition."""
from types import SimpleNamespace
import cv2
import numpy as np
import pytest
import exam_review
import objective_view

@pytest.fixture
def crops(monkeypatch, tmp_path):
    page = np.full((1684, 1190), 255, dtype=np.uint8)
    sx, sy = page.shape[1] / exam_review.PAGE_W, page.shape[0] / exam_review.PAGE_H
    page[round((exam_review.PAGE_H-195)*sy):round((exam_review.PAGE_H-187)*sy), round(59*sx):round(61*sx)] = 0
    monkeypatch.setattr(exam_review, '_load_page', lambda path: [('one', page), ('two', page)])
    monkeypatch.setattr(exam_review, '_align_and_order_pages', lambda images: (images, []))
    monkeypatch.setattr(exam_review, '_reference_pages', lambda: [])
    monkeypatch.setattr(exam_review, '_save_objective_crops', lambda *args: {})
    monkeypatch.setattr(objective_view, 'build_objective_view', lambda *args: {})
    observed = {}
    def recognize(images, labels, **kwargs):
        observed.update(zip(labels, images))
        return [SimpleNamespace(text='', confidence=0.0, error=None) for label in labels]
    monkeypatch.setattr(exam_review, '_recognize_crops', recognize)
    def run(local=True, mode='grouped_61_63'):
        report = exam_review.extract_answer_card([tmp_path / 'synthetic.pdf'], image_dir=tmp_path / ('local' if local else 'ai'), template_config={'local_ocr_enabled':local,'material_mode':mode})
        return report, observed, page
    return run

@pytest.mark.parametrize('label,rect', [
    ('31',(62,180,153,22)), ('46',(63,exam_review.PAGE_H-210,224,19)),
    ('61',(63,exam_review.PAGE_H-591.5,224,17.5)),
    ('64思路',(314,exam_review.PAGE_H-496,240,284)), ('64代码',(314,exam_review.PAGE_H-782,240,300)),
])
def test_saved_visual_crops_include_more_context(crops,label,rect):
    report, observed, page = crops()
    saved = cv2.imread(report['crop_paths'][label],cv2.IMREAD_GRAYSCALE)
    original = exam_review._crop_pdf_rect(page,*rect)
    assert saved.shape[0] > original.shape[0]*2
    assert saved.shape[1] > original.shape[1]*2
    assert observed[label].shape == original.shape

def test_ai_only_recognizer_receives_expanded_image_and_margin_stroke(crops):
    report, observed, page = crops(False)
    original = exam_review._crop_pdf_rect(page,62,180,153,22)
    assert observed['31'].shape[1] > original.shape[1]
    assert observed['31'].min() == 0
    saved=cv2.imread(report['crop_paths']['31'],cv2.IMREAD_GRAYSCALE)
    assert saved.shape == tuple(size*2 for size in observed['31'].shape)

def test_legacy_subfields_and_question_39_keep_extra_context(crops):
    report, observed, page = crops(True,'legacy_subfields')
    q61=cv2.imread(report['crop_paths']['61(1)'],0)
    assert q61.shape[0] > observed['61(1)'].shape[0]*2
    q38=cv2.imread(report['crop_paths']['38'],0); q39=cv2.imread(report['crop_paths']['39'],0)
    assert q39.shape[0] > q38.shape[0] and q39.shape[1] > q38.shape[1]

@pytest.mark.parametrize('rect',[(0,0,10,10),(exam_review.PAGE_W-10,exam_review.PAGE_H-10,10,10)])
def test_padding_clamps_to_page_edges(rect):
    x,y,w,h = exam_review._expand_crop_rect(rect)
    assert 0 <= x <= exam_review.PAGE_W and 0 <= y <= exam_review.PAGE_H
    assert x+w <= exam_review.PAGE_W and y+h <= exam_review.PAGE_H
    assert w > rect[2] and h > rect[3]
