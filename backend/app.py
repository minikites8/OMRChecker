"""FastAPI backend for the separated OMRChecker platform."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from platform_auth import AuthError, AuthService
from platform_config import PlatformSettings
from platform_cos import TencentCosStorage
from platform_database import PostgresStore
from platform_persistence import PlatformPersistence

import scan_ui as legacy


settings = PlatformSettings.from_env()
database = PostgresStore(settings.database_url)
cos = TencentCosStorage(settings)
persistence = PlatformPersistence(settings, database, cos)
auth = AuthService(settings, database)

app = FastAPI(title="OMRChecker API", version="0.1.0")
origins = [item.strip() for item in os.environ.get("FRONTEND_ORIGINS", "http://localhost:8765").split(",") if item.strip()]
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


@app.get("/api/templates")
def templates(request: Request) -> dict:
    current_user(request)
    return legacy.TEMPLATE_MANAGER.list()


@app.get("/api/candidates")
def candidates(request: Request) -> dict:
    current_user(request)
    return legacy.CANDIDATE_MANAGER.list()


@app.get("/api/candidates/export.json")
def candidate_export(request: Request, review_id: Optional[List[str]] = None, review_ids: Optional[List[str]] = None) -> JSONResponse:
    current_user(request)
    selected = (review_id or []) + (review_ids or [])
    selected = [item for value in selected for item in str(value).split(",") if item.strip()]
    return JSONResponse(legacy.export_confirmed_grades(selected or None))


@app.get("/api/exam/imports")
def exam_imports(request: Request) -> dict:
    current_user(request)
    return {"ok": True, "imports": legacy.list_exam_imports()}


@app.get("/api/review/objective-view")
def objective_view(request: Request, review_id: str = "") -> dict:
    current_user(request)
    return legacy.read_objective_view(review_id)


@app.get("/api/review/status")
def review_status(request: Request, review_id: str = "") -> dict:
    current_user(request)
    return legacy.read_review_status(review_id)


@app.get("/api/review/batch/status")
def batch_status(request: Request, batch_id: str = "") -> dict:
    current_user(request)
    return legacy.read_batch_status(batch_id)


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
def review(request: Request, payload: dict) -> dict:
    user = current_user(request)
    return legacy.run_review_job(actor_payload(payload, user))


@app.post("/api/review/batch")
def review_batch(request: Request, payload: dict) -> dict:
    user = current_user(request)
    return legacy.start_batch_review(actor_payload(payload, user))


@app.post("/api/review/ai-judge")
def review_ai(request: Request, payload: dict) -> dict:
    current_user(request)
    return legacy.start_ai_review(payload)


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
