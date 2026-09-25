"""SaaS shell contracts independent of deployment credentials."""
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "ui"


class Markup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.elements = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_saas_shell_keeps_unique_ids_and_existing_app_entrypoints():
    page = Markup((ROOT / "index.html").read_text(encoding="utf-8"))
    ids = [attrs["id"] for _, attrs in page.elements if "id" in attrs]
    assert len(ids) == len(set(ids))
    assert {"accountMenu", "serviceMenu", "accountAdmin", "accountLogout", "storageStatus", "welcomeTitle", "workspaceDate"} <= set(ids)
    scripts = [attrs["src"] for tag, attrs in page.elements if tag == "script"]
    assert scripts[0].startswith("/static/platform.js")
    assert scripts[-1].startswith("/static/app.js")
    assert any(src.startswith("/static/saas.js") for src in scripts)
    assert any(src.startswith("/static/admin.js") for src in scripts)


def test_deployment_labels_come_from_runtime_state():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "数据存储于本地" not in html
    assert "本地扫描工作台" not in html
    assert 'id="storageStatus">读取存储配置中' in html
    assert 'data-service="postgres"' in html
    assert 'data-service="cos"' in html
    assert 'data-service="auth"' in html
    assert 'id="adminNav" hidden' in html
    assert 'id="accountAdmin" href="#admin" hidden' in html


def test_login_has_accessible_fields_and_loading_state():
    html = (ROOT / "login.html").read_text(encoding="utf-8")
    page = Markup(html)
    ids = [attrs["id"] for _, attrs in page.elements if "id" in attrs]
    assert len(ids) == len(set(ids))
    assert 'id="loginForm" hidden' in html
    assert 'id="loginDivider" hidden' in html
    assert 'id="loginLoading" role="status"' in html
    assert 'for="loginEmail"' in html and 'for="loginPassword"' in html
    assert 'autocomplete="username"' in html
    assert 'autocomplete="current-password"' in html
    assert 'aria-controls="loginPassword"' in html
    assert 'id="loginMessage" role="alert"' in html
    assert 'id="loginRetry"' in html


def test_new_assets_are_self_hosted_and_motion_aware():
    for name in ["saas.css", "login.css"]:
        css = (ROOT / name).read_text(encoding="utf-8")
        assert "prefers-reduced-motion:reduce" in css
        assert ":focus-visible" in css
        assert "@import" not in css
        assert "min-width:320px" in css
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert '/static/saas.css?v=1' in html
    assert 'http://fonts' not in html and 'https://fonts' not in html


def test_storage_information_lives_in_admin_system_status():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    sidebar = html.split('id="platformSidebar"', 1)[1].split("</aside>", 1)[0]
    dashboard = html.split('data-view="dashboard"', 1)[1].split('data-view="papers"', 1)[0]
    admin = html.split('data-view="admin"', 1)[1]
    assert 'id="storageStatus"' not in sidebar
    assert 'class="local-label"' not in sidebar
    assert 'id="servicePanelTitle"' not in dashboard
    assert 'id="adminSystemStatus" hidden' in admin
    assert '<h2 id="servicePanelTitle">系统状态</h2>' in admin
    assert 'id="storageStatus">读取存储配置中' in admin
    assert html.count('id="storageStatus"') == 1


def test_shared_service_menu_uses_reader_facing_labels():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    menu = html.split('id="serviceMenu"', 1)[1].split("</details>", 1)[0]
    assert "PostgreSQL" not in menu
    assert "COS" not in menu
    assert "数据服务" in menu
    assert 'data-service="postgres"' in menu
    assert '/static/saas.js?v=storage-admin-1' in html
