"""OIDC and built-in login service for the OMRChecker HTTP UI."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from http.cookies import SimpleCookie
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from platform_config import PlatformSettings
from platform_database import PostgresStore


SESSION_COOKIE = "omr_session"
OIDC_STATE_COOKIE = "omr_oidc_state"


class AuthError(RuntimeError):
    pass


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _json_b64(payload: dict) -> str:
    return _b64(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _parse_cookie(header: str, name: str) -> str:
    cookie = SimpleCookie()
    try:
        cookie.load(header or "")
    except Exception:
        return ""
    morsel = cookie.get(name)
    return morsel.value if morsel else ""


class AuthService:
    def __init__(self, settings: PlatformSettings, database: PostgresStore):
        self.settings = settings
        self.database = database
        self._discovery: Optional[dict] = None
        self._discovery_expires_at = 0.0

    @property
    def enabled(self) -> bool:
        return self.settings.auth_enabled

    def startup(self) -> None:
        if not self.enabled:
            return
        errors = self.settings.validate_startup()
        if errors:
            raise AuthError("；".join(errors))
        self.database.ensure_schema()
        self.database.ensure_bootstrap_admin(
            self.settings.bootstrap_admin_email,
            self.settings.bootstrap_admin_password,
            self.settings.bootstrap_admin_password_hash,
        )

    def _sign(self, value: str) -> str:
        if not self.settings.session_secret:
            raise AuthError("OMR_SESSION_SECRET 未配置")
        return _b64(hmac.new(self.settings.session_secret.encode("utf-8"), value.encode("ascii"), hashlib.sha256).digest())

    def _pack(self, payload: dict) -> str:
        body = _json_b64(payload)
        return body + "." + self._sign(body)

    def _unpack(self, token: str) -> Optional[dict]:
        try:
            body, signature = token.split(".", 1)
            expected = self._sign(body)
            if not hmac.compare_digest(signature, expected):
                return None
            payload = json.loads(_unb64(body).decode("utf-8"))
            if int(payload.get("exp", 0)) < int(time.time()):
                return None
            return payload
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def cookie_header(self, name: str, value: str, max_age: int, secure: bool = False) -> str:
        flags = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if secure:
            flags.append("Secure")
        return "; ".join(flags)

    def session_cookie(self, user: dict) -> str:
        payload = {
            "sub": str(user.get("id", "")),
            "email": str(user.get("email", "")),
            "display_name": str(user.get("display_name", "")),
            "role": str(user.get("role", "teacher")),
            "auth_provider": str(user.get("auth_provider", "")),
            "exp": int(time.time()) + self.settings.session_ttl_seconds,
        }
        return self.cookie_header(
            SESSION_COOKIE,
            self._pack(payload),
            self.settings.session_ttl_seconds,
            secure=self.settings.public_base_url.startswith("https://"),
        )

    def clear_session_cookie(self) -> str:
        return self.cookie_header(SESSION_COOKIE, "", 0, secure=self.settings.public_base_url.startswith("https://"))

    def user_from_headers(self, cookie_header: str) -> Optional[dict]:
        if not self.enabled:
            return {"id": "local", "email": "local@localhost", "display_name": "本地用户", "role": "admin", "auth_provider": "local"}
        payload = self._unpack(_parse_cookie(cookie_header, SESSION_COOKIE))
        return payload

    def login_builtin(self, email: str, password: str) -> dict:
        if not self.settings.builtin_enabled:
            raise AuthError("内置登录未启用")
        user = self.database.verify_builtin_user(email.strip().lower(), password)
        if not user:
            raise AuthError("邮箱或密码错误")
        return dict(user)

    def _fetch_json(self, url: str, data: Optional[bytes] = None, headers: Optional[dict] = None) -> dict:
        request = Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
        try:
            with urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise AuthError(f"OIDC 请求失败：{error}") from error

    def _discovery_document(self) -> dict:
        if not self.settings.oidc_enabled:
            raise AuthError("OIDC 登录未启用")
        now = time.time()
        if self._discovery and now < self._discovery_expires_at:
            return self._discovery
        url = self.settings.oidc_issuer_url.rstrip("/") + "/.well-known/openid-configuration"
        self._discovery = self._fetch_json(url)
        self._discovery_expires_at = now + 300
        return self._discovery

    def begin_oidc(self, redirect_uri: str) -> tuple[str, str]:
        discovery = self._discovery_document()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = _b64(secrets.token_bytes(48))
        challenge = _b64(hashlib.sha256(verifier.encode("ascii")).digest())
        state_cookie = self.cookie_header(
            OIDC_STATE_COOKIE,
            self._pack({"state": state, "nonce": nonce, "verifier": verifier, "exp": int(time.time()) + 600}),
            600,
            secure=self.settings.public_base_url.startswith("https://"),
        )
        params = {
            "response_type": "code",
            "client_id": self.settings.oidc_client_id,
            "redirect_uri": redirect_uri,
            "scope": self.settings.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return discovery["authorization_endpoint"] + "?" + urlencode(params), state_cookie

    def complete_oidc(self, code: str, state: str, cookie_header: str, redirect_uri: str) -> dict:
        state_payload = self._unpack(_parse_cookie(cookie_header, OIDC_STATE_COOKIE))
        if not state_payload or not hmac.compare_digest(str(state_payload.get("state", "")), state):
            raise AuthError("OIDC 状态校验失败")
        discovery = self._discovery_document()
        token_payload = urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.settings.oidc_client_id,
            "client_secret": self.settings.oidc_client_secret,
            "code_verifier": state_payload["verifier"],
        }).encode("utf-8")
        token = self._fetch_json(
            discovery["token_endpoint"],
            data=token_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        claims = self._verify_id_token(token.get("id_token", ""), state_payload.get("nonce", ""), discovery)
        if not claims:
            userinfo_endpoint = discovery.get("userinfo_endpoint")
            if not userinfo_endpoint or not token.get("access_token"):
                raise AuthError("OIDC 响应缺少用户身份信息")
            claims = self._fetch_json(
                userinfo_endpoint,
                headers={"Authorization": "Bearer " + token["access_token"]},
            )
        subject = str(claims.get("sub", "")).strip()
        email = str(claims.get("email", "")).strip().lower()
        if not subject or not email:
            raise AuthError("OIDC 用户缺少 sub 或 email claim")
        display_name = str(claims.get("name") or claims.get("preferred_username") or email).strip()
        return dict(self.database.upsert_oidc_user(
            self.settings.oidc_issuer_url,
            subject,
            email,
            display_name,
            self.settings.default_oidc_role,
        ))

    def _verify_id_token(self, token: str, nonce: str, discovery: dict) -> Optional[dict]:
        if not token:
            return None
        try:
            import jwt
            if not discovery.get("jwks_uri"):
                raise AuthError("OIDC discovery 缺少 jwks_uri")
            key = jwt.PyJWKClient(discovery["jwks_uri"]).get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                audience=self.settings.oidc_client_id,
                issuer=self.settings.oidc_issuer_url,
                options={"require": ["exp", "iat", "sub"]},
            )
            if nonce and claims.get("nonce") != nonce:
                raise AuthError("OIDC nonce 校验失败")
            return claims
        except AuthError:
            raise
        except Exception as error:
            raise AuthError(f"OIDC ID Token 校验失败：{error}") from error
