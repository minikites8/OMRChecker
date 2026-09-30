"""Answer editor contracts: persistence, isolation, retries and both HTTP servers."""
import copy
import json
import threading
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.request import Request, urlopen

import pytest
from fastapi.testclient import TestClient

import exam_answers as answers
import scan_ui
from backend import app as api
from exam_import import import_exam_and_answers
from exam_regrade import _prepare_review


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_ui, 'IMPORT_ROOT', tmp_path / 'imports')
    monkeypatch.setattr(scan_ui, 'REVIEW_ROOT', tmp_path / 'reviews')
    monkeypatch.setattr(scan_ui, 'PLATFORM_PERSISTENCE', SimpleNamespace(sync_file=Mock()))
    monkeypatch.setattr(scan_ui, 'AI_REVIEW_WORKERS', {})
    monkeypatch.setattr(scan_ui, 'AI_QUESTION_WORKERS', {})
    monkeypatch.setattr(scan_ui, 'ai_is_configured', lambda: True)
    imported = import_exam_and_answers({'sections': [{'name': '测试试卷', 'questions': [
        {'id': 1, 'type': 'single', 'title': '1. 单选', 'description': '选择答案 A / B / C / D', 'answer': 'A', 'score': 2},
        {'id': 31, 'type': 'blank', 'title': '31. 求值', 'description': '输入 6 × 7 的值', 'answer': '42', 'score': 2},
        {'id': 32, 'type': 'essay', 'title': '32–33. 两个空', 'description': '填出两个空', 'question_ids': ['32', '33'], 'answer': {'32': 'x', '33': 'y'}, 'score': 4},
    ]}]})
    imported['name'] = '答案编辑测试'
    path = scan_ui.IMPORT_ROOT / 'paper/normalized_exam.json'
    path.parent.mkdir(parents=True)
    scan_ui._write_json_atomic(path, imported)
    review = {'review_id': 'saved', 'import_id': 'paper', 'answer_paper_type': '', 'owner_user_id': 'teacher',
              'student_id': '100001', 'student_name': '考生甲', 'grade_confirmed': True,
              'objective': [{'question': '1', 'recognized': 'A', 'manual_status': '', 'override_answer': False}],
              'items': [{'question': key, 'recognized_text': value, 'manual_status': '', 'manual_text': '',
                         'confidence': .99, 'handwriting_urls': ['/reviews/saved/output/handwriting/' + key + '.png']}
                        for key, value in [('31', '42'), ('32', 'x'), ('33', 'y')]]}
    review = _prepare_review(review, imported, 'initial')
    for item in review['items']:
        item.update(ai_status='AI通过', ai_score=item['score'], ai_reason='原结果', ai_confidence=.99)
    import exam_review
    review['score_summary'] = exam_review._score_summary(review)
    review['grade_confirmed'] = True
    review_path = scan_ui.REVIEW_ROOT / 'saved/output/review.json'
    review_path.parent.mkdir(parents=True)
    scan_ui._write_review(review_path, review)
    def enqueue(payload):
        _id, target, record = scan_ui._load_review(payload['review_id'])
        record['ai_question_judgment'] = {'status': '处理中', 'question': payload['question']}
        scan_ui._write_review(target, record)
        return record
    starter = Mock(side_effect=enqueue)
    monkeypatch.setattr(scan_ui, 'start_ai_question_review', starter)
    return SimpleNamespace(imported=imported, path=path, review_path=review_path, starter=starter)


def payload(group='1:2', values=None, variant=''):
    view = answers.read_exam_answers('paper', variant)
    result = {key: view[key] for key in ('import_id', 'paper_type', 'revision')}
    result['question_key'] = group
    if values is not None:
        result['answers'] = values
    return result


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_view_contains_content_and_every_answer_item(saved):
    data = answers.read_exam_answers('paper')
    assert data['questions'][1]['description'] == '输入 6 × 7 的值'
    assert data['questions'][2]['answers'] == {'32': 'x', '33': 'y'}
    assert len(data['revision']) == 64
    assert data['name'] == '答案编辑测试'


def test_save_updates_canonical_embedded_and_download_answers(saved):
    old_review = saved.review_path.read_bytes()
    result = answers.save_exam_answers(payload(values={'31': '43'}))
    current = read(saved.path)
    assert result['changed'] is True
    assert current['answer_map']['31'] == '43'
    assert current['exam']['sections'][0]['questions'][1]['answer'] == '43'
    assert read(saved.path.parent / 'answer_map.json')['answer_map']['31'] == '43'
    assert read(saved.path.parent / 'exam_with_answers.json')[0]['questions'][1]['answer'] == '43'
    assert current['exam']['sections'][0]['questions'][0] == saved.imported['exam']['sections'][0]['questions'][0]
    assert saved.review_path.read_bytes() == old_review
    history = read(next((saved.path.parent / 'answer_history').glob('*.json')))
    assert history['previous_answers'] == {'31': '42'}
    assert history['original'] == saved.imported


def test_revision_rejects_stale_edit_and_noop_keeps_revision(saved):
    stale = payload(values={'31': '44'})
    unchanged = answers.save_exam_answers(payload(values={'31': '42'}))
    assert unchanged['changed'] is False and unchanged['revision'] == stale['revision']
    answers.save_exam_answers(payload(values={'31': '43'}))
    with pytest.raises(ValueError, match='版本'):
        answers.save_exam_answers(stale)
    assert read(saved.path)['answer_map']['31'] == '43'


def test_clear_answer_is_missing_and_reopen_keeps_blank(saved):
    data = answers.save_exam_answers(payload(values={'31': ''}))
    assert '31' in data['summary']['answer_missing']
    assert '31' not in read(saved.path)['answer_map']
    assert answers.read_exam_answers('paper')['questions'][1]['answers'] == {'31': ''}


@pytest.mark.parametrize('values', [{'31': []}, {'32': 'bad'}, {'31': 'a' * 2001}])
def test_invalid_values_preserve_import(saved, values):
    before = saved.path.read_bytes()
    with pytest.raises(ValueError):
        answers.save_exam_answers(payload(values=values))
    assert saved.path.read_bytes() == before


@pytest.mark.parametrize('value', ['E', 'AB', '<script>'])
def test_objective_answer_validation(saved, value):
    with pytest.raises(ValueError, match='单选'):
        answers.save_exam_answers(payload('1:1', {'1': value}))


def test_multiblank_edit_and_regrade_group_once(saved):
    answers.save_exam_answers(payload('1:3', {'32': 'new x', '33': 'new y'}))
    before = read(saved.review_path)
    result = answers.regrade_exam_question(payload('1:3'))
    current = read(saved.review_path)
    assert result['ai_processing'] == 1
    saved.starter.assert_called_once_with({'review_id': 'saved', 'question': '32'})
    assert current['items'][0] == before['items'][0]
    assert [q['expected_answer'] for q in current['items'][1:]] == ['new x', 'new y']
    assert current['objective'] == before['objective']


def test_single_question_regrade_preserves_neighbors_manual_identity_and_images(saved):
    record = read(saved.review_path)
    record['items'][0].update(manual_status='部分得分', manual_score=1, manual_text='老师修正', reviewed_by={'id': 'teacher'})
    scan_ui._write_review(saved.review_path, record)
    answers.save_exam_answers(payload(values={'31': '43'}))
    before = read(saved.review_path)
    result = answers.regrade_exam_question(payload())
    current = read(saved.review_path)
    assert result['matched'] == result['ai_processing'] == 1
    assert current['items'][1:] == before['items'][1:]
    assert current['objective'] == before['objective']
    assert current['items'][0]['expected_answer'] == '43'
    for field in ('manual_status', 'manual_text', 'manual_score', 'reviewed_by', 'handwriting_urls'):
        assert current['items'][0][field] == before['items'][0][field]
    for field in ('student_id', 'student_name', 'owner_user_id'):
        assert current[field] == before[field]
    assert current['grade_confirmed'] is False
    assert current['review_activity'][-1]['action'] == 'review.question_regraded'
    assert read(next((saved.review_path.parent / 'regrade_history').glob('*.json'))) == before


def test_objective_uses_deterministic_rescore_only(saved):
    before = read(saved.review_path)
    answers.save_exam_answers(payload('1:1', {'1': 'B'}))
    result = answers.regrade_exam_question(payload('1:1'))
    current = read(saved.review_path)
    assert result['completed'] == 1 and result['mode'] == 'objective'
    saved.starter.assert_not_called()
    assert current['objective'][0]['expected'] == 'B'
    assert current['items'] == before['items']
    assert current['score_summary']['objective_score'] == 0


def test_busy_review_is_skipped_with_original_bytes(saved):
    scan_ui.AI_QUESTION_WORKERS['saved:31'] = Future()
    before = saved.review_path.read_bytes()
    result = answers.regrade_exam_question(payload())
    assert result['skipped_busy'] == ['saved'] and result['matched'] == 1
    assert saved.review_path.read_bytes() == before
    saved.starter.assert_not_called()


def test_missing_ai_configuration_preserves_saved_answer_and_review(saved, monkeypatch):
    answers.save_exam_answers(payload(values={'31': '43'}))
    before = saved.review_path.read_bytes()
    monkeypatch.setattr(scan_ui, 'ai_is_configured', lambda: False)
    with pytest.raises(ValueError, match='配置 AI'):
        answers.regrade_exam_question(payload())
    assert saved.review_path.read_bytes() == before
    assert read(saved.path)['answer_map']['31'] == '43'


def test_variant_edit_and_regrade_isolation(saved):
    imported = copy.deepcopy(saved.imported)
    imported.update(paper_variants={'A': copy.deepcopy(saved.imported), 'B': copy.deepcopy(saved.imported)}, paper_types=['A', 'B'])
    scan_ui._write_json_atomic(saved.path, imported)
    record = read(saved.review_path); record['answer_paper_type'] = 'B'; scan_ui._write_review(saved.review_path, record)
    other = copy.deepcopy(record); other.update(review_id='other', answer_paper_type='A')
    other_path = scan_ui.REVIEW_ROOT / 'other/output/review.json'; other_path.parent.mkdir(parents=True); scan_ui._write_review(other_path, other)
    other_bytes = other_path.read_bytes()
    answers.save_exam_answers(payload(values={'31': 'B-only'}, variant='B'))
    current = read(saved.path)
    assert current['answer_map']['31'] == current['paper_variants']['A']['answer_map']['31'] == '42'
    assert current['paper_variants']['B']['answer_map']['31'] == 'B-only'
    result = answers.regrade_exam_question(payload(variant='B'))
    assert result['matched'] == 1
    assert read(saved.review_path)['items'][0]['expected_answer'] == 'B-only'
    assert other_path.read_bytes() == other_bytes
    with pytest.raises(ValueError, match='卷型'):
        answers.read_exam_answers('paper', 'Z')


def test_storage_failure_restores_original_import(saved):
    original = read(saved.path)
    scan_ui.PLATFORM_PERSISTENCE.sync_file.side_effect = [None, OSError('storage failed')]
    with pytest.raises(OSError, match='storage failed'):
        answers.save_exam_answers(payload(values={'31': '43'}))
    assert read(saved.path) == original


def test_no_saved_reviews_reports_zero(saved):
    saved.review_path.unlink()
    result = answers.regrade_exam_question(payload())
    assert result['matched'] == result['completed'] == result['ai_processing'] == 0


def test_fastapi_get_edit_and_regrade_routes(saved, monkeypatch):
    monkeypatch.setattr(api, 'current_user', lambda request: {'id': 'teacher', 'sub': 'teacher'})
    client = TestClient(api.app, raise_server_exceptions=False)
    data = client.get('/api/exam/answers', params={'import_id': 'paper'})
    assert data.status_code == 200 and data.json()['questions']
    result = client.post('/api/exam/answers', json=payload(values={'31': 'new'}))
    assert result.status_code == 200, result.text
    assert read(saved.path)['answers_updated_by'] == 'teacher'
    result = client.post('/api/exam/regrade-question', json=payload())
    assert result.status_code == 200 and result.json()['ai_processing'] == 1
    assert client.get('/api/exam/answers?import_id=../x').status_code == 400
    stale = payload(); stale['revision'] = 'stale'
    assert client.post('/api/exam/regrade-question', json=stale).status_code == 400


def test_legacy_get_edit_and_regrade_routes(saved, monkeypatch):
    server = scan_ui.create_server('127.0.0.1', 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(base + '/api/exam/answers?import_id=paper') as response:
            assert json.load(response)['questions'][1]['answers'] == {'31': '42'}
        for route, body in [('/api/exam/answers', payload(values={'31': '43'})), ('/api/exam/regrade-question', None)]:
            data = body or payload()
            req = Request(base + route, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
            with urlopen(req) as response:
                assert json.load(response)['ok'] is True
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_incomplete_reference_answer_blocks_regrade(saved):
    answers.save_exam_answers(payload(values={'31': ''}))
    before = saved.review_path.read_bytes()
    with pytest.raises(ValueError, match='补全'):
        answers.regrade_exam_question(payload())
    saved.starter.assert_not_called()
    assert saved.review_path.read_bytes() == before


def test_actual_single_question_ai_path_receives_new_reference(saved, monkeypatch):
    import exam_review
    answers.save_exam_answers(payload(values={'31': '43'}))
    before = read(saved.review_path)
    captured = []
    def judge(items):
        captured.extend(copy.deepcopy(items))
        return {'status': '已完成', 'enabled': True, 'processed': len(items), 'results': {
            str(item['question']): {'status': 'AI不通过', 'confidence': .99, 'score': 0,
            'reason': '已按新答案判定', 'corrected_answer': item['expected_answer'], 'visual_text': item['recognized_text']}
            for item in items}}
    monkeypatch.setattr(exam_review, 'judge_handwritten_items', judge)
    monkeypatch.setattr(scan_ui, 'start_ai_question_review', scan_ui.run_ai_question_review)
    result = answers.regrade_exam_question(payload())
    current = read(saved.review_path)
    assert result['completed'] == 1 and result['ai_processing'] == 0
    assert [(q['question'], q['expected_answer']) for q in captured] == [('31', '43')]
    assert current['items'][0]['ai_status'] == 'AI不通过'
    assert current['items'][1:] == before['items'][1:]
    assert current['score_summary']['total_score'] == before['score_summary']['total_score'] - 2
