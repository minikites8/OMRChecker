"""One-sheet regrade, explicit paper variants, isolation and API contracts."""
import copy
import json
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import exam_review
import scan_ui
from backend import app as api
from exam_import import import_exam_and_answers
from exam_regrade import _prepare_review, regrade_exam


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


@pytest.fixture
def papers(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_ui, 'IMPORT_ROOT', tmp_path / 'imports')
    monkeypatch.setattr(scan_ui, 'REVIEW_ROOT', tmp_path / 'reviews')
    monkeypatch.setattr(scan_ui, 'PLATFORM_PERSISTENCE', SimpleNamespace(sync_file=Mock()))
    monkeypatch.setattr(scan_ui, 'AI_REVIEW_WORKERS', {})
    monkeypatch.setattr(scan_ui, 'AI_QUESTION_WORKERS', {})
    monkeypatch.setattr(scan_ui, 'ai_is_configured', lambda: False)
    def variant(kind, expected, points):
        return import_exam_and_answers({'sections': [{'name': kind + '卷', 'questions': [
            {'id': 1, 'title': '1. ' + kind + '卷选择题', 'description': kind + '卷客观题题干', 'type': 'single', 'answer': kind, 'score': points},
            {'id': 31, 'title': '31. ' + kind + '卷填空题', 'description': kind + '卷主观题题干', 'type': 'blank', 'answer': expected, 'score': points + 1}]}]})
    a, b = variant('A', '10', 2), variant('B', '20', 4)
    imported = {**copy.deepcopy(a), 'paper_variants': {'A': a, 'B': b}, 'paper_types': ['A', 'B'], 'name': '双卷考试'}
    import_path = scan_ui.IMPORT_ROOT / 'paper/normalized_exam.json'; scan_ui._write_json_atomic(import_path, imported)
    base = {'ok': True, 'review_id': 'selected', 'import_id': 'paper', 'paper_type': 'A', 'answer_paper_type': 'A',
            'student_name': '考生甲', 'student_id': '000001', 'owner_user_id': 'teacher', 'card_files': ['original.pdf'],
            'objective': [{'question': '1', 'recognized': 'A', 'manual_status': '', 'override_answer': False}],
            'items': [{'question': '31', 'recognized_text': '10', 'confidence': .99, 'manual_status': '', 'manual_text': '',
                       'handwriting_urls': ['/reviews/selected/output/handwriting/31.png']}]}
    base = _prepare_review(base, imported, 'initial')
    base['items'][0].update(ai_status='AI通过', ai_score=3, ai_reason='original judgment')
    base['score_summary'] = exam_review._score_summary(base)
    base['grade_confirmed'] = True; base['grade_confirmed_at'] = 'original'
    selected = scan_ui.REVIEW_ROOT / 'selected/output/review.json'; scan_ui._write_review(selected, base)
    neighbor = copy.deepcopy(base); neighbor.update(review_id='neighbor', student_name='考生乙', student_id='000002')
    other = scan_ui.REVIEW_ROOT / 'neighbor/output/review.json'; scan_ui._write_review(other, neighbor)
    def start(payload):
        return scan_ui._load_review(payload['review_id'])[2]
    starter = Mock(side_effect=start); monkeypatch.setattr(scan_ui, 'start_ai_review', starter)
    return SimpleNamespace(imported=imported, import_path=import_path, selected=selected, neighbor=other, starter=starter)


def payload(paper_type='B'):
    return {'import_id': 'paper', 'review_ids': ['selected'], 'paper_type': paper_type, '_actor_user_id': 'teacher'}


def test_selected_variant_drives_question_answers_scores_and_only_one_sheet(papers):
    neighbor = papers.neighbor.read_bytes(); imported = papers.import_path.read_bytes(); before = read(papers.selected)
    result = regrade_exam(payload())
    current = read(papers.selected)
    assert result['total'] == result['completed'] == 1 and result['failed'] == 0
    assert [row['review_id'] for row in result['reviews']] == ['selected']
    papers.starter.assert_called_once_with({'review_id': 'selected'})
    assert current['paper_type'] == current['answer_paper_type'] == 'B'
    assert current['paper_type_status'] == '人工确认'
    assert current['objective'][0]['expected'] == 'B'
    assert current['objective'][0]['score'] == 4
    assert current['items'][0]['expected_answer'] == '20'
    assert 'B卷主观题题干' in current['items'][0]['source_content']
    assert current['items'][0]['score'] == 5
    assert current['score_summary']['possible_score'] == 9
    assert current['grade_confirmed'] is False
    assert papers.neighbor.read_bytes() == neighbor
    assert papers.import_path.read_bytes() == imported
    history = next((papers.selected.parent / 'regrade_history').glob('*.json'))
    assert read(history) == before
    assert current['regrade']['paper_type'] == 'B'


def test_saved_user_choice_overrides_cached_answer_variant(papers):
    record = read(papers.selected); record.update(paper_type='B', paper_type_status='人工确认', answer_paper_type='A')
    scan_ui._write_review(papers.selected, record)
    regrade_exam({'import_id': 'paper', 'review_ids': ['selected']})
    current = read(papers.selected)
    assert current['answer_paper_type'] == 'B'
    assert current['items'][0]['expected_answer'] == '20'


def test_whole_exam_regrade_preserves_each_sheets_selected_variant(papers):
    record = read(papers.selected); record['paper_type'] = 'B'; scan_ui._write_review(papers.selected, record)
    result = regrade_exam({'import_id': 'paper'})
    assert result['completed'] == 2
    assert read(papers.selected)['items'][0]['expected_answer'] == '20'
    assert read(papers.neighbor)['items'][0]['expected_answer'] == '10'
    assert read(papers.neighbor)['answer_paper_type'] == 'A'


def test_manual_decisions_identity_and_images_survive_variant_change(papers):
    before = read(papers.selected); before['items'][0].update(manual_status='部分得分', manual_score=1, manual_text='老师修正', reviewed_by={'id': 'teacher'})
    before['objective'][0].update(manual_status='通过', override_answer=True, reviewed_answer='B')
    scan_ui._write_review(papers.selected, before)
    regrade_exam(payload())
    after = read(papers.selected)
    for key in ['student_name', 'student_id', 'owner_user_id', 'card_files']:
        assert after[key] == before[key]
    for key in ['manual_status', 'manual_score', 'manual_text', 'reviewed_by', 'handwriting_urls']:
        assert after['items'][0][key] == before['items'][0][key]
    assert after['items'][0]['awarded_score'] == 1
    assert after['objective'][0]['manual_status'] == '通过'


@pytest.mark.parametrize('kind', ['C', 'D', '', None, ['B'], {'type': 'B'}])
def test_invalid_or_unavailable_variant_has_no_saved_side_effects(papers, kind):
    original = papers.selected.read_bytes()
    with pytest.raises(ValueError): regrade_exam(payload(kind))
    assert papers.selected.read_bytes() == original
    assert not (papers.selected.parent / 'regrade_history').exists()
    papers.starter.assert_not_called()


@pytest.mark.parametrize('ids', [None, [], ['selected', 'neighbor']])
def test_explicit_variant_is_restricted_to_a_single_selected_sheet(papers, ids):
    body = payload()
    if ids is None: body.pop('review_ids')
    else: body['review_ids'] = ids
    before = (papers.selected.read_bytes(), papers.neighbor.read_bytes())
    with pytest.raises(ValueError): regrade_exam(body)
    assert (papers.selected.read_bytes(), papers.neighbor.read_bytes()) == before


def test_busy_sheet_retains_original_variant_and_results(papers):
    scan_ui.AI_REVIEW_WORKERS['selected'] = Future()
    before = papers.selected.read_bytes()
    result = regrade_exam(payload())
    assert result['skipped_busy'] == ['selected'] and result['completed'] == 0
    assert papers.selected.read_bytes() == before
    papers.starter.assert_not_called()


def test_switch_back_to_a_restores_a_references_and_scores(papers):
    regrade_exam(payload('B')); regrade_exam(payload('A'))
    current = read(papers.selected)
    assert current['paper_type'] == current['answer_paper_type'] == 'A'
    assert current['objective'][0]['expected'] == 'A'
    assert current['items'][0]['expected_answer'] == '10'
    assert current['score_summary']['possible_score'] == 5
    assert len(list((papers.selected.parent / 'regrade_history').glob('*.json'))) == 2


def test_api_accepts_selected_variant_for_exactly_one_sheet(papers, monkeypatch):
    monkeypatch.setattr(api, 'current_user', lambda request: {'id': 'teacher', 'sub': 'teacher'})
    client = TestClient(api.app, raise_server_exceptions=False)
    before = papers.neighbor.read_bytes()
    response = client.post('/api/exam/regrade', json=payload('B'))
    assert response.status_code == 200, response.text
    assert response.json()['reviews'][0]['answer_paper_type'] == 'B'
    assert papers.neighbor.read_bytes() == before
    response = client.post('/api/exam/regrade', json=payload('C'))
    assert response.status_code == 400


def test_ai_worker_receives_selected_variant_references(papers, monkeypatch):
    seen = []
    def start(body):
        record = scan_ui._load_review(body['review_id'])[2]
        seen.append((record['answer_paper_type'], record['objective'][0]['expected'], record['items'][0]['expected_answer'], record['items'][0]['score']))
        return record
    monkeypatch.setattr(scan_ui, 'start_ai_review', start)
    regrade_exam(payload())
    assert seen == [('B', 'B', '20', 5)]
