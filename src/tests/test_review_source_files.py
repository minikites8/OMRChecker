"""Original uploaded PDFs remain readable by teachers in their own workspaces."""
import json
from pathlib import Path
from urllib.parse import quote

import pytest
import scan_ui
from src.tests.test_saas_workspaces import ALICE, BOB, CHARLIE, http_service, store
from workspace_context import current_workspace, workspace_scope

RID = '20260929-130905-original'
PDF = b'%PDF-1.4\n% original uploaded bytes\n%%EOF\n'


def seed(workspace, names=None, include_metadata=True):
    with workspace_scope(workspace):
        folder = scan_ui.REVIEW_ROOT / RID
        (folder / 'input').mkdir(parents=True, exist_ok=True)
        (folder / 'output').mkdir(parents=True, exist_ok=True)
        review = {'review_id': RID, 'owner_user_id': 'another-teacher', 'items': [], 'objective': []}
        if include_metadata:
            review['card_files'] = names if names is not None else ['答卷 1 #.PDF', 'back.pdf', 'photo.png']
        for name in ['答卷 1 #.PDF', 'back.pdf', 'unlisted.pdf']:
            (folder / 'input' / name).write_bytes(PDF + name.encode('utf-8'))
        (folder / 'input' / 'photo.png').write_bytes(b'png')
        path = folder / 'output' / 'review.json'
        path.write_text(json.dumps(review), encoding='utf-8')
        return folder, path.read_bytes()


@pytest.mark.parametrize('prefix', ['/w', '/api/w'])
def test_teacher_can_open_original_pdfs_for_a_collaborative_review(http_service, prefix):
    client, store, root = http_service
    store.migrate_shared([ALICE, BOB])
    workspace = store.require_member('shared', ALICE)
    folder, before = seed(workspace)
    for teacher in ['alice', 'bob']:
        response = client.get(prefix + '/shared/api/review/source-files', params={'review_id': RID}, headers={'Cookie': teacher})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['ok'] is True and data['review_id'] == RID
        assert [f['name'] for f in data['files']] == ['答卷 1 #.PDF', 'back.pdf']
        for item in data['files']:
            assert item['url'] == prefix + '/shared/reviews/' + RID + '/input/' + quote(item['name'], safe='')
            pdf = client.get(item['url'], headers={'Cookie': teacher})
            assert pdf.status_code == 200
            assert 'application/pdf' in pdf.headers['content-type']
            assert pdf.content == PDF + item['name'].encode('utf-8')
            assert item['size'] == len(pdf.content)
    assert (folder / 'output/review.json').read_bytes() == before
    assert current_workspace() is None


def test_source_metadata_and_pdf_bytes_enforce_workspace_membership(http_service):
    client, store, root = http_service
    own, other = store.create(ALICE, 'Own'), store.create(BOB, 'Other')
    seed(own); seed(other)
    for workspace, cookie in [(own, 'alice'), (other, 'bob')]:
        base = '/api/w/' + workspace['id']
        for route in ['/api/review/source-files?review_id=' + RID, '/reviews/' + RID + '/input/back.pdf']:
            assert client.get(base + route, headers={'Cookie': cookie}).status_code == 200
            for user, status in [('', 401), ('bob' if cookie == 'alice' else 'alice', 404), ('charlie', 404)]:
                assert client.get(base + route, headers={'Cookie': user}).status_code == status
    assert client.get('/api/review/source-files?review_id=' + RID, headers={'Cookie': 'alice'}).status_code == 409


def test_original_pdf_list_skips_missing_duplicate_and_traversal_entries(http_service):
    client, store, root = http_service
    workspace = store.create(ALICE, 'Source validation')
    seed(workspace, ['back.pdf', 'back.pdf', '../outside.pdf', '..\\outside.pdf', '/outside.pdf', 42, None, 'missing.pdf', 'photo.png'])
    response = client.get('/api/w/' + workspace['id'] + '/api/review/source-files', params={'review_id': RID}, headers={'Cookie': 'alice'})
    assert response.status_code == 200
    assert [item['name'] for item in response.json()['files']] == ['back.pdf']


def test_legacy_review_without_file_metadata_lists_retained_pdf_uploads(http_service):
    client, store, root = http_service
    workspace = store.create(ALICE, 'Legacy')
    seed(workspace, include_metadata=False)
    response = client.get('/api/w/' + workspace['id'] + '/api/review/source-files', params={'review_id': RID}, headers={'Cookie': 'alice'})
    assert response.status_code == 200
    assert len(response.json()['files']) == 3
    assert all(item['name'].lower().endswith('.pdf') for item in response.json()['files'])


def test_image_only_or_missing_originals_return_an_empty_pdf_list(http_service):
    client, store, root = http_service
    workspace = store.create(ALICE, 'Images')
    seed(workspace, ['photo.png', 'missing.pdf'])
    response = client.get('/api/w/' + workspace['id'] + '/api/review/source-files', params={'review_id': RID}, headers={'Cookie': 'alice'})
    assert response.status_code == 200
    assert response.json()['files'] == []


def test_missing_review_returns_a_readable_error(http_service):
    client, store, root = http_service
    workspace = store.create(ALICE, 'Missing')
    response = client.get('/api/w/' + workspace['id'] + '/api/review/source-files', params={'review_id': 'missing'}, headers={'Cookie': 'alice'})
    assert response.status_code == 400
    assert response.json()['ok'] is False
