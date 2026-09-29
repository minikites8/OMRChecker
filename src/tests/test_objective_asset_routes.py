"""Read-only legacy objective links retain shared-workspace member checks."""
import base64

import pytest
import scan_ui
import backend.app as api
from fastapi import HTTPException
from src.tests.test_saas_workspaces import ALICE, BOB, CHARLIE, http_service, store
from workspace_context import current_workspace, workspace_scope

BASE = '/reviews/20260929-130905-324e6a/output/handwriting/objective_view/'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5CYAAAAASUVORK5CYII=')


def seed(workspace, filename, content):
    with workspace_scope(workspace):
        path = scan_ui.REVIEW_ROOT / ('20260929-130905-324e6a/output/handwriting/objective_view/' + filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def browser_session(client, monkeypatch):
    # Real browsers retain a valid session cookie on same-origin redirects.
    users = {'alice': ALICE, 'bob': BOB, 'charlie': CHARLIE}
    def from_headers(cookie):
        return users.get(cookie.removeprefix('test_session='))
    def current_user(request):
        user = from_headers(request.headers.get('cookie', ''))
        if not user:
            raise HTTPException(401, 'login required')
        return user
    monkeypatch.setattr(scan_ui.AUTH_SERVICE, 'user_from_headers', from_headers)
    monkeypatch.setattr(api, 'current_user', current_user)
    client.cookies.set('test_session', 'alice', path='/')


@pytest.mark.parametrize('filename,content,content_type', [
    ('objective.png', PNG, 'image/png'), ('page.png', PNG, 'image/png'),
    ('manifest.json', b'{"version": 1}', 'application/json'),
])
def test_legacy_objective_assets_redirect_to_shared_api(http_service, monkeypatch, filename, content, content_type):
    client, store, root = http_service
    store.migrate_shared([ALICE])
    seed(store.require_member('shared', ALICE), filename, content)
    route = BASE + filename + '?download=1'
    response = client.get(route, headers={'Cookie': 'alice'}, follow_redirects=False)
    assert response.status_code == 302, response.text
    assert response.headers['location'] == '/api/w/shared' + route
    assert 'no-store' in response.headers['cache-control']
    browser_session(client, monkeypatch)
    response = client.get(route, follow_redirects=True)
    assert response.status_code == 200, response.text
    assert content_type in response.headers['content-type']
    if filename.endswith('.png'):
        assert response.content == content
    else:
        assert response.json() == {'version': 1}
    assert current_workspace() is None


def test_legacy_objective_assets_require_login_and_shared_membership(http_service):
    client, store, root = http_service
    store.migrate_shared([ALICE])
    seed(store.require_member('shared', ALICE), 'objective.png', PNG)
    for cookie, status in [('', 401), ('bob', 404), ('charlie', 404)]:
        response = client.get(BASE + 'objective.png', headers={'Cookie': cookie}, follow_redirects=False)
        assert response.status_code == status
        assert 'location' not in response.headers
    assert current_workspace() is None


def test_legacy_assets_are_bound_to_shared_and_never_infer_another_workspace(http_service, monkeypatch):
    client, store, root = http_service
    store.migrate_shared([ALICE])
    shared = store.require_member('shared', ALICE)
    another = store.create(ALICE, 'separate workspace')
    seed(shared, 'objective.png', PNG)
    seed(another, 'objective.png', PNG + b'separate-workspace')
    browser_session(client, monkeypatch)
    response = client.get(BASE + 'objective.png', follow_redirects=True)
    assert response.content == PNG
    response = client.get('/api/w/' + another['id'] + BASE + 'objective.png', headers={'Cookie': 'alice'})
    assert response.content == PNG + b'separate-workspace'
    store.create(BOB, 'unrelated workspace')
    assert client.get(BASE + 'objective.png', headers={'Cookie': 'bob'}).status_code == 404
    assert current_workspace() is None


@pytest.mark.parametrize('method,route', [
    ('POST', BASE + 'objective.png'), ('GET', '/api/candidates'),
    ('GET', '/reviews/latest.json'), ('GET', BASE + 'unexpected.png'),
    ('GET', '/reviews/other/output/private.json'),
])
def test_legacy_compatibility_keeps_workspace_selection_for_other_routes(http_service, method, route):
    client, store, root = http_service
    store.migrate_shared([ALICE])
    response = client.request(method, route, headers={'Cookie': 'alice'}, follow_redirects=False)
    assert response.status_code == 409
    assert 'location' not in response.headers
