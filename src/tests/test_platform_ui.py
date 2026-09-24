"""Contracts for the platform shell, independent of the OCR runtime."""
import re
from html.parser import HTMLParser
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[2] / "ui"

class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = []
        self.views = []
        self.scripts = []
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if "data-view" in attrs:
            self.views.append((attrs["data-view"], "hidden" in attrs))
        if tag == "script":
            self.scripts.append(attrs.get("src", ""))


def test_platform_views_and_unique_controls():
    page = Page((UI_ROOT / "index.html").read_text(encoding="utf-8"))
    assert len(page.ids) == len(set(page.ids))
    assert page.views == [("dashboard", False), ("papers", True), ("candidates", True), ("grading", True),
                          ("results", True), ("sheets", True), ("templates", True), ("scanner", True), ("admin", True)]
    assert page.scripts[0].startswith("/static/platform.js")
    assert page.scripts[-1].startswith("/static/app.js")


def test_legacy_query_ids_still_exist():
    page = Page((UI_ROOT / "index.html").read_text(encoding="utf-8"))
    js = (UI_ROOT / "app.js").read_text(encoding="utf-8")
    ids = re.findall(r"document\.querySelector\(['\"]#([\w-]+)['\"]\)", js)
    assert len(ids) > 60
    assert set(ids) <= set(page.ids)


def test_platform_hooks_and_chinese_accessibility():
    html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
    js = (UI_ROOT / "app.js").read_text(encoding="utf-8")
    platform = (UI_ROOT / "platform.js").read_text(encoding="utf-8-sig")
    for event in ("imports", "health", "review", "score", "batch", "ready", "saved", "selection", "busy"):
        assert "platform:" + event in js
        assert "platform:" + event in platform
    for identifier in ("paperSearch", "exportGradeCsv", "reviewEmpty", "menuToggle", "reviewFilterEmpty", "candidateExportJson", "candidateSelectAll", "candidateSelectionSummary", "reviewConfirmGradeButton"):
        assert f'id="{identifier}"' in html
    assert 'aria-label="主导航"' in html
    assert 'aria-pressed="true"' in html
    assert '<option selected>12</option>' in html
    assert "confirmSwitch" in js and "confirmSwitch" in platform


def test_responsive_and_filter_visibility_contracts():
    css = (UI_ROOT / "platform.css").read_text(encoding="utf-8-sig")
    assert "[hidden],.hidden{display:none!important}" in css
    assert "@media(max-width:760px)" in css
    assert "@media(prefers-reduced-motion:reduce)" in css
    assert ":focus-visible" in css
    assert ".review-items{max-height:none" in css
