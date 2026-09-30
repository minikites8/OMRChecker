"""COS media URLs are signed at the HTTP boundary after workspace authorization."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
import scan_ui
from platform_config import PlatformSettings
from platform_persistence import PlatformPersistence
from src.tests.test_saas_workspaces import ALICE, http_service, store
from src.tests.test_review_source_files import RID, seed
from workspace_context import workspace_scope


@pytest.fixture
def cloud():
    rows, queries, signed = {}, [], []
    def find_artifacts(keys):
        queries.append(list(keys))
        return [rows[key] for key in keys if key in rows]
    def sign(key):
        signed.append(key)
        return 'https://bucket.cos.ap-test.myqcloud.com/' + quote(key, safe='/') + '?q-signature=test'
    persistence = PlatformPersistence(
        replace(PlatformSettings.from_env(), persistence_mode='postgres_cos', cos_prefix='omrchecker'),
        SimpleNamespace(find_artifacts=find_artifacts), SimpleNamespace(presigned_url=sign))
    def register(kind, relative, mime='application/pdf'):
        key = persistence._key(kind, Path(relative))
        rows[key] = {'object_key': key, 'content_type': mime, 'size_bytes': 125}
        return sign(key)
    return persistence, register, queries, signed


@pytest.mark.parametrize('route,kind,mime', [('reviews', 'review', 'image/png'), ('imports', 'exam_import', 'application/pdf'), ('jobs', 'scan_job', 'application/pdf')])
def test_media_links_use_registered_cos_objects(cloud, route, kind, mime):
    persistence, register, queries, signed = cloud
    extension = 'png' if mime.startswith('image/') else 'pdf'
    relative = 'r1/答卷 1 #.' + extension
    with workspace_scope({'id': 'a' * 32}):
        expected = register(kind, relative, mime)
        signed.clear()
        url = '/api/w/' + 'a' * 32 + '/' + route + '/' + quote(relative, safe='/')
        payload = {'file': {'url': url}, 'items': [url], 'text': 'hello'}
        result = persistence.resolve_asset_urls(payload)
    assert result == {'file': {'url': expected}, 'items': [expected], 'text': 'hello'}
    assert payload['file']['url'] == url
    assert len(queries) == 1 and len(queries[0]) == 1 and len(signed) == 1


def test_local_and_unregistered_media_keep_the_original_links(cloud):
    persistence, register, queries, signed = cloud
    value = {'url': '/reviews/r1/missing.pdf'}
    assert persistence.resolve_asset_urls(value) == value
    persistence.settings = replace(persistence.settings, persistence_mode='local')
    queries.clear()
    assert persistence.resolve_asset_urls(value) == value
    assert queries == []


@pytest.mark.parametrize('url', ['/reviews/r1/review.json', '/reviews/r1/scores.csv', '/sheets/r1/package.zip', '/static/logo.png', 'https://example.test/photo.png', '//example.test/photo.png', '/reviews/r1/../secret.pdf', '/reviews/r1/%2e%2e/secret.pdf', '/reviews/r1/%5csecret.pdf', '/reviews/r1/%00secret.pdf', '/w/' + 'b'*32 + '/reviews/r1/photo.png'])
def test_unrelated_and_invalid_links_stay_unchanged(cloud, url):
    persistence, register, queries, signed = cloud
    with workspace_scope({'id': 'a' * 32}):
        assert persistence.resolve_asset_urls({'url': url}) == {'url': url}
    assert queries == []


def test_shared_workspace_uses_existing_cos_keys_and_retains_fragment(cloud):
    persistence, register, queries, signed = cloud
    with workspace_scope({'id': 'shared'}):
        expected = register('review', 'r1/answer.pdf')
        assert persistence.resolve_asset_urls('/w/shared/reviews/r1/answer.pdf?cache=123#page=2') == expected + '#page=2'
    assert '/workspaces/' not in expected


def test_metadata_content_type_limits_direct_links(cloud):
    persistence, register, queries, signed = cloud
    register('review', 'r1/photo.png', 'text/html')
    signed.clear()
    assert persistence.resolve_asset_urls('/reviews/r1/photo.png') == '/reviews/r1/photo.png'
    assert signed == []


def test_http_pdf_payload_returns_cos_url_for_each_server(http_service, monkeypatch, cloud):
    client, store, root = http_service
    workspace = store.create(ALICE, 'COS links')
    seed(workspace)
    persistence, register, queries, signed = cloud
    with workspace_scope(workspace):
        expected = register('review', RID + '/input/back.pdf')
    monkeypatch.setattr(scan_ui, 'PLATFORM_PERSISTENCE', persistence)
    response = client.get('/api/w/' + workspace['id'] + '/api/review/source-files', params={'review_id': RID}, headers={'Cookie': 'alice'})
    assert response.status_code == 200, response.text
    files = response.json()['files']
    assert next(item['url'] for item in files if item['name'] == 'back.pdf') == expected
    assert next(item['url'] for item in files if item['name'] != 'back.pdf').startswith('/api/w/' + workspace['id'] + '/reviews/')


@pytest.mark.parametrize('on_disk', [True, False])
def test_http_asset_redirects_to_cos_after_membership_check(http_service, monkeypatch, cloud, on_disk):
    client, store, root = http_service
    workspace = store.create(ALICE, 'COS redirects')
    folder, before = seed(workspace)
    persistence, register, queries, signed = cloud
    with workspace_scope(workspace):
        expected = register('review', RID + '/input/back.pdf')
    if not on_disk:
        (folder / 'input/back.pdf').unlink()
    monkeypatch.setattr(scan_ui, 'PLATFORM_PERSISTENCE', persistence)
    url = '/api/w/' + workspace['id'] + '/reviews/' + RID + '/input/back.pdf'
    response = client.get(url, headers={'Cookie': 'alice'}, follow_redirects=False)
    assert response.status_code == 302, response.text
    assert response.headers['location'] == expected
    assert response.headers['cache-control'] == 'no-store'
    signed.clear()
    assert client.get(url, headers={'Cookie': 'bob'}, follow_redirects=False).status_code == 404
    assert client.get(url, follow_redirects=False).status_code == 401
    assert signed == []
