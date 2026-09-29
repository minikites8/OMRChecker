"""Pre-upload preview must stay independent from grading, AI and review records."""
import base64
from pathlib import Path
import fitz
import numpy as np
import pytest
import exam_review
import scan_ui
import answer_alignment
from src.tests.test_saas_workspaces import ALICE, http_service, store
from workspace_context import workspace_scope


def payload(pages=2):
    with fitz.open() as document:
        for _ in range(pages):
            page=document.new_page(width=exam_review.PAGE_W,height=exam_review.PAGE_H)
            page.insert_text((60,60),'Scan preview test')
        encoded=base64.b64encode(document.tobytes()).decode('ascii')
    return {'card_files':[{'name':'preview.pdf','data':encoded}], 'template_id':'software-16th-abc'}


@pytest.fixture
def preview_only(monkeypatch):
    monkeypatch.setattr(exam_review,'_align_and_order_pages',lambda images,**kwargs:(images,[]))
    refs=[np.full((1684,1191),255,dtype=np.uint8)]*2
    monkeypatch.setattr(exam_review,'_reference_pages',lambda *args:refs)
    monkeypatch.setattr(exam_review,'_estimate_local_vertical_shift',lambda *args:(0,1))
    monkeypatch.setattr(exam_review,'align_printed_region',lambda image,reference,region,**kwargs:
        (image,{'status':'已校正','matrix':[[1,0,0],[0,1,0]]}))
    def unexpected(*args,**kwargs):
        pytest.fail('Preview should only locate scans and read bubbles')
    monkeypatch.setattr(exam_review,'_recognize_crops',unexpected)
    monkeypatch.setattr(scan_ui,'start_batch_review',unexpected)
    monkeypatch.setattr(scan_ui,'start_review_job',unexpected)


@pytest.mark.parametrize('prefix',['/w','/api/w'])
def test_upload_preview_produces_both_layers_without_grading_or_original_storage(http_service,preview_only,prefix):
    client,store,root=http_service
    workspace=store.create(ALICE,'Preview')
    base=prefix+'/'+workspace['id']
    response=client.post(base+'/api/review/scan-preview',json=payload(),headers={'Cookie':'alice'})
    assert response.status_code==200,response.text
    data=response.json();assert data['ok'] and data['pages']==2 and data['warnings']==[]
    assert data['objective']==30 and data['fill']==35
    pdf=client.get(data['pdf_url'],headers={'Cookie':'alice'})
    assert pdf.status_code==200 and 'application/pdf' in pdf.headers['content-type']
    with fitz.open(stream=pdf.content,filetype='pdf') as doc:
        assert doc.page_count==2 and len(doc.get_ocgs())==2
    with workspace_scope(workspace):
        assert not list(Path(str(scan_ui.REVIEW_ROOT)).glob('*/output/review.json'))
        assert not list(Path(str(scan_ui.JOBS_ROOT)).glob('.scan-preview-*'))
        assert len(list(Path(str(scan_ui.JOBS_ROOT)).rglob('*.*')))==1
    for cookie,status in [('',401),('bob',404),('charlie',404)]:
        assert client.get(data['pdf_url'],headers={'Cookie':cookie}).status_code==status
        assert client.post(base+'/api/review/scan-preview',json=payload(),headers={'Cookie':cookie}).status_code==status


def test_single_page_and_poor_alignment_offer_actionable_rescan_warnings(http_service,preview_only,monkeypatch):
    client,store,root=http_service;workspace=store.create(ALICE,'Partial')
    monkeypatch.setattr(exam_review,'align_printed_region',lambda image,reference,region,**kwargs:(image,{'status':'待复核'}))
    response=client.post('/api/w/'+workspace['id']+'/api/review/scan-preview',json=payload(1),headers={'Cookie':'alice'})
    assert response.status_code==200,response.text
    assert any('1 页' in text and '2 页' in text for text in response.json()['warnings'])
    assert any('定位待复核' in text for text in response.json()['warnings'])


@pytest.mark.parametrize('files',[[],[{}]*3,[{'name':'one.pdf'},{'name':'two.png'}],['bad'],[{'name':'a.pdf','data':'broken'}]])
def test_invalid_preview_groups_return_readable_errors(http_service,files):
    client,store,root=http_service;workspace=store.create(ALICE,'Validation')
    response=client.post('/api/w/'+workspace['id']+'/api/review/scan-preview',json={'card_files':files},headers={'Cookie':'alice'})
    assert response.status_code==400,response.text
    assert response.json()['ok'] is False


def test_preview_preserves_every_source_page_including_extra_pages(http_service,preview_only):
    client,store,root=http_service;workspace=store.create(ALICE,'Extra pages')
    response=client.post('/api/w/'+workspace['id']+'/api/review/scan-preview',json=payload(3),headers={'Cookie':'alice'})
    assert response.status_code==200,response.text
    assert response.json()['pages']==3
    assert any('3 页' in text for text in response.json()['warnings'])
