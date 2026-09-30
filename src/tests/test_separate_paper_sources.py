"""Independently imported A/B/C references, actual routing and isolation."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
import scan_ui
from backend import app as api
from exam_import import import_exam_and_answers
from exam_answers import read_exam_answers, regrade_exam_question
from exam_paper_sources import paper_identity, regrade_sources, resolve_regrade_source
from exam_regrade import _prepare_review, regrade_exam


def read(path): return json.loads(path.read_text(encoding='utf-8'))


@pytest.fixture
def separate(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_ui, 'IMPORT_ROOT', tmp_path/'imports')
    monkeypatch.setattr(scan_ui, 'REVIEW_ROOT', tmp_path/'reviews')
    monkeypatch.setattr(scan_ui, 'PLATFORM_PERSISTENCE', SimpleNamespace(sync_file=Mock()))
    monkeypatch.setattr(scan_ui, 'AI_REVIEW_WORKERS', {})
    monkeypatch.setattr(scan_ui, 'AI_QUESTION_WORKERS', {})
    monkeypatch.setattr(scan_ui, 'ai_is_configured', lambda:False)
    docs={}
    for index,kind in enumerate('ABC',1):
        doc=import_exam_and_answers({'sections':[{'name':'软件'+kind+'卷','questions':[
            {'id':1,'title':'1. '+kind+'卷选择题','description':kind+'卷题干','type':'single','answer':kind,'score':index*2},
            {'id':31,'title':'31. '+kind+'卷填空题','description':kind+'卷主观题题干','type':'blank','answer':str(index*10),'score':index*2+1}]}]})
        doc['name']='软件'+kind+'卷';docs[kind]=doc
        scan_ui._write_json_atomic(scan_ui.IMPORT_ROOT/('paper-'+kind) / 'normalized_exam.json',doc)
    unrelated=copy.deepcopy(docs['B']);unrelated['name']='数学B卷'
    scan_ui._write_json_atomic(scan_ui.IMPORT_ROOT/'math-B/normalized_exam.json',unrelated)
    record={'ok':True,'review_id':'selected','import_id':'paper-A','paper_type':'A','answer_paper_type':'A',
            'student_name':'验收考生甲','student_id':'000001','owner_user_id':'teacher','card_files':['original.pdf'],
            'objective':[{'question':'1','recognized':'B','manual_status':'','override_answer':False}],
            'items':[{'question':'31','recognized_text':'20','confidence':.99,'manual_status':'','manual_text':'','handwriting_urls':[]}]}
    record=_prepare_review(record,docs['A'],'before');record.update(paper_type='B',grade_confirmed=True)
    selected=scan_ui.REVIEW_ROOT/'selected/output/review.json';scan_ui._write_review(selected,record)
    neighbor=copy.deepcopy(record);neighbor.update(review_id='neighbor',paper_type='A')
    other=scan_ui.REVIEW_ROOT/'neighbor/output/review.json';scan_ui._write_review(other,neighbor)
    starter=Mock(side_effect=lambda payload:scan_ui._load_review(payload['review_id'])[2])
    monkeypatch.setattr(scan_ui,'start_ai_review',starter)
    return SimpleNamespace(docs=docs,selected=selected,neighbor=other,starter=starter)


@pytest.mark.parametrize('name,kind,family', [('软件B卷','B','软件'),('软件 A 卷','A','软件'),('软件（Ｃ卷）','C','软件'),('软件(B卷)','B','软件'),('ABC卷','',''),('C语言考试','',''),('B卷','B',''),('2026期中软件B卷','B','2026期中软件')])
def test_named_paper_identity(name,kind,family):
    assert paper_identity({'name':name})==(kind,family)


def test_separate_imports_are_exposed_without_changing_answer_editor_keys(separate):
    view=read_exam_answers('paper-B')
    assert view['paper_types']==[] and view['paper_type']==''
    assert [(s['import_id'],s['paper_type']) for s in view['regrade_sources']]==[('paper-A','A'),('paper-B','B'),('paper-C','C')]
    assert view['regrade_sources'][1]['label']=='B 卷 · 软件B卷'
    assert view['questions'][1]['answers']=={'31':'20'}


def test_selected_type_routes_to_separate_reference_and_preserves_neighbor(separate):
    before=read(separate.selected);neighbor=separate.neighbor.read_bytes()
    references={k:(scan_ui.IMPORT_ROOT/('paper-'+k)/'normalized_exam.json').read_bytes() for k in 'ABC'}
    result=regrade_exam({'import_id':'paper-A','review_ids':['selected'],'paper_type':'B','answer_import_id':'paper-B'})
    assert result['completed']==1 and result['failed']==0
    current=read(separate.selected)
    assert current['import_id']==current['answer_import_id']=='paper-B'
    assert current['paper_type']==current['answer_paper_type']=='B'
    assert current['answer_import_name']=='软件B卷'
    assert current['objective'][0]['expected']=='B' and current['objective'][0]['score']==4
    assert current['items'][0]['expected_answer']=='20' and current['items'][0]['score']==5
    assert 'B卷' in current['items'][0]['source_content']
    assert current['score_summary']['total_score']==current['score_summary']['possible_score']==9
    assert current['grade_confirmed'] is False
    assert current['regrade']['source_import_id']=='paper-A' and current['regrade']['answer_import_id']=='paper-B'
    assert separate.neighbor.read_bytes()==neighbor
    assert read(next((separate.selected.parent/'regrade_history').glob('*.json')))==before
    for k,content in references.items(): assert (scan_ui.IMPORT_ROOT/('paper-'+k)/'normalized_exam.json').read_bytes()==content
    separate.starter.assert_called_once_with({'review_id':'selected'})


def test_saved_type_routes_legacy_regrade_requests_to_matching_import(separate):
    result=regrade_exam({'import_id':'paper-A','review_ids':['selected']})
    assert result['completed']==1 and read(separate.selected)['import_id']=='paper-B'


def test_batch_uses_each_reviews_saved_type(separate):
    result=regrade_exam({'import_id':'paper-A'})
    assert result['completed']==2
    assert read(separate.selected)['import_id']=='paper-B'
    assert read(separate.neighbor)['import_id']=='paper-A'


def test_correctly_linked_b_sheet_keeps_reference(separate):
    record=read(separate.selected);record.update(import_id='paper-B',answer_paper_type='')
    scan_ui._write_review(separate.selected,record)
    result=regrade_exam({'import_id':'paper-B','review_ids':['selected'],'paper_type':'B'})
    assert result['completed']==1 and read(separate.selected)['answer_paper_type']=='B'


def test_manual_review_is_preserved_when_relinking(separate):
    record=read(separate.selected);record['items'][0].update(manual_status='部分得分',manual_score=1,manual_text='教师修正')
    scan_ui._write_review(separate.selected,record)
    regrade_exam({'import_id':'paper-A','review_ids':['selected'],'paper_type':'B'})
    current=read(separate.selected)
    assert current['items'][0]['awarded_score']==1 and current['items'][0]['manual_text']=='教师修正'
    assert current['student_id']==record['student_id'] and current['card_files']==record['card_files']


@pytest.mark.parametrize('target',['math-B','paper-C','../paper-B','missing'])
def test_wrong_reference_rejected_before_record_mutation(separate,target):
    before=separate.selected.read_bytes()
    result=regrade_exam({'import_id':'paper-A','review_ids':['selected'],'paper_type':'B','answer_import_id':target})
    assert result['failed']==1 and separate.selected.read_bytes()==before
    separate.starter.assert_not_called()


@pytest.mark.parametrize('target',[None,'',123])
def test_invalid_reference_value_rejected(separate,target):
    before=separate.selected.read_bytes()
    with pytest.raises(ValueError):regrade_exam({'import_id':'paper-A','review_ids':['selected'],'answer_import_id':target})
    assert separate.selected.read_bytes()==before


def test_duplicate_b_import_requires_explicit_selection(separate):
    scan_ui._write_json_atomic(scan_ui.IMPORT_ROOT/'paper-B-copy/normalized_exam.json',separate.docs['B'])
    sources=regrade_sources('paper-A',separate.docs['A'])
    assert all(s['import_id'] in s['label'] for s in sources if s['paper_type']=='B')
    before=separate.selected.read_bytes()
    result=regrade_exam({'import_id':'paper-A','review_ids':['selected']})
    assert result['failed']==1 and separate.selected.read_bytes()==before
    result=regrade_exam({'import_id':'paper-A','review_ids':['selected'],'paper_type':'B','answer_import_id':'paper-B-copy'})
    assert result['completed']==1 and read(separate.selected)['import_id']=='paper-B-copy'


def test_missing_and_deleted_b_reference_is_not_substituted(separate):
    doc=copy.deepcopy(separate.docs['B']);doc['deleted_at']='2026-09-30'
    scan_ui._write_json_atomic(scan_ui.IMPORT_ROOT/'paper-B/normalized_exam.json',doc)
    before=separate.selected.read_bytes()
    result=regrade_exam({'import_id':'paper-A','review_ids':['selected']})
    assert result['failed']==1 and separate.selected.read_bytes()==before


def test_explicit_reference_restricted_to_single_sheet(separate):
    with pytest.raises(ValueError):regrade_exam({'import_id':'paper-A','answer_import_id':'paper-B'})
    with pytest.raises(ValueError):regrade_exam({'import_id':'paper-A','review_ids':['selected','neighbor'],'answer_import_id':'paper-B'})


def test_plain_single_import_remains_available(separate):
    plain=copy.deepcopy(separate.docs['A']);plain['name']='期中考试'
    options=regrade_sources('plain',plain)
    assert len(options)==1 and options[0]['paper_type']=='' and '期中考试' in options[0]['label']


def test_answer_editor_single_question_regrade_after_relink(separate):
    regrade_exam({'import_id':'paper-A','review_ids':['selected'],'paper_type':'B'})
    view=read_exam_answers('paper-B')
    result=regrade_exam_question({'import_id':'paper-B','paper_type':'','question_key':'1:1','revision':view['revision']})
    assert result['completed']==1
    assert read(separate.selected)['objective'][0]['expected']=='B'


def test_fastapi_exposes_separate_options_and_routes_reference(separate):
    with TestClient(api.app) as client:
        view=client.get('/api/exam/answers',params={'import_id':'paper-B'})
        assert view.status_code==200 and len(view.json()['regrade_sources'])==3
        result=client.post('/api/exam/regrade',json={'import_id':'paper-A','review_ids':['selected'],'paper_type':'B','answer_import_id':'paper-B'})
        assert result.status_code==200 and result.json()['completed']==1
    assert read(separate.selected)['import_id']=='paper-B'

def test_single_named_reference_handles_old_cached_variant(separate):
    record=read(separate.selected);record.update(import_id='paper-B',answer_paper_type='A')
    prepared=_prepare_review(record,separate.docs['B'],'question-refresh')
    assert prepared['answer_paper_type']=='B' and prepared['items'][0]['expected_answer']=='20'


def test_single_named_reference_rejects_explicit_wrong_variant(separate):
    with pytest.raises(ValueError):_prepare_review(read(separate.selected),separate.docs['B'],'explicit',paper_type='A')
