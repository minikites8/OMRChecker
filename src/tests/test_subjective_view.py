"""Subjective workspace wiring contracts alongside the existing review controls."""
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_subjective_assets_load_before_the_legacy_review_renderer():
    html=(ROOT/'ui/index.html').read_text(encoding='utf-8')
    assert 'subjective-view.css?v=' in html
    assert html.index('/static/subjective-view.js') < html.index('/static/app.js')
    assert 'data-filter="text" aria-pressed="false">主观题' in html
    assert 'id="reviewItems"' in html


def test_existing_controls_and_save_payload_are_preserved():
    app=(ROOT/'ui/app.js').read_text(encoding='utf-8')
    assert 'window.subjectiveView.render(reviewState.items,reviewState.reviewId)' in app
    assert "'题复核状态'" in app and "'题人工修正答案'" in app
    assert 'status:item.manual_status,text:item.manual_text' in app
    assert "select.addEventListener('change',function(){item.manual_status=select.value;updateTextScoreBadge();renderScoreBoard()})" in app


def test_subjective_filter_delegation_and_live_scoring_contract():
    platform=(ROOT/'ui/platform.js').read_text(encoding='utf-8')
    viewer=(ROOT/'ui/subjective-view.js').read_text(encoding='utf-8-sig')
    assert "kind === 'text' && window.subjectiveView" in platform
    assert 'window.subjectiveView.setFilter(model.filter)' in platform
    assert 'const statusOf = item => textLocalStatus(item)' in viewer
    assert "window.addEventListener('platform:score', update)" in viewer
    assert "$('subjectiveDetailSlot').append($('reviewItems'))" in viewer


def test_subjective_navigation_accessibility_and_missing_scan_states():
    viewer=(ROOT/'ui/subjective-view.js').read_text(encoding='utf-8-sig')
    for contract in ['aria-label="主观题导航"','for="subjectiveZoom">主观题扫描缩放',"button.setAttribute('aria-pressed'",'ArrowLeft','Home','End',"image.addEventListener('error'",'本题尚无作答扫描图','扫描图加载失败']:
        assert contract in viewer
    assert "card.hidden = card.dataset.question !== state.selected" in viewer
    assert 'element.textContent = text' in viewer
