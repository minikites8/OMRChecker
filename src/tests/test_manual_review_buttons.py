import copy
import pytest
from exam_review import apply_manual_review, _score_summary, refresh_rule_judgments
from review_collaboration import attach_collaboration, check_decisions


def review(question='64', score=6):
    return {'review_id':'manual-controls','items':[{'question':question,'score':score,'auto_status':'需人工复核','manual_status':'待复核','recognized_text':'answer','expected_answer':'answer','confidence':0.9,'ai_status':'AI通过','ai_score':score}], 'objective':[]}


@pytest.mark.parametrize('value,status',[(0,'不通过'),(2.5,'部分得分'),(6,'通过')])
def test_question_64_manual_score_controls_total_and_final_status(value,status):
    report=review();item=report['items'][0]
    apply_manual_review(report,[{'question':'64','status':'待复核','score':value,'text':''}])
    assert item['manual_score']==value and item['manual_status']==status
    assert item['final_status']==status and item['awarded_score']==value
    assert item['score_basis']=='人工复核'
    assert report['score_summary']['total_score']==value
    assert report['score_summary']['pending_score']==0
    item['ai_score']=1;item['ai_status']='AI部分得分'
    refresh_rule_judgments(report)
    assert report['score_summary']['total_score']==value


@pytest.mark.parametrize('value',[-1,6.1,float('nan'),float('inf'),True,False,'','bad'])
def test_invalid_manual_score_is_rejected(value):
    report=review();before=copy.deepcopy(report)
    with pytest.raises(ValueError,match='得分'):
        apply_manual_review(report,[{'question':'64','status':'部分得分','score':value}])
    assert report==before


def test_unit_score_blank_keeps_binary_manual_grading():
    with pytest.raises(ValueError,match='1分'):
        apply_manual_review(review('63',1),[{'question':'63','score':.5,'status':'部分得分'}])


def test_buttons_override_partial_points_and_explicit_clear_restores_ai():
    report=review()
    apply_manual_review(report,[{'question':'64','score':2.5}])
    for status,points in [('不通过',0),('通过',6)]:
        apply_manual_review(report,[{'question':'64','status':status}])
        assert report['items'][0].get('manual_score') is None
        assert report['score_summary']['total_score']==points
    apply_manual_review(report,[{'question':'64','score':3}])
    apply_manual_review(report,[{'question':'64','status':'待复核','score':None}])
    assert report['items'][0].get('manual_score') is None
    assert report['score_summary']['total_score']==6


def test_collaboration_accepts_partial_manual_result_for_64():
    report=attach_collaboration(review())
    payload={'review_id':'manual-controls','expected_revision':report['collaboration']['revision'],'decisions':[{'question':'64','status':'部分得分','score':2.5}]}
    check_decisions(payload,report)
    apply_manual_review(report,payload['decisions'])
    assert report['items'][0]['awarded_score']==2.5


def test_objective_buttons_still_store_full_or_zero():
    report={'items':[],'objective':[{'question':'1','score':2,'recognized':'A','expected':'B','auto_status':'需人工复核'}]}
    for status,points in [('通过',2),('不通过',0)]:
        apply_manual_review(report,[],[{'question':'1','status':status}])
        assert report['objective'][0]['awarded_score']==points


@pytest.mark.parametrize('question,maximum,points', [('64',6,2.5),('31',2,1.25),('35',.5,.25),('63',3,1.5)])
def test_manual_partial_score_survives_http_save_confirm_and_export(tmp_path,monkeypatch,question,maximum,points):
    import json
    from fastapi.testclient import TestClient
    import backend.app as api
    import scan_ui
    root=tmp_path/'reviews';path=root/'manual-controls'/'output'/'review.json';path.parent.mkdir(parents=True)
    original=review(question,maximum);original.update(ok=True,student_name='样例考生',student_id='20260001',paper_type='A')
    path.write_text(json.dumps(original),encoding='utf-8')
    monkeypatch.setattr(scan_ui,'REVIEW_ROOT',root)
    monkeypatch.setattr(scan_ui,'_ensure_review_scores',lambda report:False)
    monkeypatch.setattr(scan_ui.PLATFORM_PERSISTENCE,'sync_file',lambda *a,**kw:None)
    monkeypatch.setattr(scan_ui.CANDIDATE_MANAGER,'root',lambda:root)
    monkeypatch.setattr(api,'current_user',lambda request:{'id':'teacher','role':'teacher','display_name':'老师'})
    client=TestClient(api.app,raise_server_exceptions=False)
    loaded=client.get('/api/review/status',params={'review_id':'manual-controls'}).json()
    payload={'review_id':'manual-controls','expected_revision':loaded['collaboration']['revision'],'decisions':[{'question':question,'status':'部分得分','score':points}]}
    response=client.post('/api/review/confirm',json=payload);assert response.status_code==200,response.text
    saved=response.json();assert saved['items'][0]['manual_score']==points
    assert saved['score_summary']['total_score']==points
    assert saved['grade_confirmation_status']=='待确认' and saved['grade_blockers']==[]
    disk=json.loads(path.read_text(encoding='utf-8'));assert disk['items'][0]['manual_score']==points
    confirmed=client.post('/api/review/confirm-grade',json={'review_id':'manual-controls','expected_revision':saved['collaboration']['revision']})
    assert confirmed.status_code==200,confirmed.text
    assert confirmed.json()['grade_confirmed'] is True
    exported=client.get('/api/candidates/export.json',params={'review_id':'manual-controls'})
    assert exported.status_code==200,exported.text
    exported_question=exported.json()[0]['questions'][0]
    assert exported_question['question_id']==int(question) and exported_question['score']==points
    assert '人工复核' in exported_question['remark']
    before=path.read_bytes()
    invalid=client.post('/api/review/confirm',json={'review_id':'manual-controls','expected_revision':confirmed.json()['collaboration']['revision'],'decisions':[{'question':question,'status':'部分得分','score':7}]})
    assert invalid.status_code==400
    assert path.read_bytes()==before
