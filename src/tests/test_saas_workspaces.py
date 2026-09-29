"""Multi-tenant membership, invitation lifecycle, and both HTTP server boundaries."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.app as api
import scan_ui
from platform_workspaces import WorkspaceError, WorkspaceStore
from workspace_context import (WorkspacePath, WorkspaceWorkers, bind_context,
                               current_workspace, scoped_payload, workspace_scope)

ALICE = {'id': 'alice', 'display_name': '王老师', 'role': 'teacher'}
BOB = {'id': 'bob', 'display_name': '李老师', 'role': 'teacher'}
CHARLIE = {'id': 'charlie', 'display_name': '赵老师', 'role': 'admin'}


@pytest.fixture
def store(tmp_path):
    return WorkspaceStore(tmp_path, public_base_url='https://grading.example.test')


def test_create_multiple_persistent_workspaces(store):
    first = store.create(ALICE, ' 高三月考 ')
    second = store.create(ALICE, '期中考试')
    assert first['id'] != second['id']
    assert first['name'] == '高三月考'
    assert first['role'] == 'owner'
    assert len(WorkspaceStore(store.root).list(ALICE)) == 2
    assert store.list(BOB) == []


@pytest.mark.parametrize('name', ['', '  ', None, [], 5, 'a' * 81])
def test_workspace_names_are_validated(store, name):
    with pytest.raises(WorkspaceError) as error:
        store.create(ALICE, name)
    assert error.value.status == 400


@pytest.mark.parametrize('method', ['code', 'url', 'token'])
def test_join_by_code_link_or_link_token_is_idempotent(store, method):
    workspace = store.create(ALICE, '协作阅卷')
    invitation = store.invite(ALICE, workspace['id'], {})
    value = {'code': invitation['code'].lower(), 'url': invitation['invite_url'],
             'token': invitation['invite_path'].split('/')[-1]}[method]
    assert store.join(BOB, value)['id'] == workspace['id']
    assert store.join(BOB, value)['role'] == 'member'
    assert len(store.details(ALICE, workspace['id'])['members']) == 2
    with store.connection() as connection:
        row = connection.execute('SELECT * FROM app_workspace_invitations').fetchone()
        assert row['uses'] == 1
        assert invitation['code'] not in str(dict(row))
        assert invitation['invite_path'].split('/')[-1] not in str(dict(row))


def test_owner_controls_invites_and_platform_admin_requires_membership(store):
    workspace = store.create(ALICE, '协作阅卷')
    invitation = store.invite(ALICE, workspace['id'], {})
    store.join(BOB, invitation['code'])
    with pytest.raises(WorkspaceError) as error:
        store.invite(BOB, workspace['id'], {})
    assert error.value.status == 403
    with pytest.raises(WorkspaceError) as error:
        store.require_member(workspace['id'], CHARLIE)
    assert error.value.status == 404
    with pytest.raises(WorkspaceError):
        store.revoke(BOB, workspace['id'])


def test_rotation_revocation_and_expiry(store):
    workspace = store.create(ALICE, '邀请有效期')
    first = store.invite(ALICE, workspace['id'], {})
    second = store.invite(ALICE, workspace['id'], {})
    with pytest.raises(WorkspaceError):
        store.join(BOB, first['code'])
    store.join(BOB, second['invite_url'])
    store.revoke(ALICE, workspace['id'])
    with pytest.raises(WorkspaceError):
        store.join(CHARLIE, second['code'])
    assert store.require_member(workspace['id'], BOB)['role'] == 'member'
    third = store.invite(ALICE, workspace['id'], {})
    with store.connection(write=True) as connection:
        connection.execute('UPDATE app_workspace_invitations SET expires_at=? WHERE id=?',
                           (int(time.time()) - 1, third['id']))
    with pytest.raises(WorkspaceError):
        store.join(CHARLIE, third['code'])


@pytest.mark.parametrize('payload', [{'expires_in_days': 0}, {'expires_in_days': 31}, {'expires_in_days': True}, {'max_uses': 0}, {'max_uses': 1001}, {'max_uses': '2'}])
def test_invitation_limits_are_validated(store, payload):
    workspace = store.create(ALICE, '邀请配置')
    with pytest.raises(WorkspaceError) as error:
        store.invite(ALICE, workspace['id'], payload)
    assert error.value.status == 400


def test_single_use_invite_serializes_concurrent_redemptions(store):
    workspace = store.create(ALICE, '并发加入')
    invitation = store.invite(ALICE, workspace['id'], {'max_uses': 1})
    def join(user):
        try:
            store.join(user, invitation['code'])
            return True
        except WorkspaceError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(join, [BOB, CHARLIE])) == [False, True]
    assert len(store.details(ALICE, workspace['id'])['members']) == 2


def test_legacy_membership_snapshot_is_one_time(store):
    store.migrate_shared([ALICE, BOB])
    store.migrate_shared([ALICE, BOB, CHARLIE])
    assert store.require_member('shared', ALICE)['role'] == 'owner'
    assert store.require_member('shared', BOB)['role'] == 'member'
    assert store.list(CHARLIE) == []


def test_paths_workers_and_background_context_are_isolated(tmp_path):
    path = WorkspacePath(tmp_path, 'outputs/answer_review')
    workers = WorkspaceWorkers()
    with workspace_scope({'id': 'a' * 32, 'name': '甲'}):
        workers['same-id'] = 'a'
        work = bind_context(lambda: (str(path), workers['same-id'], current_workspace()['id']))
    with workspace_scope({'id': 'b' * 32, 'name': '乙'}):
        workers['same-id'] = 'b'
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(work).result()
        assert result == (str(tmp_path/'workspaces'/('a'*32)/'outputs/answer_review'), 'a', 'a'*32)
        assert workers['same-id'] == 'b'
    assert current_workspace() is None
    assert Path(path) == tmp_path/'outputs/answer_review'


def test_scoped_links_and_collaboration_metadata():
    from review_collaboration import describe_review
    with workspace_scope({'id': 'a' * 32, 'name': '高三'}):
        payload = scoped_payload({'links': ['/reviews/r/output/review.json', '/imports/x.json', '/api/session', 'https://example.test/reviews/a']})
        assert payload['links'][0] == '/w/' + 'a'*32 + '/reviews/r/output/review.json'
        assert payload['links'][2:] == ['/api/session', 'https://example.test/reviews/a']
        assert scoped_payload(payload) == payload
        assert describe_review({})['workspace_name'] == '高三'


def test_cloud_object_keys_are_isolated(tmp_path):
    from platform_persistence import PlatformPersistence
    persistence = PlatformPersistence(SimpleNamespace(cos_prefix='omr'), None, None)
    with workspace_scope({'id': 'a' * 32, 'name': '甲'}):
        first = persistence._key('review', Path('same/output.json'))
    with workspace_scope({'id': 'b' * 32, 'name': '乙'}):
        second = persistence._key('review', Path('same/output.json'))
    assert first != second
    assert '/workspaces/' in first


@pytest.fixture(params=['fastapi', 'legacy'])
def http_service(request, store, monkeypatch, tmp_path):
    users = {'alice': ALICE, 'bob': BOB, 'charlie': CHARLIE}
    auth = SimpleNamespace(enabled=True, user_from_headers=lambda cookie: users.get(cookie))
    monkeypatch.setattr(scan_ui, 'AUTH_SERVICE', auth)
    monkeypatch.setattr(api, 'auth', auth)
    monkeypatch.setattr(scan_ui, 'WORKSPACES', store)
    monkeypatch.setattr(api, 'workspaces', store)
    for name, relative in [('REVIEW_ROOT', 'outputs/answer_review'), ('IMPORT_ROOT', 'outputs/exam_imports'), ('JOBS_ROOT', 'outputs/scan_ui'), ('SHEETS_ROOT', 'output/pdf/web_designer')]:
        monkeypatch.setattr(scan_ui, name, WorkspacePath(tmp_path, relative))
    def user(req):
        result = users.get(req.headers.get('cookie', ''))
        if not result:
            raise HTTPException(401, '请先登录账号')
        return result
    monkeypatch.setattr(api, 'current_user', user)
    if request.param == 'fastapi':
        client = TestClient(api.app, raise_server_exceptions=False)
        yield client, store, tmp_path
        client.close()
    else:
        server = scan_ui.create_server('127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{server.server_address[1]}', timeout=5) as client:
                yield client, store, tmp_path
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_http_create_invite_join_and_member_permissions(http_service):
    client, store, root = http_service
    response = client.post('/api/workspaces', json={'name': '春季联考'}, headers={'Cookie': 'alice'})
    assert response.status_code == 200, response.text
    workspace = response.json()['workspace']
    wid = workspace['id']
    assert client.get('/api/workspaces', headers={'Cookie': 'bob'}).json()['workspaces'] == []
    invite = client.post(f'/api/workspaces/{wid}/invitations', json={}, headers={'Cookie': 'alice'}).json()['invitation']
    response = client.post('/api/workspaces/join', json={'invite': invite['invite_url']}, headers={'Cookie': 'bob'})
    assert response.status_code == 200, response.text
    assert response.json()['workspace']['id'] == wid
    assert client.get(f'/api/workspaces/{wid}', headers={'Cookie': 'bob'}).json()['members'][0]['user_id'] == 'alice'
    assert client.post(f'/api/workspaces/{wid}/invitations', json={}, headers={'Cookie': 'bob'}).status_code == 403
    assert client.get(f'/w/{wid}/', headers={'Cookie': 'bob'}).status_code == 200


@pytest.mark.parametrize('prefix', ['', '/w/shared', '/w/' + 'f' * 32])
def test_http_admin_api_remains_available_without_workspace(http_service, monkeypatch, prefix):
    client, store, root = http_service
    database = SimpleNamespace(configured=False)
    monkeypatch.setattr(api, 'database', database)
    monkeypatch.setattr(scan_ui, 'PLATFORM_DATABASE', database)
    path = prefix + '/api/admin/overview'
    response = client.get(path, headers={'Cookie': 'charlie'})
    assert response.status_code == 200, (path, response.text)
    assert response.json()['ok'] is True
    assert response.json()['user_management_enabled'] is False
    assert current_workspace() is None
    for cookie, status in [('alice', 403), ('', 401)]:
        assert client.get(path, headers={'Cookie': cookie}).status_code == status
    for method, route in [('POST', '/api/admin/users'), ('PATCH', '/api/admin/users/charlie')]:
        for cookie, status in [('alice', 403), ('', 401)]:
            assert client.request(method, prefix + route, headers={'Cookie': cookie}, json={}).status_code == status


def test_http_data_assets_and_lists_are_isolated(http_service):
    client, store, root = http_service
    first, second = store.create(ALICE, '甲学校'), store.create(BOB, '乙学校')
    for workspace in (first, second):
        with workspace_scope(workspace):
            path = scan_ui.REVIEW_ROOT/'same/output/review.json';path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'workspace': workspace['name']}), encoding='utf-8')
            path = scan_ui.IMPORT_ROOT/'same/normalized_exam.json';path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'name': workspace['name'], 'summary': {}, 'exam': {'sections': []}}), encoding='utf-8')
    for who, own, other in [('alice', first, second), ('bob', second, first)]:
        headers = {'Cookie': who}
        assert client.get(f"/w/{own['id']}/reviews/same/output/review.json", headers=headers).json()['workspace'] == own['name']
        result = client.get(f"/w/{own['id']}/api/exam/imports", headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()['imports'][0]['name'] == own['name']
        for path in [f"/w/{other['id']}/reviews/same/output/review.json", f"/w/{other['id']}/api/exam/imports", f"/api/workspaces/{other['id']}"]:
            assert client.get(path, headers=headers).status_code == 404
        assert client.get('/reviews/same/output/review.json', headers=headers).status_code == 409
        assert client.get('/api/exam/imports', headers=headers).status_code == 409
    assert current_workspace() is None


def test_http_requires_login_and_rejects_malformed_requests(http_service):
    client, store, root = http_service
    assert client.get('/api/workspaces').status_code == 401
    workspace = store.create(ALICE, '权限验证')
    assert client.get(f"/w/{workspace['id']}/api/exam/imports").status_code == 401
    assert client.get('/w/invalid/api/exam/imports', headers={'Cookie': 'alice'}).status_code == 404
    assert client.post('/api/workspaces/join', content='bad', headers={'Cookie': 'alice', 'Content-Type': 'application/json'}).status_code == 400
    assert client.post('/api/workspaces', json={'name': 'x' * 9000}, headers={'Cookie': 'alice'}).status_code == 413
    assert client.post('/api/workspaces', content='name=test', headers={'Cookie': 'alice'}).status_code == 415
    assert client.get('/join/' + 'x'*43).status_code == 200
    assert client.get('/workspaces').status_code == 200


def test_http_join_code_and_revoked_invite(http_service):
    client, store, root = http_service
    workspace = store.create(ALICE, '代码加入')
    invite = store.invite(ALICE, workspace['id'], {})
    assert client.post('/api/workspaces/join', json={'invite': invite['code'].lower()}, headers={'Cookie': 'bob'}).status_code == 200
    assert client.post(f"/api/workspaces/{workspace['id']}/invitations/revoke", json={}, headers={'Cookie': 'alice'}).status_code == 200
    assert client.post('/api/workspaces/join', json={'invite': invite['invite_url']}, headers={'Cookie': 'charlie'}).status_code == 400


def test_http_workspace_page_retains_destination_through_login(http_service):
    client, store, root = http_service
    workspace = store.create(ALICE, '登录跳转')
    response = client.get(f"/w/{workspace['id']}/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['location'] == '/login.html?next=%2Fw%2F' + workspace['id'] + '%2F'
