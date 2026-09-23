from pathlib import Path
import json
import numpy as np
import pytest
from objective_view import build_objective_view, objective_layout

def test_layout_covers_thirty_questions():
    layout=objective_layout()
    assert [row[0] for row in layout]==[str(i) for i in range(1,31)]
    assert layout[0][-1]=='ABCD' and layout[-1][-1]=='TF'

def test_view_saves_real_scan_and_complete_geometry(tmp_path):
    image=np.arange(842*596,dtype=np.uint32).reshape(842,596).astype('uint8')
    view=build_objective_view(image,tmp_path,page_width=596,page_height=842)
    assert len(view['questions'])==30
    assert view['page']['width']==596 and view['page']['height']==842
    assert view['questions'][0]['polygon']==[[39.0,336.0],[191.0,336.0],[191.0,360.0],[39.0,360.0]]
    assert view['questions'][0]['bubbles'][0]['polygon'][0]==[74.0,342.0]
    import cv2
    restored=cv2.imdecode(np.frombuffer((tmp_path/'page.png').read_bytes(),dtype='uint8'),0)
    assert np.array_equal(restored,image)
    assert json.loads((tmp_path/'manifest.json').read_text(encoding='utf-8'))==view
    for q in view['questions']:
        for x,y in q['polygon']:
            assert view['focus']['x']<=x<=view['focus']['x']+view['focus']['width']
            assert view['focus']['y']<=y<=view['focus']['y']+view['focus']['height']

def test_region_affine_is_inverted_for_original_scan(tmp_path):
    image=np.zeros((842,596),dtype='uint8')
    view=build_objective_view(image,tmp_path,{'single':{'matrix':[[1,0,10],[0,1,-6]]}},596,842)
    assert view['questions'][0]['polygon'][0]==[29.0,342.0]
    assert view['questions'][15]['polygon'][0]==[49.0,491.0]

def test_invalid_matrix_uses_page_alignment(tmp_path):
    view=build_objective_view(np.zeros((842,596),dtype='uint8'),tmp_path,{'single':{'matrix':[[0,0,0],[0,0,0]]}},596,842)
    assert view['questions'][0]['polygon'][0]==[39.0,336.0]

def test_api_uses_cached_manifest_without_ocr(tmp_path,monkeypatch):
    import scan_ui
    monkeypatch.setattr(scan_ui,'REVIEW_ROOT',tmp_path)
    root=tmp_path/'preview-test'/'output';root.mkdir(parents=True)
    (root/'review.json').write_text(json.dumps({'ok':True,'review_id':'preview-test'}),encoding='utf-8')
    build_objective_view(np.zeros((842,596),dtype='uint8'),root/'handwriting/objective_view')
    result=scan_ui.read_objective_view('preview-test')
    assert result['ok'] and len(result['questions'])==30
    assert result['asset_base']=='/reviews/preview-test/output/handwriting/objective_view/'

def test_api_rejects_path_traversal():
    import scan_ui
    with pytest.raises(ValueError):scan_ui.read_objective_view('../escape')

def test_objective_ui_preserves_existing_review_controls():
    root=Path(__file__).resolve().parents[2]
    js=(root/'ui/app.js').read_text(encoding='utf-8')
    viewer=(root/'ui/objective-view.js').read_text(encoding='utf-8-sig')
    assert 'window.objectiveView.render(reviewState.objective,reviewState.reviewId)' in js
    assert 'objectiveNeedsReview(item)' in js
    assert '修正第' in js and '复核结论' in js
    assert 'createElementNS' in viewer and 'AbortController' in viewer
    assert 'relativePoints(region.polygon, view)' in viewer
    assert 'sequence !== state.sequence' in viewer

def test_preview_http_serves_manifest_and_scan(tmp_path,monkeypatch):
    import scan_ui,threading,urllib.request
    monkeypatch.setattr(scan_ui,'REVIEW_ROOT',tmp_path)
    output=tmp_path/'http-preview'/'output';output.mkdir(parents=True)
    (output/'review.json').write_text(json.dumps({'ok':True,'review_id':'http-preview'}),encoding='utf-8')
    build_objective_view(np.zeros((842,596),dtype='uint8'),output/'handwriting/objective_view')
    server=scan_ui.create_server('127.0.0.1',0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        base='http://127.0.0.1:'+str(server.server_address[1])
        data=json.load(urllib.request.urlopen(base+'/api/review/objective-view?review_id=http-preview'))
        assert len(data['questions'])==30
        image=urllib.request.urlopen(base+data['asset_base']+data['focus']['image']).read()
        assert image.startswith(b'\x89PNG')
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
