"""Expanded original-answer scans, including historical review records."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
import pytest
import exam_review
import scan_ui


def test_historical_display_crop_reuses_alignment_and_keeps_margin_ink(tmp_path, monkeypatch):
    page = np.full((1684, 1190), 255, dtype=np.uint8)
    sx, sy = page.shape[1] / exam_review.PAGE_W, page.shape[0] / exam_review.PAGE_H
    page[round(510*sy):round(520*sy), round(323*sx):round(325*sx)] = 0
    monkeypatch.setattr(exam_review, '_load_page', lambda path: [('one', page), ('two', page)])
    monkeypatch.setattr(exam_review, '_align_and_order_pages', lambda pages: (pages, []))
    monkeypatch.setattr(exam_review, '_recognize_crops', lambda *a, **kw: pytest.fail('Display refresh invokes recognition'))
    paths = exam_review.regenerate_display_crops([tmp_path/'scan.pdf'], tmp_path/'display', {
        'material_mode': 'grouped_61_63', 'page1_program_y_pt': 0,
        'regions': {'program': {'matrix': [[1, 0, 6], [0, 1, 0]]}}})
    image = cv2.imread(paths['64代码'], cv2.IMREAD_GRAYSCALE)
    old = exam_review._crop_pdf_rect(page, 326, exam_review.PAGE_H-770, 216, 276)
    assert image.shape[1] > old.shape[1] * 2
    assert image.min() == 0 and old.min() == 255
    expected = exam_review._crop_aligned_rect(page, exam_review._expand_crop_rect((62,180,153,22)), [[1,0,6],[0,1,0]])
    assert cv2.imread(paths['31'], 0).shape == tuple(size*2 for size in expected.shape)
    assert len(paths) == 35


@pytest.fixture
def legacy_review(tmp_path, monkeypatch):
    root = tmp_path/'reviews'; identifier = 'history-scan'; input_dir = root/identifier/'input'
    input_dir.mkdir(parents=True); (input_dir/'scan.png').write_bytes(b'synthetic-scan')
    path = root/identifier/'output'/'review.json'; path.parent.mkdir()
    review = {'ok':True, 'review_id':identifier, 'card_files':['scan.png'],
              'crop_adjustments':{'version':4,'material_mode':'grouped_61_63'},
              'items':[{'question':'64','recognized_text':'saved text','ai_score':5,'awarded_score':4,
                        'handwriting_urls':['/reviews/history-scan/output/handwriting/64_thought.png','/reviews/history-scan/output/handwriting/64_code.png']}],
              'objective':[], 'score_summary':{'total_score':4,'possible_score':10}}
    path.write_text(json.dumps(review), encoding='utf-8')
    monkeypatch.setattr(scan_ui,'REVIEW_ROOT',root)
    monkeypatch.setattr(scan_ui,'_ensure_review_scores',lambda r:False)
    monkeypatch.setattr(scan_ui,'PLATFORM_PERSISTENCE',SimpleNamespace(sync_file=lambda *a,**kw:None,sync_tree=lambda *a,**kw:None))
    return path, review


def install_fake_crops(monkeypatch):
    calls=[]
    def regenerate(files, destination, adjustments):
        calls.append((files,destination,copy.deepcopy(adjustments)))
        destination.mkdir(parents=True,exist_ok=True)
        result={}
        for label,name in [('64思路','64_thought'),('64代码','64_code')]:
            target=destination/(name+'.png'); cv2.imwrite(str(target),np.full((12,24),255,dtype=np.uint8));result[label]=str(target)
        return result
    monkeypatch.setattr(exam_review,'regenerate_display_crops',regenerate)
    return calls


def test_old_review_gets_expanded_display_urls_and_keeps_grades(legacy_review, monkeypatch):
    path, original=legacy_review; calls=install_fake_crops(monkeypatch)
    result=scan_ui.read_review_status(original['review_id'])
    item=result['items'][0]
    assert len(calls)==1
    assert item['handwriting_display_urls']==[
        '/reviews/history-scan/output/handwriting/display_v2/64_thought.png',
        '/reviews/history-scan/output/handwriting/display_v2/64_code.png']
    for key,value in original['items'][0].items(): assert item[key]==value
    assert result['score_summary']==original['score_summary']
    saved=json.loads(path.read_text(encoding='utf-8'))
    assert saved['items'][0]['handwriting_display_urls']==item['handwriting_display_urls']
    scan_ui.read_review_status(original['review_id'])
    assert len(calls)==1


def test_refresh_rebuilds_a_missing_cached_image(legacy_review, monkeypatch):
    path,original=legacy_review; calls=install_fake_crops(monkeypatch)
    scan_ui.read_review_status(original['review_id'])
    (path.parent/'handwriting'/'display_v2'/'64_code.png').unlink()
    scan_ui.read_review_status(original['review_id'])
    assert len(calls)==2


def test_missing_original_scan_preserves_existing_review(legacy_review, monkeypatch):
    path,original=legacy_review; calls=install_fake_crops(monkeypatch)
    (path.parent.parent/'input'/'scan.png').unlink()
    result=scan_ui.read_review_status(original['review_id'])
    assert result['items']==original['items']
    assert calls==[]


def test_current_expanded_crops_keep_existing_urls(legacy_review, monkeypatch):
    path,original=legacy_review; calls=install_fake_crops(monkeypatch)
    original['crop_adjustments'].update(version=5,display_padding_pt={'x':4.0,'y':2.0},algorithm_padding_pt={'x':12.0,'y':12.0})
    path.write_text(json.dumps(original),encoding='utf-8')
    result=scan_ui.read_review_status(original['review_id'])
    assert calls==[] and result['items']==original['items']


def test_display_failure_preserves_review_and_original_images(legacy_review, monkeypatch):
    path,original=legacy_review
    monkeypatch.setattr(exam_review,'regenerate_display_crops',lambda *a: (_ for _ in ()).throw(ValueError('unreadable scan')))
    result=scan_ui.read_review_status(original['review_id'])
    assert result['items']==original['items']
    assert result['score_summary']==original['score_summary']


def test_legacy_subfields_and_single_page_refresh(tmp_path, monkeypatch):
    page=np.full((1684,1190),255,dtype=np.uint8)
    monkeypatch.setattr(exam_review,'_load_page',lambda path:[('one',page),('two',page)])
    monkeypatch.setattr(exam_review,'_align_and_order_pages',lambda pages:(pages,[]))
    paths=exam_review.regenerate_display_crops([tmp_path/'scan.pdf'],tmp_path/'legacy',{'material_mode':'legacy_subfields'})
    assert all(label in paths for label in ['61(1)','61(2)','63(3)','64思路','64代码'])
    assert len(paths)==39
    monkeypatch.setattr(exam_review,'_load_page',lambda path:[('one',page)])
    paths=exam_review.regenerate_display_crops([tmp_path/'scan.png'],tmp_path/'single',{})
    assert set(paths)=={str(n) for n in range(31,46)}


def test_expanded_preview_is_served_by_review_api_and_image_route(legacy_review,monkeypatch):
    import threading
    from urllib.request import urlopen
    path,original=legacy_review; install_fake_crops(monkeypatch)
    server=scan_ui.create_server('127.0.0.1',0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_address[1])
    try:
        with urlopen(base+'/api/review/status?review_id=history-scan',timeout=5) as response:
            review=json.loads(response.read().decode('utf-8'))
        urls=review['items'][0]['handwriting_display_urls']
        assert len(urls)==2
        for url in urls:
            with urlopen(base+url,timeout=5) as response:
                assert response.status==200
                assert response.headers.get_content_type()=='image/png'
                image=cv2.imdecode(np.frombuffer(response.read(),dtype=np.uint8),0)
                assert image.shape==(12,24)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_legacy_record_resolves_id_from_its_directory(legacy_review, monkeypatch):
    path,original=legacy_review; install_fake_crops(monkeypatch)
    identifier=original.pop('review_id')
    path.write_text(json.dumps(original),encoding='utf-8')
    result=scan_ui.read_review_status(identifier)
    assert result['items'][0]['handwriting_display_urls'][0].startswith('/reviews/history-scan/')


def test_pre_expansion_q64_crops_refresh_once_without_regrading(legacy_review, monkeypatch):
    path, original = legacy_review
    calls = install_fake_crops(monkeypatch)
    original['crop_adjustments'].update(version=5, display_padding_pt={'x':4.0,'y':2.0})
    path.write_text(json.dumps(original), encoding='utf-8')
    result = scan_ui.read_review_status(original['review_id'])
    assert len(calls) == 1
    assert result['subjective_display'] == {'version':2, 'padding_pt':{'x':4.0,'y':2.0},
                                           'algorithm_padding_pt':{'x':12.0,'y':12.0}}
    for key, value in original['items'][0].items():
        assert result['items'][0][key] == value
    assert result['score_summary'] == original['score_summary']
    scan_ui.read_review_status(original['review_id'])
    assert len(calls) == 1
