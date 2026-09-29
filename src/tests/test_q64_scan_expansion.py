"""Q64 includes border handwriting and the gap between the two scan crops."""
import copy
from types import SimpleNamespace
import cv2
import fitz
import numpy as np
import pytest
import exam_review as er
import objective_view
import review_overlay_pdf as overlay


OLD = {'64思路':(326, er.PAGE_H-484, 216, 260),
       '64代码':(326, er.PAGE_H-770, 216, 276)}


def q64_rects(mode='grouped_61_63'):
    return {label:rect for _,rect,label,_ in er._subjective_crop_regions(mode) if label in OLD}


def test_q64_expands_to_answer_box_and_retains_overlap():
    rects=q64_rects()
    assert rects['64思路'] == pytest.approx((314,er.PAGE_H-496,240,284))
    assert rects['64代码'] == pytest.approx((314,er.PAGE_H-782,240,300))
    thought=rects['64思路'];code=rects['64代码']
    assert code[1]+code[3]-thought[1] == pytest.approx(14)
    for x,y,w,h in rects.values():
        assert x < 316 and x+w > 553
        assert 0 <= x < x+w <= er.PAGE_W and 0 <= y < y+h <= er.PAGE_H


@pytest.mark.parametrize('mode',['grouped_61_63','legacy_subfields'])
def test_q64_remains_on_page_and_leaves_other_regions_clear(mode):
    for page,rect,label,options in er._subjective_crop_regions(mode):
        if label in OLD:
            x,y,w,h=rect
            assert page==1 and x > 287
            assert 0<=y and x+w<=er.PAGE_W and y+h<=er.PAGE_H
        elif label=='31':
            assert rect==(62,180,153,22)
        elif label=='46':
            assert rect==(63,er.PAGE_H-210,224,19)


@pytest.mark.parametrize('label,point',[
    ('64思路',(320,218)),('64思路',(319,300)),('64思路',(550,400)),
    ('64思路',(420,489)),('64代码',(420,489)),('64代码',(420,778)),
])
def test_corrected_crop_includes_previously_clipped_ink(label,point):
    page=np.full((1684,1190),255,dtype=np.uint8)
    sx,sy=page.shape[1]/er.PAGE_W,page.shape[0]/er.PAGE_H
    x,y=point;left,top=round(x*sx),round(y*sy)
    page[top:top+3,left:left+3]=0
    assert er._crop_pdf_rect(page,*OLD[label]).min()==255
    assert er._crop_pdf_rect(page,*q64_rects()[label]).min()==0


@pytest.mark.parametrize('local',[True,False])
def test_recognition_and_saved_ai_images_share_expanded_q64(monkeypatch,tmp_path,local):
    page=np.full((1684,1190),255,dtype=np.uint8)
    sx,sy=page.shape[1]/er.PAGE_W,page.shape[0]/er.PAGE_H
    for x,y in [(320,218),(550,400),(420,489),(420,778)]:
        left,top=round(x*sx),round(y*sy);page[top:top+3,left:left+3]=0
    monkeypatch.setattr(er,'_load_page',lambda path:[('front',page),('back',page)])
    monkeypatch.setattr(er,'prepare_card_alignment',lambda pages,reference=None:(pages,[],[],{},{},0,0))
    monkeypatch.setattr(er,'_save_objective_crops',lambda *args:{})
    monkeypatch.setattr(objective_view,'build_objective_view',lambda *args:{})
    observed={}
    def recognize(images,labels,**kwargs):
        observed.update(zip(labels,images))
        return [SimpleNamespace(text='',confidence=0,error=None) for label in labels]
    monkeypatch.setattr(er,'_recognize_crops',recognize)
    report=er.extract_answer_card([tmp_path/'scan.pdf'],image_dir=tmp_path/'crops',template_config={'local_ocr_enabled':local})
    assert report['crop_adjustments']['algorithm_padding_pt']=={'x':12.0,'y':12.0}
    for label,rect in q64_rects().items():
        expected=er._crop_pdf_rect(page,*(rect if local else er._expand_crop_rect(rect)))
        assert np.array_equal(observed[label],expected)
        display=cv2.imread(report['crop_paths'][label],cv2.IMREAD_GRAYSCALE)
        assert display.min()==0
        expected_ai=er._crop_pdf_rect(page,*er._expand_crop_rect(rect))
        assert display.shape==tuple(size*2 for size in expected_ai.shape)


def test_pdf_overlay_uses_expanded_scan_and_context_bounds(tmp_path):
    page=np.full((1684,1190),255,dtype=np.uint8)
    path=tmp_path/'expanded-q64.pdf'
    overlay.write_overlay_pdf([page,page],{'items':[{'question':'64'}]},path)
    with fitz.open(path) as pdf:
        assert pdf.page_count==2 and len(pdf.get_ocgs())==2
        boxes=[entry for entry in pdf[1].get_drawings() if entry['color'][2]>0.8]
        assert len(boxes)==4
        solid=[entry['rect'] for entry in boxes if entry['dashes']=='[] 0']
        assert len(solid)==2
        assert solid[0].x0<=314 and solid[0].x1>=554
        assert solid[0].y0<=212 and solid[1].y1>=782
        assert solid[0].y1-solid[1].y0>=14
