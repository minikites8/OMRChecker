"""All fixtures and cloud adapters are isolated from user data."""
import json
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import pytest
import review_deletion as deletion
from candidate_manager import CandidateManager
from platform_cos import TencentCosStorage
from platform_database import PostgresStore
from platform_persistence import PlatformPersistence


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def records(tmp_path):
    root = tmp_path / 'reviews'
    for identifier in ('review-01', 'review-02'):
        write_json(root/identifier/'output/review.json', {
            'ok': True, 'review_id': identifier, 'owner_user_id': 'teacher',
            'student_id': '202619240110', 'student_name': '张三', 'items': [],
            'objective': [], 'score_summary': {'total_score': 80}, 'import_id': 'exam-01'})
        image = root/identifier/'output/handwriting/identity/name.png'
        image.parent.mkdir(parents=True); image.write_bytes(b'synthetic image')
    write_json(root/'latest.json', {'review_id': 'review-01'})
    write_json(root/'batches/batch-01/batch.json', {
        'batch_id': 'batch-01', 'total': 2, 'completed': 1, 'failed': 1,
        'reviews': [{'review_id': 'review-01', 'student_name': '张三', 'status': '已完成'},
                    {'review_id': 'review-02', 'status': '失败'}]})
    write_json(tmp_path/'imports/exam-01/exam.json', {'title': '保留试卷'})
    return root


def snapshot(root):
    return {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_remove_identity_scans_indexes_preserves_other_submission_and_exam(records):
    other = (records/'review-02/output/review.json').read_bytes()
    result = deletion.delete_record(records, 'review-01')
    assert result == {'ok': True, 'review_id': 'review-01', 'deleted': True, 'candidate_deleted': True}
    assert not (records/'review-01').exists()
    assert (records/'review-02/output/review.json').read_bytes() == other
    assert (records.parent/'imports/exam-01/exam.json').is_file()
    assert json.loads((records/'latest.json').read_text(encoding='utf-8')) == {'review_id': 'review-02'}
    batch = json.loads((records/'batches/batch-01/batch.json').read_text(encoding='utf-8'))
    assert batch['reviews'] == [{'review_id': 'review-02', 'status': '失败'}]
    assert (batch['total'], batch['completed'], batch['failed']) == (1, 0, 1)
    assert list((records.parent/'.reviews-deleted').iterdir()) == []


def test_last_record_removes_latest_pointer_and_empties_batch(records):
    deletion.delete_record(records, 'review-01'); deletion.delete_record(records, 'review-02')
    assert not (records/'latest.json').exists()
    batch = json.loads((records/'batches/batch-01/batch.json').read_text(encoding='utf-8'))
    assert batch['reviews'] == [] and batch['total'] == 0


@pytest.mark.parametrize('identifier', [None, 7, '', '../review-01', '/review-01', 'review-01/', 'a\\b', 'a.json', 'x'*129])
def test_strict_identifiers(records, identifier):
    with pytest.raises(deletion.ReviewDeletionError) as error: deletion.delete_record(records, identifier)
    assert error.value.status == 400 and (records/'review-01').exists()


@pytest.mark.parametrize('owner,actor,status', [
    ('teacher', None, 401), ('teacher', {'id': 'other', 'role': 'teacher'}, 403),
    ('', {'id': 'teacher', 'role': 'teacher'}, 403),
    ('teacher', {'sub': 'teacher', 'role': 'teacher'}, 200),
    ('', {'id': 'admin', 'role': 'admin'}, 200)])
def test_owner_and_admin_enforcement(records, owner, actor, status):
    path = records/'review-01/output/review.json'
    review = json.loads(path.read_text(encoding='utf-8')); review['owner_user_id'] = owner; write_json(path, review)
    if status == 200:
        assert deletion.delete_record(records, 'review-01', actor=actor, enforce_ownership=True)['deleted']
    else:
        with pytest.raises(deletion.ReviewDeletionError) as error:
            deletion.delete_record(records, 'review-01', actor=actor, enforce_ownership=True)
        assert error.value.status == status and path.is_file()


def test_missing_record_is_404(records):
    with pytest.raises(deletion.ReviewDeletionError) as error: deletion.delete_record(records, 'missing')
    assert error.value.status == 404


def test_link_inside_record_preserves_external_file(records):
    external = records.parent/'external.txt'; external.write_text('preserve', encoding='utf-8')
    try: (records/'review-01/linked.txt').symlink_to(external)
    except OSError as error: pytest.skip('Symlink creation unavailable: '+str(error))
    with pytest.raises(deletion.ReviewDeletionError) as error: deletion.delete_record(records, 'review-01')
    assert error.value.status == 400 and external.read_text(encoding='utf-8') == 'preserve'


def test_busy_record_keeps_everything(records):
    with pytest.raises(deletion.ReviewDeletionError) as error:
        deletion.delete_record(records, 'review-01', is_busy=lambda review, ids: ids == ['batch-01'])
    assert error.value.status == 409 and (records/'review-01').exists()


def test_cloud_failure_keeps_local_record_and_indexes(records):
    before = snapshot(records)
    def fail(_): raise RuntimeError('cloud failed')
    with pytest.raises(RuntimeError, match='cloud failed'):
        deletion.delete_record(records, 'review-01', persistence=SimpleNamespace(delete_review=fail))
    assert before == snapshot(records)


def test_index_write_failure_rolls_back_record_and_all_indexes(records, monkeypatch):
    original = deletion._atomic_write
    def fail_latest(path, content):
        if path.name == 'latest.json': raise OSError('simulated index failure')
        return original(path, content)
    before = snapshot(records); monkeypatch.setattr(deletion, '_atomic_write', fail_latest)
    with pytest.raises(OSError, match='simulated index failure'): deletion.delete_record(records, 'review-01')
    assert before == snapshot(records)
    assert list((records.parent/'.reviews-deleted').iterdir()) == []


def test_cleanup_failure_hides_record_and_retry_enforces_owner(records, monkeypatch):
    original = deletion.shutil.rmtree
    def fail(_): raise OSError('locked file')
    monkeypatch.setattr(deletion.shutil, 'rmtree', fail)
    with pytest.raises(deletion.ReviewDeletionError) as error: deletion.delete_record(records, 'review-01')
    assert error.value.status == 503 and error.value.deleted and not (records/'review-01').exists()
    with pytest.raises(deletion.ReviewDeletionError) as denied:
        deletion.delete_record(records, 'review-01', actor={'id': 'other'}, enforce_ownership=True)
    assert denied.value.status == 403
    monkeypatch.setattr(deletion.shutil, 'rmtree', original)
    assert deletion.delete_record(records, 'review-01', actor={'id': 'teacher'}, enforce_ownership=True)['deleted']
    assert list((records.parent/'.reviews-deleted').iterdir()) == []


@pytest.fixture
def service(records, monkeypatch):
    import scan_ui
    monkeypatch.setattr(scan_ui, 'REVIEW_ROOT', records)
    monkeypatch.setattr(scan_ui, 'AI_REVIEW_WORKERS', {})
    monkeypatch.setattr(scan_ui, 'BATCH_REVIEW_WORKERS', {})
    monkeypatch.setattr(scan_ui, 'PLATFORM_PERSISTENCE', SimpleNamespace(delete_review=lambda _: None, sync_file=lambda *a: None))
    manager = CandidateManager(lambda: records, scan_ui._load_review, scan_ui._write_review, scan_ui.AI_REVIEW_LOCK, lambda _: None)
    monkeypatch.setattr(scan_ui, 'CANDIDATE_MANAGER', manager)
    yield scan_ui
    manager.executor.shutdown(wait=True)


@pytest.mark.parametrize('busy', ['ai', 'name', 'batch'])
def test_service_rejects_active_workers(service, busy):
    if busy == 'ai': service.AI_REVIEW_WORKERS['review-01'] = Future()
    if busy == 'name': service.CANDIDATE_MANAGER.pending_review_ids.add('review-01')
    if busy == 'batch': service.BATCH_REVIEW_WORKERS['batch-01'] = SimpleNamespace(is_alive=lambda: True)
    with pytest.raises(deletion.ReviewDeletionError) as error: service.delete_review_job({'review_id': 'review-01'}, enforce_ownership=False)
    assert error.value.status == 409


def test_queued_name_jobs_are_tracked_until_worker_finishes(service, monkeypatch):
    manager = service.CANDIDATE_MANAGER; entered, release = threading.Event(), threading.Event()
    def block(_):
        entered.set(); assert release.wait(5)
        raise ValueError('synthetic preview failure')
    monkeypatch.setattr(manager, 'preview_builder', block)
    try:
        manager.start({'review_ids': ['review-01', 'review-02']}); assert entered.wait(3)
        assert manager.is_recognizing('review-01') and manager.is_recognizing('review-02')
        with pytest.raises(deletion.ReviewDeletionError) as error: service.delete_review_job({'review_id': 'review-02'}, enforce_ownership=False)
        assert error.value.status == 409
    finally:
        release.set(); manager.future.result(timeout=5)
    assert manager.pending_review_ids == set()


def test_deleted_record_disappears_from_candidate_listing(service):
    assert len(service.CANDIDATE_MANAGER.list()['candidates']) == 2
    service.delete_review_job({'review_id': 'review-01'}, enforce_ownership=False)
    assert [item['review_id'] for item in service.CANDIDATE_MANAGER.list()['candidates']] == ['review-02']


def test_batch_refresh_discards_predelete_snapshot(service, monkeypatch):
    def read(identifier):
        if identifier == 'review-01': service.delete_review_job({'review_id': identifier}, enforce_ownership=False)
        raise ValueError('record unavailable')
    monkeypatch.setattr(service, 'read_review_status', read)
    assert [entry['review_id'] for entry in service.read_batch_status('batch-01')['reviews']] == ['review-02']


@pytest.fixture(params=['legacy', 'fastapi'])
def http_client(request, service, monkeypatch):
    from backend import app as api
    users = {'teacher': {'id': 'teacher', 'role': 'teacher'}, 'other': {'id': 'other', 'role': 'teacher'}, 'admin': {'id': 'admin', 'role': 'admin'}}
    auth = SimpleNamespace(enabled=True, user_from_headers=lambda cookie: users.get(cookie))
    monkeypatch.setattr(service, 'AUTH_SERVICE', auth); monkeypatch.setattr(api, 'auth', auth)
    server = transport = thread = None
    if request.param == 'legacy':
        server = service.create_server('127.0.0.1', 0); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    else:
        from fastapi.testclient import TestClient
        transport = TestClient(api.app)
    def call(path, payload=None, actor='teacher', method='POST'):
        headers = {'Content-Type': 'application/json', 'Cookie': actor}
        content = json.dumps(payload).encode() if payload is not None else None
        if transport:
            response = transport.request(method, path, headers=headers, content=content)
            return response.status_code, response.content
        req = Request('http://127.0.0.1:'+str(server.server_port)+path, data=content, headers=headers, method=method)
        try: response = urlopen(req, timeout=5)
        except HTTPError as error: response = error
        with response: return response.status, response.read()
    yield call
    if transport: transport.close()
    if server: server.shutdown(); server.server_close(); thread.join(timeout=5)


@pytest.mark.parametrize('route', ['/api/review/delete', '/api/candidates/delete'])
def test_http_delete_and_asset_404(http_client, route):
    status, raw = http_client(route, {'review_id': 'review-01'})
    assert status == 200 and json.loads(raw)['candidate_deleted']
    assert http_client('/reviews/review-01/output/handwriting/identity/name.png', method='GET')[0] == 404
    status, raw = http_client('/api/candidates', method='GET')
    assert status == 200 and [item['review_id'] for item in json.loads(raw)['candidates']] == ['review-02']


@pytest.mark.parametrize('payload,actor,status', [
    ({'review_id': 'review-01'}, '', 401),
    ({'review_id': 'review-01', '_actor_user_id': 'teacher', 'role': 'admin', 'actor': {'id': 'teacher'}}, 'other', 403),
    ({'review_id': '../review-01'}, 'teacher', 400), ({'review_id': 'missing'}, 'teacher', 404)])
def test_http_status_and_forged_actor_ignored(http_client, service, payload, actor, status):
    assert http_client('/api/review/delete', payload, actor)[0] == status
    assert (service.REVIEW_ROOT/'review-01').exists()


def test_http_busy_and_sanitized_cloud_failure(http_client, service):
    service.AI_REVIEW_WORKERS['review-01'] = Future()
    assert http_client('/api/review/delete', {'review_id': 'review-01'})[0] == 409
    service.AI_REVIEW_WORKERS.clear()
    def fail(_): raise RuntimeError('private connection credentials')
    service.PLATFORM_PERSISTENCE.delete_review = fail
    status, raw = http_client('/api/review/delete', {'review_id': 'review-01'})
    assert status == 503 and 'private' not in raw.decode() and (service.REVIEW_ROOT/'review-01').exists()


def test_cos_paginates_and_deletes_exact_keys(monkeypatch):
    calls, deleted = [], []
    pages = iter([{'Contents': [{'Key': 'review/r_1/a'}], 'IsTruncated': 'true', 'NextMarker': 'review/r_1/a'}, {'Contents': [{'Key': 'review/r_1/b'}], 'IsTruncated': 'false'}])
    def listing(**kwargs): calls.append(kwargs); return next(pages)
    storage = TencentCosStorage(SimpleNamespace(cos_bucket='test'))
    monkeypatch.setattr(storage, '_get_client', lambda: SimpleNamespace(list_objects=listing, delete_object=lambda **kw: deleted.append(kw['Key'])))
    assert storage.delete_prefix('review/r_1/') == 2
    assert deleted == ['review/r_1/a', 'review/r_1/b'] and calls[1]['Marker'] == 'review/r_1/a'


@pytest.mark.parametrize('page', [{'Contents': [{'Key': 'review/r_10/file'}]}, {'Contents': [], 'IsTruncated': True}])
def test_cos_prefix_and_pagination_guards(monkeypatch, page):
    storage = TencentCosStorage(SimpleNamespace(cos_bucket='test')); deleted = []
    monkeypatch.setattr(storage, '_get_client', lambda: SimpleNamespace(list_objects=lambda **_: page, delete_object=lambda **kw: deleted.append(kw)))
    with pytest.raises((ValueError, RuntimeError)): storage.delete_prefix('review/r_1/')
    assert deleted == []


def test_cos_empty_page(monkeypatch):
    storage = TencentCosStorage(SimpleNamespace(cos_bucket='test'))
    monkeypatch.setattr(storage, '_get_client', lambda: SimpleNamespace(list_objects=lambda **_: {'Contents': None}))
    assert storage.delete_prefix('review/r_1/') == 0
    with pytest.raises(ValueError): storage.delete_prefix('review/r_1')


def test_persistence_deletes_scoped_cos_before_parameterized_database(monkeypatch):
    operations = []; store = PostgresStore('')
    @contextmanager
    def connection(): yield SimpleNamespace(execute=lambda sql, params: operations.append((sql, params)))
    monkeypatch.setattr(store, 'connection', connection)
    persistence = PlatformPersistence(SimpleNamespace(persistence_mode='postgres_cos', cos_prefix='omr'), store, SimpleNamespace(delete_prefix=lambda prefix: operations.append(('COS', prefix))))
    persistence.delete_review('r_1')
    assert operations[0] == ('COS', 'omr/review/r_1/')
    assert 'LEFT(object_key' in operations[1][0] and 'LIKE' not in operations[1][0]
    assert operations[1][1] == ('review', 'omr/review/r_1/', 'omr/review/r_1/')
    assert operations[2][1] == ('review', 'r_1')
    with pytest.raises(ValueError): persistence.delete_review('../r_1')


def test_local_persistence_uses_only_local_files():
    PlatformPersistence(SimpleNamespace(persistence_mode='local'), None, None).delete_review('review-01')
