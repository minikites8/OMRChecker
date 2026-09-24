"""AI-only routing contracts. AI responses are simulated; image/request paths are real."""
import base64
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen

import cv2
import numpy as np
import pytest

import ai_judge
import exam_review
import scan_ui
import src.ocr as ocr
from recognition_config import recognition_settings, resolve_local_ocr_enabled


@pytest.fixture(autouse=True)
def recognition_environment(monkeypatch):
    monkeypatch.delenv('OMR_LOCAL_OCR_ENABLED', raising=False)
    monkeypatch.delenv('HANDWRITING_AI_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setattr(exam_review, '_OCR_THREAD_LOCAL', threading.local())


def test_default_and_explicit_mode_override_environment(monkeypatch):
    assert recognition_settings() == {'local_ocr_enabled': True, 'recognition_mode': 'local_ocr_ai'}
    monkeypatch.setenv('OMR_LOCAL_OCR_ENABLED', '0')
    assert recognition_settings()['recognition_mode'] == 'ai_only'
    assert resolve_local_ocr_enabled(True) is True
    assert resolve_local_ocr_enabled('false') is False
    with pytest.raises(ValueError):
        resolve_local_ocr_enabled('invalid')


def test_factory_routes_ai_without_loading_paddle(monkeypatch):
    monkeypatch.setenv('OMR_LOCAL_OCR_ENABLED', 'false')
    monkeypatch.setattr(ocr, 'PaddleTextRecognizer', lambda *a: pytest.fail('local OCR loaded'))
    assert isinstance(ocr.create_text_recognizer({}), ocr.AITextRecognizer)


def test_per_task_mode_overrides_environment_and_keeps_separate_caches(monkeypatch):
    created = []
    def create(params):
        created.append(params.local_ocr_enabled)
        return SimpleNamespace(recognize=lambda images, labels: [
            SimpleNamespace(text=str(params.local_ocr_enabled), confidence=1, error=None)
            for label in labels])
    monkeypatch.setattr(ocr, 'create_text_recognizer', create)
    monkeypatch.setenv('OMR_LOCAL_OCR_ENABLED', '0')
    image = np.full((12, 30), 255, np.uint8)
    assert exam_review._recognize_crops([image], ['31'], True)[0].text == 'True'
    assert exam_review._recognize_crops([image], ['32'], False)[0].text == 'False'
    assert exam_review._recognize_crops([image], ['33'], True)[0].text == 'True'
    assert created == [True, False]
    assert recognition_settings()['recognition_mode'] == 'ai_only'


def response(rows):
    return {'choices': [{'message': {'content': json.dumps({'results': rows})}}]}


def test_ai_transcription_batches_and_restores_input_order(monkeypatch):
    monkeypatch.setenv('HANDWRITING_AI_API_KEY', 'test-key')
    batches, images = [], []
    def request(config, items):
        assert config['text_recognition_only'] is True
        batches.append(len(items))
        images.extend(Path(item['handwriting_images'][0]) for item in items)
        assert all(path.is_file() for path in images)
        return response([{'question': item['question'], 'visual_text': item['question'],
                          'confidence': .98, 'image_status': 'clear'} for item in reversed(items)])
    monkeypatch.setattr(ai_judge, '_request', request)
    labels = [str(i) for i in range(9)]
    results = ai_judge.recognize_handwriting_crops([np.zeros((20, 50), np.uint8)]*9, labels)
    assert sorted(batches) == [2, 7]
    assert [item.text for item in results] == [str(i)+':'+str(i) for i in range(9)]
    assert all(item.error is None for item in results)
    assert all(not path.exists() for path in images)


def test_ai_transport_sends_only_images_and_field_ids(monkeypatch):
    monkeypatch.setenv('HANDWRITING_AI_API_KEY', 'test-key')
    calls = []
    class Reply:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps(response([{'question': '0:姓名', 'visual_text': '张三',
                                                   'confidence': .97, 'image_status': 'clear'}])).encode()
    def transport(request, timeout):
        calls.append(json.loads(request.data))
        return Reply()
    monkeypatch.setattr(ai_judge, 'urlopen', transport)
    result = ai_judge.recognize_handwriting_crops([np.zeros((20, 60), np.uint8)], ['姓名'])
    assert result[0].text == '张三'
    body = calls[0]
    assert body['messages'][0]['content'] == ai_judge.TEXT_RECOGNITION_PROMPT
    content = body['messages'][1]['content']
    assert any(item['type'] == 'image_url' for item in content)
    assert json.loads(content[-1]['text']) == {'items': [{'question': '0:姓名'}]}
    assert 'expected_answer' not in str(body)


@pytest.mark.parametrize('row', [None, {'confidence': 'NaN'}, {'confidence': 'oops'}])
def test_invalid_ai_transcription_produces_explicit_error(monkeypatch, row):
    monkeypatch.setenv('HANDWRITING_AI_API_KEY', 'test-key')
    rows = [] if row is None else [{'question': '0:31', 'visual_text': '42', 'image_status': 'clear', **row}]
    monkeypatch.setattr(ai_judge, '_request', lambda *a: response(rows))
    result = ai_judge.recognize_handwriting_crops([np.zeros((20, 30), np.uint8)], ['31'])[0]
    assert result.text == '' and result.confidence == 0 and result.error


def test_ai_failure_keeps_local_ocr_disabled(monkeypatch):
    monkeypatch.setenv('HANDWRITING_AI_API_KEY', 'test-key')
    monkeypatch.setattr(ocr, 'PaddleTextRecognizer', lambda *a: pytest.fail('local OCR loaded'))
    def fail(*args): raise RuntimeError('simulated AI timeout')
    monkeypatch.setattr(ai_judge, '_request', fail)
    result = exam_review._recognize_crops([np.zeros((20, 30), np.uint8)], ['31'], False)[0]
    assert result.text == '' and 'simulated AI timeout' in result.error


def test_ai_recognized_text_is_separate_from_ocr_reference():
    payload = ai_judge._prompt_items([{'question': '31', 'recognized_text': '42',
                                     'confidence': .98, 'recognition_source': 'ai'}])
    assert payload[0]['ocr_reference'] == {}


def test_ai_extraction_preserves_images_and_recognizes_name(monkeypatch, tmp_path):
    from src.tests.test_candidates import page_with_name
    pages = [page_with_name(), np.full((1684, 1191), 255, np.uint8)]
    monkeypatch.setattr(exam_review, '_load_page', lambda path: list(zip(['front', 'back'], pages)))
    monkeypatch.setattr(exam_review, '_align_and_order_pages', lambda images: (images, [1, 1]))
    monkeypatch.setattr(exam_review, '_reference_pages', lambda: [])
    monkeypatch.setattr(exam_review, '_handwriting_ratio', lambda *args: .03)
    captured = []
    def recognize(crops, labels, local_ocr_enabled=None):
        assert local_ocr_enabled is False
        captured.extend(labels)
        return [SimpleNamespace(text='张三' if label == '姓名' else '42', confidence=.98) for label in labels]
    monkeypatch.setattr(exam_review, '_recognize_crops', recognize)
    card = exam_review.extract_answer_card(['front.png'], tmp_path, {'local_ocr_enabled': False})
    assert '姓名' in captured and '64代码' in captured
    assert card['local_ocr_enabled'] is False and card['recognition_mode'] == 'ai_only'
    assert card['student_name'] == '张三' and card['student_name_source'] == 'ai'
    assert card['text_fields']['31']['text'] == '42'
    assert all(Path(path).is_file() for path in card['crop_paths'].values())
    review = exam_review._build_review_from_maps({}, {'31': '42'}, {}, [], card=card)
    assert review['recognition_mode'] == 'ai_only'
    assert review['items'][0]['recognition_source'] == 'ai'


def setup_review(monkeypatch, tmp_path):
    monkeypatch.setattr(scan_ui, 'IMPORT_ROOT', tmp_path/'imports')
    monkeypatch.setattr(scan_ui, 'REVIEW_ROOT', tmp_path/'reviews')
    monkeypatch.setattr(scan_ui, 'ai_is_configured', lambda: True)
    captured = []
    def build(exam, answers, cards, image_dir=None, template_config=None):
        captured.append(template_config['local_ocr_enabled'])
        return {'ok': True, 'items': [], 'objective': [], 'source': {}, 'review_summary': {}}
    monkeypatch.setattr(scan_ui, 'build_review_from_structured', build)
    monkeypatch.setattr(scan_ui, 'start_ai_review', lambda payload: scan_ui._load_review(payload['review_id'])[2])
    imported = scan_ui.run_exam_import({'exam_text': json.dumps([
        {'name': '填空', 'questions': [{'type': 'fill', 'title': '31. Item', 'score': 2, 'answer': '42'}]}])})
    photo = {'name': 'card.png', 'data': base64.b64encode(b'image').decode()}
    return {'import_id': imported['import_id'], 'card_files': [photo], 'local_ocr_enabled': False}, captured, photo


def test_single_review_passes_and_persists_ai_mode(monkeypatch, tmp_path):
    payload, captured, _ = setup_review(monkeypatch, tmp_path)
    report = scan_ui.run_review_job(payload)
    assert captured == [False]
    assert report['local_ocr_enabled'] is False and report['recognition_mode'] == 'ai_only'
    assert scan_ui._load_review(report['review_id'])[2]['local_ocr_enabled'] is False


def test_batch_reviews_share_captured_ai_mode(monkeypatch, tmp_path):
    payload, captured, photo = setup_review(monkeypatch, tmp_path)
    payload['card_groups'] = [{'label': str(i), 'files': [photo]} for i in range(2)]
    batch = scan_ui.start_batch_review(payload)
    worker = scan_ui.BATCH_REVIEW_WORKERS.get(batch['batch_id'])
    if worker: worker.join(10)
    saved = json.loads(scan_ui._batch_path(batch['batch_id'])[1].read_text(encoding='utf-8'))
    assert saved['recognition_mode'] == 'ai_only'
    assert captured == [False, False]
    assert all(entry.get('review_id') for entry in saved['reviews'])


def test_missing_key_preflight_leaves_job_directories_untouched(monkeypatch, tmp_path):
    monkeypatch.setattr(scan_ui, 'ai_is_configured', lambda: False)
    monkeypatch.setattr(scan_ui, 'JOBS_ROOT', tmp_path/'jobs')
    monkeypatch.setattr(scan_ui, 'REVIEW_ROOT', tmp_path/'reviews')
    with pytest.raises(ValueError, match='HANDWRITING_AI_API_KEY'):
        scan_ui.run_scan_job(local_ocr_enabled=False)
    with pytest.raises(ValueError, match='HANDWRITING_AI_API_KEY'):
        scan_ui.start_batch_review({'local_ocr_enabled': False})
    with pytest.raises(ValueError, match='HANDWRITING_AI_API_KEY'):
        scan_ui._create_review_report('', None, {}, [], local_ocr_enabled=False)
    assert list(tmp_path.iterdir()) == []


def test_scanner_subprocess_receives_ai_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(scan_ui, 'ai_is_configured', lambda: True)
    monkeypatch.setattr(scan_ui, 'JOBS_ROOT', tmp_path)
    monkeypatch.setattr(scan_ui, 'copy_template_assets', lambda *args: None)
    monkeypatch.setattr(scan_ui, 'save_uploads', lambda *args: [Path('card.png')])
    monkeypatch.setattr(scan_ui, 'latest_result_csv', lambda path: path/'result.csv')
    monkeypatch.setattr(scan_ui, 'read_results', lambda *args: ([], []))
    environments = []
    def run(*args, **kwargs):
        environments.append(kwargs['env'])
        return SimpleNamespace(returncode=0, stdout='done')
    monkeypatch.setattr(scan_ui.subprocess, 'run', run)
    report = scan_ui.run_scan_job(local_ocr_enabled=False)
    assert environments[0]['OMR_LOCAL_OCR_ENABLED'] == '0'
    assert report['recognition_mode'] == 'ai_only'


def test_candidate_name_uses_saved_review_mode(monkeypatch, tmp_path):
    from src.tests.test_candidates import manager_fixture
    manager, _load, path = manager_fixture(tmp_path)
    manager.recognizer = None
    review = json.loads(path.read_text(encoding='utf-8')); review['local_ocr_enabled'] = False
    path.write_text(json.dumps(review), encoding='utf-8')
    captured = []
    def recognize(images, labels, local_ocr_enabled=None):
        captured.append(local_ocr_enabled)
        return [SimpleNamespace(text='李四', confidence=.99)]
    monkeypatch.setattr(exam_review, '_recognize_crops', recognize)
    manager.start({'review_ids': ['review-01']}); manager.future.result(timeout=10)
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert captured == [False]
    assert saved['student_name'] == '李四' and saved['student_name_source'] == 'ai'


def test_health_exposes_default_mode(monkeypatch):
    monkeypatch.setenv('OMR_LOCAL_OCR_ENABLED', '0')
    server = scan_ui.create_server('127.0.0.1', 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with urlopen('http://127.0.0.1:'+str(server.server_address[1])+'/api/health') as reply:
            assert json.load(reply)['recognition'] == {'local_ocr_enabled': False, 'recognition_mode': 'ai_only'}
    finally:
        server.shutdown(); server.server_close(); thread.join(3)

@pytest.mark.parametrize('route', ['/api/scan', '/api/demo'])
def test_fastapi_scanner_forwards_mode(monkeypatch, route):
    from backend import app as api
    from fastapi.testclient import TestClient
    monkeypatch.setattr(api, 'current_user', lambda request: {'id': 'test-user'})
    modes = []
    def run(*args, **kwargs):
        modes.append(kwargs['local_ocr_enabled'])
        return {'ok': True, 'recognition_mode': 'ai_only'}
    monkeypatch.setattr(api.legacy, 'run_scan_job', run)
    reply = TestClient(api.app).post(route, json={'files': [], 'local_ocr_enabled': False})
    assert reply.status_code == 200 and modes == [False]


def test_fastapi_health_exposes_default_mode(monkeypatch):
    from backend import app as api
    from fastapi.testclient import TestClient
    monkeypatch.setenv('OMR_LOCAL_OCR_ENABLED', '0')
    result = TestClient(api.app).get('/api/health')
    assert result.status_code == 200
    assert result.json()['recognition']['recognition_mode'] == 'ai_only'
