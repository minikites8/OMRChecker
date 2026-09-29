"""Workspace membership and hashed, expiring invitations for both HTTP servers."""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlparse


class WorkspaceError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


SCHEMA = """
CREATE TABLE IF NOT EXISTS app_workspaces (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_user_id TEXT NOT NULL,
    created_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_workspace_members (
    workspace_id TEXT NOT NULL REFERENCES app_workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL, role TEXT NOT NULL, display_name TEXT NOT NULL,
    joined_at BIGINT NOT NULL, PRIMARY KEY(workspace_id, user_id)
);
CREATE TABLE IF NOT EXISTS app_workspace_invitations (
    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES app_workspaces(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL UNIQUE, token_hash TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL, expires_at BIGINT NOT NULL, max_uses INTEGER NOT NULL,
    uses INTEGER NOT NULL DEFAULT 0, revoked_at BIGINT, created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS workspace_member_user ON app_workspace_members(user_id);
CREATE INDEX IF NOT EXISTS workspace_invitation_workspace ON app_workspace_invitations(workspace_id);
"""


def user_id(user):
    identifier = str((user or {}).get('id') or (user or {}).get('sub') or '')
    if not identifier:
        raise WorkspaceError(401, '请先登录账号')
    return identifier


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class WorkspaceStore:
    """PostgreSQL in deployments; transactional SQLite for local installations."""
    def __init__(self, data_root, database=None, public_base_url=''):
        self.root = Path(data_root)
        self.database = database
        self.public_base_url = public_base_url.rstrip('/')
        self._ready = False
        self._schema_lock = threading.Lock()

    @property
    def postgres(self):
        return bool(self.database and self.database.configured)

    @contextmanager
    def connection(self, write=False):
        if self.postgres:
            with self.database.connection() as connection:
                yield connection
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.root / 'workspaces.sqlite3', timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            try:
                connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def execute(self, connection, query, params=()):
        return connection.execute(query if self.postgres else query.replace('%s', '?'), params)

    def ensure_schema(self):
        with self._schema_lock:
            if self._ready:
                return
            with self.connection(write=True) as connection:
                for statement in SCHEMA.split(';'):
                    if statement.strip():
                        self.execute(connection, statement)
            self._ready = True

    def _member(self, connection, workspace_id, uid, owner=False):
        row = self.execute(connection, '''SELECT w.*, m.role FROM app_workspaces w
            JOIN app_workspace_members m ON m.workspace_id=w.id
            WHERE w.id=%s AND m.user_id=%s''' + (' FOR UPDATE OF w' if owner and self.postgres else ''), (workspace_id, uid)).fetchone()
        if not row:
            raise WorkspaceError(404, '工作区不存在或尚未加入')
        result = dict(row)
        if owner and result['role'] != 'owner':
            raise WorkspaceError(403, '请由工作区所有者管理邀请')
        return result

    def require_member(self, workspace_id, user, owner=False):
        self.ensure_schema()
        if not re.fullmatch(r'(?:[a-f0-9]{32}|shared)', str(workspace_id)):
            raise WorkspaceError(404, '工作区不存在')
        with self.connection() as connection:
            return self._member(connection, workspace_id, user_id(user), owner)

    def list(self, user):
        self.ensure_schema()
        with self.connection() as connection:
            rows = self.execute(connection, '''SELECT w.*, m.role,
                (SELECT COUNT(*) FROM app_workspace_members x WHERE x.workspace_id=w.id) AS member_count
                FROM app_workspaces w JOIN app_workspace_members m ON m.workspace_id=w.id
                WHERE m.user_id=%s ORDER BY w.created_at, w.id''', (user_id(user),)).fetchall()
            return [dict(row) for row in rows]

    def create(self, user, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise WorkspaceError(400, '工作区名称需填写 1–80 个字符')
        uid = user_id(user)
        workspace = {'id': uuid.uuid4().hex, 'name': name.strip(), 'owner_user_id': uid,
                     'created_at': int(time.time()), 'role': 'owner', 'member_count': 1}
        self.ensure_schema()
        with self.connection(write=True) as connection:
            self.execute(connection, 'INSERT INTO app_workspaces(id,name,owner_user_id,created_at) VALUES(%s,%s,%s,%s)',
                         (workspace['id'], workspace['name'], uid, workspace['created_at']))
            self._add_member(connection, workspace['id'], user, 'owner')
        return workspace

    def _add_member(self, connection, workspace_id, user, role='member'):
        self.execute(connection, '''INSERT INTO app_workspace_members(workspace_id,user_id,role,display_name,joined_at)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(workspace_id,user_id) DO NOTHING''',
            (workspace_id, user_id(user), role, str(user.get('display_name') or user.get('email') or '工作区成员'), int(time.time())))

    def migrate_shared(self, users):
        """Snapshot existing users once; later accounts join via invitations."""
        if not users:
            return
        self.ensure_schema()
        owner = next((user for user in users if user.get('role') == 'admin'), users[0])
        with self.connection(write=True) as connection:
            inserted = self.execute(connection, '''INSERT INTO app_workspaces(id,name,owner_user_id,created_at)
                VALUES(%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING''',
                ('shared', '原有阅卷工作区', user_id(owner), int(time.time())))
            if inserted.rowcount:
                for user in users:
                    self._add_member(connection, 'shared', user, 'owner' if user_id(user) == user_id(owner) else 'member')

    def details(self, user, workspace_id):
        self.ensure_schema()
        with self.connection() as connection:
            result = self._member(connection, workspace_id, user_id(user))
            members = self.execute(connection, '''SELECT user_id,role,display_name,joined_at
                FROM app_workspace_members WHERE workspace_id=%s ORDER BY joined_at,user_id''', (workspace_id,)).fetchall()
            return {'workspace': result, 'members': [dict(row) for row in members]}

    def invite(self, user, workspace_id, payload):
        self.ensure_schema()
        days, max_uses = payload.get('expires_in_days', 7), payload.get('max_uses', 100)
        if type(days) is not int or not 1 <= days <= 30 or type(max_uses) is not int or not 1 <= max_uses <= 1000:
            raise WorkspaceError(400, '有效期为 1–30 天，使用次数为 1–1000 次')
        code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(12))
        token, now, identifier = secrets.token_urlsafe(32), int(time.time()), uuid.uuid4().hex
        with self.connection(write=True) as connection:
            self._member(connection, workspace_id, user_id(user), owner=True)
            self.execute(connection, '''UPDATE app_workspace_invitations SET revoked_at=%s
                WHERE workspace_id=%s AND revoked_at IS NULL''', (now, workspace_id))
            self.execute(connection, '''INSERT INTO app_workspace_invitations
                (id,workspace_id,code_hash,token_hash,created_by,expires_at,max_uses,created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
                (identifier, workspace_id, digest(code), digest(token), user_id(user), now + days * 86400, max_uses, now))
        return {'id': identifier, 'code': '-'.join(code[i:i+4] for i in range(0,12,4)),
                'invite_path': '/join/' + token, 'invite_url': self.public_base_url + '/join/' + token,
                'expires_at': now + days * 86400, 'max_uses': max_uses}

    def revoke(self, user, workspace_id):
        self.ensure_schema()
        with self.connection(write=True) as connection:
            self._member(connection, workspace_id, user_id(user), owner=True)
            self.execute(connection, '''UPDATE app_workspace_invitations SET revoked_at=%s
                WHERE workspace_id=%s AND revoked_at IS NULL''', (int(time.time()), workspace_id))
        return {'revoked': True}

    def join(self, user, value):
        uid = user_id(user)
        if not isinstance(value, str) or not value.strip() or len(value) > 2048:
            raise WorkspaceError(400, '请填写工作区邀请码或邀请链接')
        value = value.strip()
        if '/' in value:
            match = re.fullmatch(r'/join/([A-Za-z0-9_-]{43})/?', unquote(urlparse(value).path))
            if not match:
                raise WorkspaceError(400, '请使用完整的工作区邀请链接')
            value = match.group(1)
        code = re.sub(r'[\s-]', '', value).upper()
        self.ensure_schema()
        with self.connection(write=True) as connection:
            suffix = ' FOR UPDATE' if self.postgres else ''
            row = self.execute(connection, '''SELECT * FROM app_workspace_invitations
                WHERE code_hash=%s OR token_hash=%s''' + suffix, (digest(code), digest(value))).fetchone()
            now = int(time.time())
            if not row or row['revoked_at'] is not None or row['expires_at'] <= now:
                raise WorkspaceError(400, '邀请已过期或已撤销，请联系工作区所有者获取新邀请')
            existing = self.execute(connection, 'SELECT role FROM app_workspace_members WHERE workspace_id=%s AND user_id=%s',
                                    (row['workspace_id'], uid)).fetchone()
            if not existing:
                if row['uses'] >= row['max_uses']:
                    raise WorkspaceError(400, '邀请名额已用完，请联系工作区所有者')
                self._add_member(connection, row['workspace_id'], user)
                self.execute(connection, 'UPDATE app_workspace_invitations SET uses=uses+1 WHERE id=%s', (row['id'],))
            return self._member(connection, row['workspace_id'], uid)

    def dispatch(self, method, path, user, payload=None):
        payload = payload or {}
        if not isinstance(payload, dict):
            raise WorkspaceError(400, '请求内容需使用 JSON 对象')
        if path == '/api/workspaces':
            if method == 'GET':
                return {'ok': True, 'workspaces': self.list(user)}
            if method == 'POST':
                return {'ok': True, 'workspace': self.create(user, payload.get('name'))}
        if method == 'POST' and path == '/api/workspaces/join':
            return {'ok': True, 'workspace': self.join(user, payload.get('invite'))}
        match = re.fullmatch(r'/api/workspaces/([a-f0-9]{32}|shared)(/invitations(?:/revoke)?)?', path)
        if match:
            wid, action = match.groups()
            if method == 'GET' and not action:
                return {'ok': True, **self.details(user, wid)}
            if method == 'POST' and action == '/invitations':
                return {'ok': True, 'invitation': self.invite(user, wid, payload)}
            if method == 'POST' and action == '/invitations/revoke':
                return {'ok': True, **self.revoke(user, wid)}
        raise WorkspaceError(404, '工作区接口不存在')
