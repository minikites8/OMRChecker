"""Request-local tenant paths and context propagation into background jobs."""
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from collections.abc import MutableMapping
from pathlib import Path
import os
import re

CURRENT_WORKSPACE = ContextVar('omr_workspace', default=None)
WORKSPACE_URL_PREFIX = ContextVar('omr_workspace_url_prefix', default='/w')


def current_workspace():
    return CURRENT_WORKSPACE.get()


@contextmanager
def workspace_scope(workspace, *, url_prefix='/w'):
    token = CURRENT_WORKSPACE.set(workspace)
    prefix_token = WORKSPACE_URL_PREFIX.set(url_prefix)
    try:
        yield
    finally:
        WORKSPACE_URL_PREFIX.reset(prefix_token)
        CURRENT_WORKSPACE.reset(token)


def bind_context(function):
    context = copy_context()
    return lambda *args, **kwargs: context.run(function, *args, **kwargs)


class WorkspacePath(os.PathLike):
    def __init__(self, root, relative):
        self.root, self.relative = Path(root), Path(relative)

    def path(self):
        workspace = current_workspace()
        root = self.root
        if workspace and workspace['id'] != 'shared':
            root = root / 'workspaces' / workspace['id']
        return root / self.relative

    def __fspath__(self):
        return str(self.path())

    def __str__(self):
        return str(self.path())

    def __truediv__(self, other):
        return self.path() / other

    def __getattr__(self, name):
        return getattr(self.path(), name)


class WorkspaceResource:
    def __init__(self, factory):
        self.factory = factory
        self.instances = {}
        import threading
        self.lock = threading.RLock()

    def __getattr__(self, name):
        workspace = current_workspace()
        key = workspace['id'] if workspace else 'shared'
        with self.lock:
            if key not in self.instances:
                self.instances[key] = self.factory()
            resource = self.instances[key]
        return getattr(resource, name)


class WorkspaceWorkers(MutableMapping):
    def __init__(self):
        self.items_by_workspace = {}

    def _items(self):
        workspace = current_workspace()
        return self.items_by_workspace.setdefault(workspace['id'] if workspace else 'shared', {})

    def __getitem__(self, key):
        return self._items()[key]

    def __setitem__(self, key, value):
        self._items()[key] = value

    def __delitem__(self, key):
        del self._items()[key]

    def __iter__(self):
        return iter(self._items())

    def __len__(self):
        return len(self._items())


def scoped_payload(value):
    workspace = current_workspace()
    if not workspace:
        return value
    if isinstance(value, dict):
        return {key: scoped_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scoped_payload(item) for item in value]
    if isinstance(value, str):
        # Stored JSON stays portable; links returned to the browser are explicit.
        value = re.sub(r'^/(?:api/)?w/(?:[a-f0-9]{32}|shared)(?=/(?:jobs|sheets|reviews|imports)/)', '', value)
        if re.match(r'^/(jobs|sheets|reviews|imports)/', value):
            return WORKSPACE_URL_PREFIX.get() + '/' + workspace['id'] + value
    return value


def split_workspace_path(path):
    match = re.match(r'^/(?:api/)?w/([a-f0-9]{32}|shared)(/.*)?$', path)
    return (match.group(1), match.group(2) or '/') if match else (None, path)


def tenant_route(path):
    if re.match(r'^/(jobs|sheets|reviews|imports)/', path):
        return True
    return path.startswith('/api/') and not any(path == prefix or path.startswith(prefix + '/') for prefix in (
        '/api/health', '/api/session', '/api/auth', '/api/admin', '/api/workspaces'))
