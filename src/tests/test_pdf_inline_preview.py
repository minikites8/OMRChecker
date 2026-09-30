"""Explicit PDF previews stay inline while ordinary assets retain COS links."""
import pytest
import scan_ui
from src.tests.test_cos_direct_links import cloud
from src.tests.test_saas_workspaces import ALICE, http_service, store
from workspace_context import workspace_scope, scoped_payload


@pytest.mark.parametrize('route,kind', [('jobs','scan_job'),('reviews','review'),('imports','exam_import')])
def test_preview_payload_retains_scoped_url_and_cos_download(cloud, route, kind):
    persistence, register, queries, signed = cloud
    with workspace_scope({'id':'a'*32}):
        relative='preview/answer.PDF'
        expected=register(kind, relative)
        signed.clear()
        url='/'+route+'/'+relative
        result=persistence.resolve_asset_urls(scoped_payload({'url':url,'preview_url':url+'?preview=1'}))
        assert result['url']==expected
        assert result['preview_url'].endswith('/w/'+'a'*32+url+'?preview=1')
        assert len(queries)==1 and len(signed)==1
        queries.clear();signed.clear()
        assert persistence.resolve_asset_urls(result['preview_url'])==result['preview_url']
        assert queries==signed==[]


def test_preview_flag_leaves_image_direct_links_intact(cloud):
    persistence, register, queries, signed = cloud
    expected=register('review','r1/photo.png','image/png')
    assert persistence.resolve_asset_urls('/reviews/r1/photo.png?preview=1')==expected


@pytest.mark.parametrize('prefix',['/w','/api/w'])
@pytest.mark.parametrize('route,kind',[('jobs','scan_job'),('reviews','review'),('imports','exam_import')])
def test_explicit_preview_streams_pdf_with_workspace_access(http_service, monkeypatch, cloud, prefix, route, kind):
    client,store,root=http_service
    workspace=store.create(ALICE,'Inline PDFs')
    persistence,register,queries,signed=cloud
    with workspace_scope(workspace):
        base={'jobs':scan_ui.JOBS_ROOT,'reviews':scan_ui.REVIEW_ROOT,'imports':scan_ui.IMPORT_ROOT}[route]
        path=base/'inline'/'answer.PDF'
        path.parent.mkdir(parents=True,exist_ok=True)
        content=b'%PDF-1.4\ninline-preview\n%%EOF\n'
        path.write_bytes(content)
        expected=register(kind,'inline/answer.PDF')
    monkeypatch.setattr(scan_ui,'PLATFORM_PERSISTENCE',persistence)
    url=prefix+'/'+workspace['id']+'/'+route+'/inline/answer.PDF'
    response=client.get(url,headers={'Cookie':'alice'},follow_redirects=False)
    assert response.status_code==302 and response.headers['location']==expected
    queries.clear();signed.clear()
    preview=url+'?preview=1'
    response=client.get(preview,headers={'Cookie':'alice'},follow_redirects=False)
    assert response.status_code==200, response.text
    assert response.content==content
    assert response.headers['content-type'].split(';')[0]=='application/pdf'
    assert response.headers['content-disposition']=='inline'
    assert response.headers['cache-control']=='no-store'
    assert response.headers['x-content-type-options']=='nosniff'
    assert 'location' not in response.headers
    for cookie,status in [('',401),('bob',404),('charlie',404)]:
        assert client.get(preview,headers={'Cookie':cookie},follow_redirects=False).status_code==status
    assert queries==signed==[]
    path.unlink()
    assert client.get(preview,headers={'Cookie':'alice'},follow_redirects=False).status_code==404
    assert queries==signed==[]


def test_scan_preview_payload_has_explicit_inline_url(tmp_path, monkeypatch):
    import review_overlay_pdf
    jobs=tmp_path/'jobs'
    monkeypatch.setattr(scan_ui,'JOBS_ROOT',jobs)
    monkeypatch.setattr(scan_ui.TEMPLATE_MANAGER,'get',lambda key: {'recognition':{}})
    monkeypatch.setattr(scan_ui,'save_uploads',lambda files,path: [])
    monkeypatch.setattr(scan_ui.PLATFORM_PERSISTENCE,'sync_tree',lambda *args,**kwargs: None)
    def build(sources,recognition,output):
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_bytes(b'%PDF-1.4\n%%EOF')
        return {'pages':1,'warnings':[]}
    monkeypatch.setattr(review_overlay_pdf,'build_upload_preview_pdf',build)
    data=scan_ui.create_scan_preview({'card_files':[{'name':'answer.pdf','data':'cGRm'}], 'template_id':'test'})
    assert data['preview_url']==data['pdf_url']+'?preview=1'


def test_review_overlay_payload_has_explicit_inline_url(tmp_path, monkeypatch):
    import review_overlay_pdf
    folder=tmp_path/'reviews'/'r1'
    (folder/'input').mkdir(parents=True)
    (folder/'input'/'answer.pdf').write_bytes(b'%PDF-1.4\n%%EOF')
    (folder/'output').mkdir()
    report=folder/'output'/'review.json'
    monkeypatch.setattr(scan_ui,'REVIEW_ROOT',folder.parent)
    monkeypatch.setattr(scan_ui,'_load_review',lambda rid: ('r1',report,{'card_files':['answer.pdf']}))
    monkeypatch.setattr(scan_ui.PLATFORM_PERSISTENCE,'sync_tree',lambda *args,**kwargs: None)
    monkeypatch.setattr(review_overlay_pdf,'overlay_fingerprint',lambda *args: 'test')
    def build(files,review,destination,reference_pdf):
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(b'%PDF-1.4\n%%EOF')
    monkeypatch.setattr(review_overlay_pdf,'build_overlay_pdf',build)
    data=scan_ui.read_review_overlay_pdf('r1')['files'][0]
    assert data['preview_url']==data['url']+'?preview=1'
