"""Workspace JSON and file routes through API-only deployment gateways."""
import json

import pytest
import scan_ui
from src.tests.test_saas_workspaces import ALICE, BOB, CHARLIE, http_service, store
from workspace_context import current_workspace, scoped_payload, split_workspace_path, workspace_scope


def seed(workspace):
    with workspace_scope(workspace):
        imported = scan_ui.IMPORT_ROOT / 'fixture/normalized_exam.json'
        imported.parent.mkdir(parents=True, exist_ok=True)
        imported.write_text(json.dumps({'name': workspace['name'], 'summary': {}, 'exam': {'sections': []}}), encoding='utf-8')
        latest = scan_ui.REVIEW_ROOT / 'latest.json'
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(json.dumps({'review_id': workspace['id'] + '-fixture'}), encoding='utf-8')
        (scan_ui.REVIEW_ROOT / 'fixture.png').write_bytes(b'png-regression-bytes')


@pytest.mark.parametrize('prefix', ['/w', '/api/w'])
def test_shared_json_endpoints(http_service, prefix):
    client, store, root = http_service
    store.migrate_shared([CHARLIE])
    shared = store.require_member('shared', CHARLIE)
    seed(shared)
    for route, key in [('/api/exam/imports', 'imports'), ('/reviews/latest.json', 'review_id'), ('/api/candidates', 'candidates')]:
        response = client.get(prefix + '/shared' + route, headers={'Cookie': 'charlie'})
        assert response.status_code == 200, response.text
        assert 'application/json' in response.headers['content-type']
        data = response.json()
        assert key in data
        if key == 'imports': assert data[key][0]['name'] == shared['name']
        if key == 'review_id': assert data[key] == 'shared-fixture'
        assert client.get(prefix + '/shared' + route).status_code == 401
        assert client.get(prefix + '/shared' + route, headers={'Cookie': 'bob'}).status_code == 404
    assert current_workspace() is None


def test_api_gateway_data_and_files_keep_tenant_isolation(http_service):
    client, store, root = http_service
    first, second = store.create(ALICE, 'workspace A'), store.create(BOB, 'workspace B')
    for workspace in [first, second]: seed(workspace)
    for user, own, other in [('alice', first, second), ('bob', second, first)]:
        base = '/api/w/' + own['id']
        result = client.get(base + '/api/exam/imports', headers={'Cookie': user})
        assert result.status_code == 200, result.text
        assert result.json()['imports'][0]['name'] == own['name']
        result = client.get(base + '/reviews/latest.json', headers={'Cookie': user})
        assert result.json()['review_id'] == own['id'] + '-fixture'
        result = client.get(base + '/reviews/fixture.png?download=1', headers={'Cookie': user})
        assert result.status_code == 200 and result.content == b'png-regression-bytes'
        for route in ['/api/exam/imports', '/api/candidates', '/reviews/latest.json', '/reviews/fixture.png']:
            assert client.get('/api/w/' + other['id'] + route, headers={'Cookie': user}).status_code == 404
            assert client.get(base + route, headers={'Cookie': 'charlie'}).status_code == 404
        for method, status, cookie in [('POST', 401, ''), ('POST', 404, 'charlie')]:
            response = client.request(method, base + '/api/review', json={}, headers={'Cookie': cookie})
            assert response.status_code == status
    assert current_workspace() is None


@pytest.mark.parametrize('path', ['/api/w/invalid/api/candidates', '/api/w/shared-extra/reviews/latest.json'])
def test_invalid_workspace_alias_has_json_error(http_service, path):
    client, store, root = http_service
    response = client.get(path, headers={'Cookie': 'alice'})
    assert response.status_code == 404
    assert response.json()['ok'] is False


def test_scoped_links_rebase_and_restore_request_transport():
    first, second = {'id': 'a' * 32}, {'id': 'b' * 32}
    with workspace_scope(first, url_prefix='/api/w'):
        value = scoped_payload({'urls': ['/reviews/r/result.png', '/w/shared/imports/a.json', '/api/w/shared/sheets/a.pdf', 'https://elsewhere.test/reviews/x']})
        assert value['urls'][:3] == ['/api/w/' + first['id'] + p for p in ['/reviews/r/result.png', '/imports/a.json', '/sheets/a.pdf']]
        assert value['urls'][3] == 'https://elsewhere.test/reviews/x'
        assert scoped_payload(value) == value
        with workspace_scope(second):
            assert scoped_payload('/reviews/x') == '/w/' + second['id'] + '/reviews/x'
        assert scoped_payload('/reviews/x') == '/api/w/' + first['id'] + '/reviews/x'
    with workspace_scope(second):
        assert scoped_payload('/reviews/x') == '/w/' + second['id'] + '/reviews/x'
    assert scoped_payload('/reviews/x') == '/reviews/x'
    assert split_workspace_path('/api/w/shared/api/candidates') == ('shared', '/api/candidates')
