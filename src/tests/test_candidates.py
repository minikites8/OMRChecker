import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request,urlopen
import cv2
import numpy as np
import pytest
from candidate_identity import name_fields,normalize_name,prepare_name_crop,merge_name_fields
from candidate_manager import CandidateManager,candidate_record,grade_confirmation_blockers


def prediction(text='张三',confidence=.98,error=None):return SimpleNamespace(text=text,confidence=confidence,error=error)
def page_with_name():
    image=np.full((1684,1191),255,np.uint8)
    cv2.putText(image,'NAME',(908,327),cv2.FONT_HERSHEY_SIMPLEX,.7,0,2)
    cv2.line(image,(900,341),(1096,341),0,2)
    return image


def manager_fixture(tmp_path,recognizer=None):
    root=tmp_path/'reviews';root.mkdir()
    review={'ok':True,'review_id':'review-01','student_id':'202619240110','items':[],
            'score_summary':{'total_score':33,'possible_score':100},'objective':[{'question':'1','recognized':'C'}]}
    folder=root/'review-01/output/handwriting/objective_view';folder.mkdir(parents=True)
    (folder/'page.png').write_bytes(cv2.imencode('.png',page_with_name())[1].tobytes())
    path=folder.parents[1]/'review.json';path.write_text(json.dumps(review),encoding='utf-8')
    def load(identifier):
        p=root/identifier/'output/review.json'
        if not p.is_file():raise ValueError('复核任务不存在')
        return identifier,p,json.loads(p.read_text(encoding='utf-8'))
    def write(p,r):p.write_text(json.dumps(r,ensure_ascii=False),encoding='utf-8')
    manager=CandidateManager(lambda:root,load,write,threading.RLock(),lambda _:None,
                             recognizer or (lambda crops,labels:[prediction()]))
    return manager,load,path


def test_name_crop_keeps_preview_and_removes_only_ocr_underline(tmp_path):
    image=page_with_name();crop,ratio=prepare_name_crop(image,tmp_path)
    raw=cv2.imdecode(np.frombuffer((tmp_path/'identity/name.png').read_bytes(),np.uint8),0)
    assert raw.shape==crop.shape and ratio>.006
    assert (raw<100).sum()>(crop<100).sum()>0
    assert image[341,950]==0


def test_blank_name_line_does_not_hallucinate():
    image=np.full((1684,1191),255,np.uint8);cv2.line(image,(900,341),(1096,341),0,2)
    _,ratio=prepare_name_crop(image)
    result=name_fields(prediction('姓名'),ratio)
    assert ratio<.006 and result['student_name']=='' and result['student_name_status']=='待填写'


@pytest.mark.parametrize('text,expected',[('姓名： 张 三','张三'),('张飞___','张飞'),(' Amina Ali ','Amina Ali'),('阿布·热合曼','阿布·热合曼')])
def test_name_cleanup(text,expected):assert normalize_name(text)==expected


def test_ocr_name_always_has_human_review_status():
    result=name_fields(prediction(),.08)
    assert result['student_name']=='张三' and result['student_name_status']=='待确认'
    assert result['student_name_confidence']==.98


def test_invalid_name_and_non_finite_confidence():
    assert name_fields(prediction('202619240110'),.08)['student_name']==''
    assert name_fields(prediction('张三',float('nan')),.08)['student_name_confidence']==0
    assert name_fields(prediction('',0,'model failure'),.08)['student_name_status']=='识别异常'


def test_background_ocr_preserves_manual_name():
    review={'student_name':'李四','student_name_source':'manual','student_name_status':'已确认'}
    merge_name_fields(review,name_fields(prediction('张三'),.08))
    assert review['student_name']=='李四' and review['student_name_status']=='已确认'
    assert review['name_ocr']=='张三'


def test_new_answer_card_recognizes_name_in_the_same_ocr_batch(monkeypatch,tmp_path):
    import exam_review as er
    image=page_with_name();calls=[]
    monkeypatch.setattr(er,'_load_page',lambda _: [('p1',image)])
    monkeypatch.setattr(er,'_align_and_order_pages',lambda images:(images,[1]))
    monkeypatch.setattr(er,'_reference_pages',lambda:[])
    def recognize(crops,labels):
        calls.append(labels);return [prediction('张三' if label=='姓名' else '') for label in labels]
    monkeypatch.setattr(er,'_recognize_crops',recognize)
    card=er.extract_answer_card([tmp_path/'card.png'],tmp_path/'crops')
    assert len(calls)==1 and calls[0][-1]=='姓名'
    assert card['student_name']=='张三' and '姓名' not in card['text_fields']
    assert (tmp_path/'crops/identity/name.png').is_file()


def test_review_build_carries_identity_without_changing_scores(monkeypatch):
    import exam_review as er
    monkeypatch.setattr(er,'extract_answer_card',lambda *a,**k:{'student_id':'202619240110','student_name':'张三','student_name_status':'待确认','text_fields':{},'crop_paths':{},'objective':{},'ocr_errors':[]})
    review=er._build_review_from_maps({}, {},{},[])
    assert review['student_name']=='张三' and review['student_name_status']=='待确认'
    assert review['score_summary']['total_score']==0


def test_candidate_list_and_manual_save_persist_only_identity(tmp_path):
    manager,load,path=manager_fixture(tmp_path)
    before=load('review-01')[2]
    saved=manager.save({'review_id':'review-01','student_name':'李四','student_id':'002619240110','paper_type':'b'})
    after=load('review-01')[2]
    assert saved['candidate']['student_name_status']=='已确认'
    assert after['student_id']=='002619240110' and after['paper_type']=='B'
    assert after['student_id_ocr']=='202619240110'
    assert after['objective']==before['objective'] and after['score_summary']==before['score_summary']
    assert manager.list()['candidates'][0]['student_name']=='李四'


@pytest.mark.parametrize('payload',[{'review_id':'../review-01'},{'review_id':'review-01','student_id':'123'}, {'review_id':'review-01','student_name':'<script>'},{'review_id':'review-01','paper_type':'D'}])
def test_invalid_identity_input_rejected(tmp_path,payload):
    manager,_,_=manager_fixture(tmp_path)
    with pytest.raises(ValueError):manager.save(payload)


def test_async_ocr_keeps_scores_and_reports_progress(tmp_path):
    manager,load,_=manager_fixture(tmp_path)
    result=manager.start({'review_ids':['review-01','review-01']})
    assert result['name_job']['total']==1
    manager.future.result(timeout=5)
    data=manager.list();record=data['candidates'][0]
    assert data['name_job']['completed']==1 and data['name_job']['failed']==0
    assert record['student_name']=='张三' and record['name_image_url'].endswith('/identity/name.png')
    assert load('review-01')[2]['score_summary']['total_score']==33
    manager.start({});assert manager.job['total']==0


def test_ocr_job_preserves_concurrent_manual_edit(tmp_path):
    entered=threading.Event();resume=threading.Event()
    def recognize(*_):entered.set();assert resume.wait(3);return [prediction('张三')]
    manager,load,_=manager_fixture(tmp_path,recognize)
    manager.start({'review_ids':['review-01']});assert entered.wait(3)
    manager.save({'review_id':'review-01','student_name':'李四','student_id':'202619240110','paper_type':'A'})
    resume.set();manager.future.result(timeout=5)
    assert load('review-01')[2]['student_name']=='李四'
    assert load('review-01')[2]['student_name_status']=='已确认'


def test_records_stay_separate_for_repeated_student_id(tmp_path):
    manager,load,path=manager_fixture(tmp_path)
    other=path.parents[2]/'review-02/output/review.json';other.parent.mkdir(parents=True)
    other.write_text(path.read_text(encoding='utf-8'),encoding='utf-8')
    records=manager.list()['candidates']
    assert len(records)==2 and {r['review_id'] for r in records}=={'review-01','review-02'}


def test_candidate_http_endpoints(tmp_path,monkeypatch):
    import scan_ui
    manager,_,_=manager_fixture(tmp_path);monkeypatch.setattr(scan_ui,'CANDIDATE_MANAGER',manager)
    server=scan_ui.create_server('127.0.0.1',0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_address[1])
    try:
        assert json.load(urlopen(base+'/api/candidates'))['candidates'][0]['student_id']=='202619240110'
        payload={'review_id':'review-01','student_name':'张三','student_id':'202619240110','paper_type':'C'}
        request=Request(base+'/api/candidates/save',json.dumps(payload).encode(),{'Content-Type':'application/json'})
        assert json.load(urlopen(request))['candidate']['student_name']=='张三'
        request=Request(base+'/api/candidates/recognize',b'{"review_ids":["review-01"]}',{'Content-Type':'application/json'})
        assert json.load(urlopen(request))['ok'];manager.future.result(timeout=5)
    finally:server.shutdown();server.server_close();thread.join(timeout=3)

def test_grade_confirmation_blockers_and_candidate_status(tmp_path):
    review = {
        'review_id': 'review-01',
        'objective': [
            {'question': '1', 'auto_status': '自动通过'},
            {'question': '2', 'auto_status': '不通过'},
        ],
        'items': [
            {'question': '61', 'ai_status': 'AI通过'},
            {'question': '62', 'ai_status': 'AI需复核'},
        ],
    }
    blockers = grade_confirmation_blockers(review)
    assert blockers == ['第62题待复核']
    review['items'][1]['manual_status'] = '通过'
    assert grade_confirmation_blockers(review) == []
    record = candidate_record({**review, 'grade_confirmed': False}, tmp_path)
    assert record['grade_confirmation_status'] == '待确认'
    review['grade_confirmed'] = True
    assert candidate_record(review, tmp_path)['grade_confirmation_status'] == '已确认'


def test_selected_scored_candidate_exports_before_grade_confirmation(tmp_path, monkeypatch):
    import scan_ui
    manager, load, path = manager_fixture(tmp_path)
    monkeypatch.setattr(scan_ui, 'CANDIDATE_MANAGER', manager)
    review = load('review-01')[2]
    review['score_summary'] = {'total_score': 83, 'possible_score': 100}
    path.write_text(json.dumps(review, ensure_ascii=False), encoding='utf-8')
    exported = scan_ui.export_confirmed_grades(['review-01'])
    assert len(exported) == 1
    assert exported[0]['student_name'] == ''
    assert exported[0]['questions'] == [{'question_id': 1, 'score': 0, 'remark': '待复核'}]


def test_confirm_grade_and_export_only_confirmed_records(tmp_path, monkeypatch):
    import scan_ui
    manager, load, path = manager_fixture(tmp_path)
    monkeypatch.setattr(scan_ui, 'CANDIDATE_MANAGER', manager)
    monkeypatch.setattr(scan_ui, 'REVIEW_ROOT', tmp_path / 'reviews')
    review = load('review-01')[2]
    review['objective'][0].update(auto_status='自动通过', final_status='自动通过', score=2)
    review['items'] = [{'question': '61', 'ai_status': 'AI通过', 'score': 3}]
    review['score_summary'] = {'total_score': 5, 'possible_score': 5, 'objective_score': 2, 'text_score': 3, 'failed_score': 0}
    path.write_text(json.dumps(review, ensure_ascii=False), encoding='utf-8')
    confirmed = scan_ui.confirm_review_grade({'review_id': 'review-01'})
    assert confirmed['grade_confirmed'] is True
    assert confirmed['grade_confirmation_status'] == '已确认'
    exported = scan_ui.export_confirmed_grades()
    assert len(exported) == 1
    assert exported[0]['student_name'] == ''
    assert exported[0]['questions'][0]['score'] == 2
    assert exported[0]['questions'][1]['score'] == 3


def test_confirm_grade_rejects_pending_review(tmp_path, monkeypatch):
    import scan_ui
    manager, load, path = manager_fixture(tmp_path)
    monkeypatch.setattr(scan_ui, 'CANDIDATE_MANAGER', manager)
    monkeypatch.setattr(scan_ui, 'REVIEW_ROOT', tmp_path / 'reviews')
    review = load('review-01')[2]
    review['objective'][0].update(auto_status='待复核')
    path.write_text(json.dumps(review, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ValueError, match='完成'):
        scan_ui.confirm_review_grade({'review_id': 'review-01'})

def test_grade_confirmation_http_endpoints(tmp_path, monkeypatch):
    import scan_ui
    manager, load, path = manager_fixture(tmp_path)
    review = load('review-01')[2]
    review['objective'][0].update(auto_status='自动通过', final_status='自动通过', score=2)
    review['items'] = []
    review['score_summary'] = {'total_score': 2, 'possible_score': 2}
    path.write_text(json.dumps(review, ensure_ascii=False), encoding='utf-8')
    other = path.parents[2] / 'review-02/output/review.json'
    other.parent.mkdir(parents=True)
    other_review = dict(review)
    other_review.update(review_id='review-02', student_id='202619240111', grade_confirmed=True)
    other.write_text(json.dumps(other_review, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(scan_ui, 'CANDIDATE_MANAGER', manager)
    monkeypatch.setattr(scan_ui, 'REVIEW_ROOT', tmp_path / 'reviews')
    server = scan_ui.create_server('127.0.0.1', 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:' + str(server.server_address[1])
    try:
        confirm_request = Request(
            base + '/api/review/confirm-grade',
            json.dumps({'review_id': 'review-01'}).encode('utf-8'),
            {'Content-Type': 'application/json'},
        )
        confirmed = json.load(urlopen(confirm_request))
        assert confirmed['ok'] is True and confirmed['grade_confirmed'] is True
        with urlopen(base + '/api/candidates/export.json?review_id=review-01') as response:
            exported = json.load(response)
            assert response.headers['Content-Disposition'].startswith('attachment;')
        assert isinstance(exported, list) and len(exported) == 1
        assert exported[0]['questions'][0]['score'] == 2
        with urlopen(base + '/api/candidates/export.json?review_id=review-01&review_id=review-02') as response:
            selected = json.load(response)
        assert isinstance(selected, list) and len(selected) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_question_score_records_preserve_scout_native_ids():
    from candidate_manager import question_score_records

    records = question_score_records({
        "objective": [{
            "question": "1",
            "id": 78,
            "session_id": 14,
            "section_id": 13,
            "auto_status": "自动通过",
            "score": 2,
        }],
        "items": [],
    })

    assert records[0]["id"] == 78
    assert records[0]["question_id"] == 78
    assert records[0]["session_id"] == 14
    assert records[0]["section_id"] == 13
