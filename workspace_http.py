"""HTTP workspace boundary shared by the local server and FastAPI."""
import json
import re
from functools import wraps
from urllib.parse import quote, urlsplit

from platform_workspaces import WorkspaceError
from workspace_context import split_workspace_path, tenant_route, workspace_scope, scoped_payload


def is_admin_api(path):
    return path == "/api/admin" or path.startswith("/api/admin/")


def is_legacy_objective_asset(method, path):
    """Pre-workspace objective scan links belong to the migrated shared workspace."""
    return method in {'GET', 'HEAD'} and bool(re.fullmatch(
        r'/reviews/[A-Za-z0-9_-]+/output/handwriting/objective_view/'
        r'(?:objective\.png|page\.png|manifest\.json)', path))


def workspace_request(method):
    @wraps(method)
    def wrapped(handler, *args, **kwargs):
        import scan_ui as server
        original = handler.path
        parsed = urlsplit(original)
        wid, route = split_workspace_path(parsed.path)
        try:
            if parsed.path.startswith(('/w/', '/api/w/')) and not wid:
                raise WorkspaceError(404, '工作区地址格式错误')
            if route in {'/workspaces', '/workspaces.html'} or route.startswith('/join/'):
                handler.send_file(server.UI_ROOT / 'workspaces.html')
                return
            management = route == '/api/workspaces' or route.startswith('/api/workspaces/')
            if wid or management or (tenant_route(route) and server.AUTH_SERVICE.enabled):
                user = handler._current_user()
                if not user:
                    if wid and route == '/' and handler.command == 'GET':
                        handler.send_redirect('/login.html?next=' + quote(parsed.path, safe=''))
                        return
                    raise WorkspaceError(401, '请先登录账号')
                if management and not wid:
                    payload = None
                    if handler.command == 'POST':
                        if handler.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                            raise WorkspaceError(415, '请使用 application/json')
                        try:
                            length = int(handler.headers.get('Content-Length', '0'))
                            if length < 0 or length > 8192:
                                raise WorkspaceError(413, '工作区请求上限为 8 KB')
                            payload = json.loads(handler.rfile.read(length).decode('utf-8'))
                        except (ValueError, UnicodeError):
                            raise WorkspaceError(400, '请提交有效的 JSON 对象')
                    result = server.WORKSPACES.dispatch(handler.command, route, user, payload)
                    handler.send_json(200, result)
                    return
                if not wid:
                    if is_legacy_objective_asset(handler.command, route):
                        server.WORKSPACES.require_member('shared', user)
                        handler.send_redirect('/api/w/shared' + original)
                        return
                    raise WorkspaceError(409, '请先选择阅卷工作区')
                if is_admin_api(route):
                    handler.path = route + ('?' + parsed.query if parsed.query else '')
                    return method(handler, *args, **kwargs)
                if route != '/' and not tenant_route(route):
                    raise WorkspaceError(404, '工作区接口不存在')
                workspace = server.WORKSPACES.require_member(wid, user)
                handler.path = route + ('?' + parsed.query if parsed.query else '')
                with workspace_scope(workspace, url_prefix='/api/w' if parsed.path.startswith('/api/w/') else '/w'):
                    return method(handler, *args, **kwargs)
            return method(handler, *args, **kwargs)
        except WorkspaceError as error:
            handler.send_json(error.status, {'ok': False, 'error': str(error), 'workspaces_url': '/workspaces'})
        finally:
            handler.path = original
    return wrapped


def install_fastapi_workspaces(app, server, get_user, get_store, auth_enabled=None):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
    from starlette.concurrency import run_in_threadpool

    @app.middleware('http')
    async def workspace_boundary(request, call_next):
        path = request.url.path
        wid, route = split_workspace_path(path)
        try:
            if path.startswith(('/w/', '/api/w/')) and not wid:
                raise WorkspaceError(404, '工作区地址格式错误')
            if path in {'/workspaces', '/workspaces.html'} or path.startswith('/join/'):
                return FileResponse(server.UI_ROOT / 'workspaces.html', headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
            management = path == '/api/workspaces' or path.startswith('/api/workspaces/')
            if management:
                user = await run_in_threadpool(get_user, request)
                payload = None
                if request.method == 'POST':
                    if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                        raise WorkspaceError(415, '请使用 application/json')
                    raw = bytearray()
                    async for chunk in request.stream():
                        raw.extend(chunk)
                        if len(raw) > 8192:
                            raise WorkspaceError(413, '工作区请求上限为 8 KB')
                    try:
                        payload = json.loads(raw)
                    except (ValueError, UnicodeError):
                        raise WorkspaceError(400, '请提交有效的 JSON 对象')
                result = await run_in_threadpool(get_store().dispatch, request.method, path, user, payload)
                return JSONResponse(result, headers={'Cache-Control': 'no-store'})
            if wid:
                user = await run_in_threadpool(get_user, request)
                if is_admin_api(route):
                    request.scope['path'] = route
                    request.scope['raw_path'] = route.encode('utf-8')
                    return await call_next(request)
                if route != '/' and not tenant_route(route):
                    raise WorkspaceError(404, '工作区接口不存在')
                workspace = await run_in_threadpool(get_store().require_member, wid, user)
                if route == '/':
                    return FileResponse(server.UI_ROOT / 'index.html', headers={'Cache-Control': 'no-store'})
                request.scope['path'] = route
                request.scope['raw_path'] = route.encode('utf-8')
                with workspace_scope(workspace, url_prefix='/api/w' if path.startswith('/api/w/') else '/w'):
                    response = await call_next(request)
                    if 'application/json' in response.headers.get('content-type', ''):
                        body = b''.join([part async for part in response.body_iterator])
                        result = scoped_payload(json.loads(body))
                        headers = dict(response.headers)
                        headers.pop('content-length', None)
                        headers['Cache-Control'] = 'no-store'
                        return JSONResponse(result, status_code=response.status_code, headers=headers)
                    return response
            if tenant_route(route) and (auth_enabled() if auth_enabled else server.AUTH_SERVICE.enabled):
                user = await run_in_threadpool(get_user, request)
                if is_legacy_objective_asset(request.method, route):
                    await run_in_threadpool(get_store().require_member, 'shared', user)
                    target = '/api/w/shared' + path + ('?' + request.url.query if request.url.query else '')
                    return RedirectResponse(target, status_code=302, headers={'Cache-Control': 'no-store'})
                raise WorkspaceError(409, '请先选择阅卷工作区')
            return await call_next(request)
        except WorkspaceError as error:
            return JSONResponse({'ok': False, 'error': str(error), 'workspaces_url': '/workspaces'}, status_code=error.status, headers={'Cache-Control': 'no-store'})
        except HTTPException as error:
            if error.status_code == 401 and wid and route == '/' and request.method == 'GET':
                return RedirectResponse('/login.html?next=' + quote(path, safe=''), status_code=302)
            return JSONResponse({'ok': False, 'error': error.detail}, status_code=error.status_code, headers={'Cache-Control': 'no-store'})
