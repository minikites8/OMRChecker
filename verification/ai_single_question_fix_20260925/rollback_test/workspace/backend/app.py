"""FastAPI backend for the separated OMRChecker platform."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import parse_qs
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from starlette.concurrency import run_in_threadpool
from platform_admin import AdminError, AdminService, require_admin

from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from platform_auth import AuthError, AuthService
from platform_config import PlatformSettings
from runtime_settings import get_setting
from recognition_assets import validate_recognition_assets
from platform_cos import TencentCosStorage
from platform_database import PostgresStore
from platform_persistence import PlatformPersistence

import scan_ui as legacy


logger = logging.getLogger("omrchecker.api")


settings = PlatformSettings.from_env()
database = PostgresStore(settings.database_url)
cos = TencentCosStorage(settings)
persistence = PlatformPersistence(settings, database, cos)
auth = AuthService(settings, database)

app = FastAPI(title="OMRChecker API", version="0.1.0")
origins = [item.strip() for item in get_setting("FRONTEND_ORIGINS", "http://localhost:8765").split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def current_user(request: Request) -> dict:
    user = auth.user_from_headers(request.headers.get("cookie", ""))
    if auth.enabled and not user:
        raise HTTPException(status_code=401, detail="需要登录")
    return user or {"id": "local", "sub": "local", "email": "local@localhost", "role": "admin"}


def _review_error_response(request: Request, error: Exception) -> JSONResponse:
    """将复核失败转换为前端可读的 JSON，避免默认 HTML 500。"""
    if isinstance(error, ValueError):
        status = 400
        message = str(error)
        extra = {}
        logger.warning("复核请求校验失败 path=%s error=%s", request.url.path, message)
    elif isinstance(error, FileNotFoundError):
        status = 404
        message = str(error)
        extra = {}
        logger.warning("复核输入文件不存在 path=%s error=%s", request.url.path, message)
    elif isinstance(error, legacy.ScanFailure):
        status = 502
        message = str(error)
        extra = {"log_tail": str(getattr(error, "log", ""))[-12000:]}
        logger.error("复核扫描失败 path=%s error=%s", request.url.path, message)
    elif isinstance(error, legacy.ReviewDataUnavailable):
        status = 503
        message = str(error)
        extra = {"retryable": True}
        logger.warning("复核结果暂不可用 path=%s error=%s", request.url.path, message)
    else:
        status = 503
        message = "批改服务暂时失败，请稍后重试"
        extra = {}
        logger.exception("复核接口异常 path=%s", request.url.path)
    return JSONResponse(
        {"ok": False, "error": message, **extra},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _review_response(request: Request, operation) -> JSONResponse:
    try:
        return JSONResponse(jsonable_encoder(operation()))
    except Exception as error:
        return _review_error_response(request, error)


def public_user(user: dict | None) -> dict | None:
    if not user:
        return None
    return {
        "id": str(user.get("id") or user.get("sub") or ""),
        "email": str(user.get("email") or ""),
        "display_name": str(user.get("display_name") or ""),
        "role": str(user.get("role") or "teacher"),
        "auth_provider": str(user.get("auth_provider") or ""),
    }


def actor_payload(payload: dict, user: dict) -> dict:
    enriched = dict(payload or {})
    enriched["_actor_user_id"] = str(user.get("id") or user.get("sub") or "")
    return enriched


def oidc_redirect_uri() -> str:
    return settings.oidc_redirect_uri or settings.public_base_url + "/auth/oidc/callback"


@app.on_event("startup")
def startup() -> None:
    validate_recognition_assets(legacy.PROJECT_ROOT)
    auth.startup()
    persistence.startup()


@app.get("/api/health")
def health() -> dict:
    ai_settings = legacy.ai_config()
    return {
        "ok": True,
        "service": "OMRChecker API",
        "review_concurrency": legacy.DEFAULT_REVIEW_WORKERS,
        "recognition": legacy.recognition_settings(),
        "ai_judgment": {
            "configured": legacy.ai_is_configured(),
            "model": ai_settings["model"],
            "concurrency": ai_settings["concurrency"],
            "review_workers": legacy.DEFAULT_AI_WORKERS,
        },
        "platform": settings.public_config(),
        "persistence": persistence.health(),
    }


@app.get("/api/session")
def session(request: Request) -> dict:
    user = auth.user_from_headers(request.headers.get("cookie", ""))
    return {"ok": True, "authenticated": bool(user), "user": public_user(user), "auth": settings.public_config()}


@app.post("/api/auth/login")
def builtin_login(request: Request, payload: dict) -> JSONResponse:
    try:
        user = auth.login_builtin(payload.get("email", ""), payload.get("password", ""))
    except AuthError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    return JSONResponse(
        {"ok": True, "user": public_user(user)},
        headers={"Set-Cookie": auth.session_cookie(user)},
    )


@app.get("/auth/oidc/start")
def oidc_start() -> RedirectResponse:
    try:
        target, state_cookie = auth.begin_oidc(oidc_redirect_uri())
    except AuthError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    response = RedirectResponse(target, status_code=302)
    response.headers["Set-Cookie"] = state_cookie
    return response


@app.get("/auth/oidc/callback")
def oidc_callback(request: Request) -> RedirectResponse:
    query = parse_qs(request.url.query)
    code = (query.get("code") or [""])[0]
    state = (query.get("state") or [""])[0]
    try:
        user = auth.complete_oidc(code, state, request.headers.get("cookie", ""), oidc_redirect_uri())
    except AuthError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    response = RedirectResponse("/", status_code=302)
    response.headers["Set-Cookie"] = auth.session_cookie(user)
    return response


@app.get("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=302)
    response.headers["Set-Cookie"] = auth.clear_session_cookie()
    return response


def admin_result(request: Request, payload=None) -> JSONResponse:
    try:
        result = AdminService(settings, database, auth).execute(
            request.method, request.url.path, request.headers.get("cookie", ""),
            dict(request.query_params), payload,
        )
        return JSONResponse(jsonable_encoder(result), headers={"Cache-Control": "no-store"})
    except AdminError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=error.status, headers={"Cache-Control": "no-store"})
    except Exception:
        return JSONResponse({"ok": False, "error": "管理服务暂时繁忙，请稍后重试"}, status_code=503, headers={"Cache-Control": "no-store"})


@app.get("/api/admin/settings", tags=["admin"])
@app.get("/api/admin/overview", tags=["admin"])
@app.get("/api/admin/users", tags=["admin"])
@app.get("/api/admin/audit-events", tags=["admin"])
def admin_read(request: Request):
    return admin_result(request)


async def admin_write(request: Request):
    try:
        await run_in_threadpool(require_admin, auth, request.headers.get("cookie", ""))
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise AdminError(415, "请使用 application/json")
        limit = 65536 if request.url.path == "/api/admin/settings" else 16384
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > limit:
                raise AdminError(413, f"管理请求上限为 {limit // 1024} KB")
        import json
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise AdminError(400, "JSON 格式错误")
        return await run_in_threadpool(admin_result, request, payload)
    except AdminError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=error.status, headers={"Cache-Control": "no-store"})
    except Exception:
        return JSONResponse({"ok": False, "error": "管理服务暂时繁忙，请稍后重试"}, status_code=503, headers={"Cache-Control": "no-store"})


ADMIN_USER_PROPERTIES = {
    "email": {"type": "string", "format": "email", "maxLength": 254},
    "display_name": {"type": "string", "maxLength": 80},
    "role": {"type": "string", "enum": ["admin", "teacher"]},
    "is_active": {"type": "boolean"},
    "password": {"type": "string", "minLength": 12, "maxLength": 128, "writeOnly": True},
}


def admin_body_schema(creating=False):
    fields = ("email", "display_name", "role", "password") if creating else ("display_name", "role", "is_active", "password")
    schema = {"type": "object", "additionalProperties": False,
              "properties": {key: ADMIN_USER_PROPERTIES[key] for key in fields}}
    if creating:
        schema["required"] = ["email", "password"]
    else:
        schema["minProperties"] = 1
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": schema}}}}


@app.post("/api/admin/users", tags=["admin"], openapi_extra=admin_body_schema(True))
async def admin_create_user(request: Request):
    return await admin_write(request)


@app.patch("/api/admin/users/{user_id}", tags=["admin"], openapi_extra=admin_body_schema())
async def admin_update_user(user_id: str, request: Request):
    return await admin_write(request)


@app.patch("/api/admin/settings", tags=["admin"], openapi_extra={
    "requestBody": {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "required": ["revision"], "additionalProperties": False,
        "properties": {"revision": {"type": "integer", "minimum": 0},
                       "values": {"type": "object", "description": "Changed settings; secret values are write-only."},
                       "reset": {"type": "array", "items": {"type": "string"}}}
    }}}}})
async def admin_update_settings(request: Request):
    return await admin_write(request)


@app.get("/api/templates")
def templates(request: Request) -> dict:
    current_user(request)
    return legacy.TEMPLATE_MANAGER.list()


@app.get("/api/candidates")
def candidates(request: Request) -> dict:
    current_user(request)
    return legacy.CANDIDATE_MANAGER.list()


@app.get("/api/candidates/export.json")
def candidate_export(request: Request) -> JSONResponse:
    current_user(request)
    values = request.query_params.getlist("review_id") + request.query_params.getlist("review_ids")
    selected = [item.strip() for value in values for item in value.split(",") if item.strip()] if values else None
    response = _review_response(request, lambda: legacy.export_confirmed_grades(selected))
    response.headers["Cache-Control"] = "no-store"
    if response.status_code == 200:
        response.headers["Content-Disposition"] = 'attachment; filename="candidate-grades.json"'
    return response


@app.get("/api/exam/imports")
def exam_imports(request: Request) -> dict:
    current_user(request)
    return {"ok": True, "imports": legacy.list_exam_imports()}


@app.get("/api/review/objective-view")
def objective_view(request: Request, review_id: str = "") -> JSONResponse:
    current_user(request)
    return _review_response(request, lambda: legacy.read_objective_view(review_id))


@app.get("/api/review/status")
def review_status(request: Request, review_id: str = "") -> JSONResponse:
    current_user(request)
    return _review_response(request, lambda: legacy.read_review_status(review_id))


@app.get("/api/review/batch/status")
def batch_status(request: Request, batch_id: str = "") -> JSONResponse:
    current_user(request)
    return _review_response(request, lambda: legacy.read_batch_status(batch_id))


@app.post("/api/scan")
def scan(request: Request, payload: dict) -> dict:
    user = current_user(request)
    return legacy.run_scan_job(payload.get("files", []), False, payload.get("template_id"), str(user.get("id") or user.get("sub") or ""), local_ocr_enabled=payload.get("local_ocr_enabled"))


@app.post("/api/demo")
def demo(request: Request, payload: dict) -> dict:
    user = current_user(request)
    return legacy.run_scan_job([], True, payload.get("template_id"), str(user.get("id") or user.get("sub") or ""), local_ocr_enabled=payload.get("local_ocr_enabled"))


@app.post("/api/sheets")
def sheets(request: Request, payload: dict) -> dict:
    current_user(request)
    package = legacy.generate_sheet_package(payload, output_root=legacy.SHEETS_ROOT)
    return {
        "ok": True,
        "sheet_id": package["sheet_id"],
        "spec": package["spec"],
        "pdf_url": legacy.sheet_url(package["pdf_path"]),
        "template_url": legacy.sheet_url(package["template_path"]),
        "reference_url": legacy.sheet_url(package["reference_path"]),
        "package_url": legacy.sheet_url(package["package_path"]),
    }


@app.post("/api/exam/import")
def exam_import(request: Request, payload: dict) -> dict:
    user = current_user(request)
    try:
        return legacy.run_exam_import(actor_payload(payload, user))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/exam/delete")
def exam_delete(request: Request, payload: dict) -> dict:
    user = current_user(request)
    try:
        return legacy.delete_exam_import(actor_payload(payload, user))
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="试卷删除暂时失败，请稍后重试") from error


@app.post("/api/review")
def review(request: Request, payload: dict) -> JSONResponse:
    user = current_user(request)
    response = _review_response(request, lambda: legacy.start_review_job(actor_payload(payload, user)))
    if response.status_code == 200:
        response.status_code = 202
        response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/review/batch")
def review_batch(request: Request, payload: dict) -> JSONResponse:
    user = current_user(request)
    return _review_response(request, lambda: legacy.start_batch_review(actor_payload(payload, user)))


@app.post("/api/review/ai-judge")
def review_ai(request: Request, payload: dict) -> JSONResponse:
    current_user(request)
    return _review_response(request, lambda: legacy.start_ai_review(payload))


@app.post("/api/review/delete")
@app.post("/api/candidates/delete")
def review_delete(request: Request, payload: dict):
    user = current_user(request)
    try:
        return legacy.delete_review_job(payload, actor=user, enforce_ownership=auth.enabled)
    except legacy.ReviewDeletionError as error:
        return JSONResponse({"ok": False, "error": str(error), "deleted": error.deleted,
                             "review_id": payload.get("review_id")}, status_code=error.status)
    except Exception:
        return JSONResponse({"ok": False, "error": "删除暂时失败，请稍后重试"}, status_code=503)


@app.post("/api/review/confirm")
def review_confirm(request: Request, payload: dict) -> dict:
    current_user(request)
    return legacy.save_manual_review(payload)


@app.post("/api/review/confirm-grade")
def review_confirm_grade(request: Request, payload: dict) -> dict:
    current_user(request)
    return legacy.confirm_review_grade(payload)


@app.post("/api/candidates/save")
def candidate_save(request: Request, payload: dict) -> dict:
    current_user(request)
    return legacy.CANDIDATE_MANAGER.save(payload)


@app.post("/api/candidates/recognize")
def candidate_recognize(request: Request, payload: dict) -> dict:
    current_user(request)
    return legacy.CANDIDATE_MANAGER.start(payload)


@app.post("/api/templates/{action}")
def template_action(request: Request, action: str, payload: dict) -> dict:
    current_user(request)
    operations = {
        "create": legacy.TEMPLATE_MANAGER.create,
        "update": legacy.TEMPLATE_MANAGER.update,
        "activate": legacy.TEMPLATE_MANAGER.activate,
        "delete": legacy.TEMPLATE_MANAGER.delete,
    }
    if action not in operations:
        raise HTTPException(status_code=404, detail="接口不存在")
    return operations[action](payload)


@app.get("/{asset_type}/{asset_path:path}")
def artifact(asset_type: str, asset_path: str, request: Request):
    current_user(request)
    roots = {
        "jobs": legacy.JOBS_ROOT,
        "sheets": legacy.SHEETS_ROOT,
        "reviews": legacy.REVIEW_ROOT,
        "imports": legacy.IMPORT_ROOT,
    }
    root = roots.get(asset_type)
    if root is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        path = legacy.resolve_under(root, asset_path)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)
