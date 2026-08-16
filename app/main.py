from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.database import SessionLocal, initialize_database
from app.dependencies import current_user_from_request, role_home
from app.routers import auth, examiner, instructor, student, websockets
from app.services.exam_service import auto_end_scheduled_exams, auto_submit_expired_attempts
from app.web import render


settings = get_settings()


async def expiry_worker() -> None:
    while True:
        try:
            await asyncio.to_thread(expire_once)
        except Exception:
            # The next cycle retries; request handling remains available.
            pass
        await asyncio.sleep(10)


def expire_once() -> None:
    with SessionLocal() as db:
        auto_end_scheduled_exams(db)
        auto_submit_expired_attempts(db)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(initialize_database, SessionLocal)
    worker = asyncio.create_task(expiry_worker())
    yield
    worker.cancel()
    with suppress(asyncio.CancelledError):
        await worker


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=settings.session_cookie_name,
    max_age=settings.session_max_age_seconds,
    same_site="lax",
    https_only=settings.session_https_only,
)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)
app.include_router(auth.router)
app.include_router(student.router)
app.include_router(instructor.router)
app.include_router(examiner.router)
app.include_router(websockets.router)


@app.get("/")
def home(request: Request):
    with SessionLocal() as db:
        user = current_user_from_request(request, db)
        if user:
            return RedirectResponse(role_home(user.role), status_code=303)
    return render(request, "index.html", {"page_title": "Secure web-based exams"})


@app.get("/health")
def health():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unhealthy", "database": "unavailable"}, status_code=503)
    return {"status": "healthy", "database": "connected"}


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        return RedirectResponse("/auth/login", status_code=303)
    if exc.status_code == 403:
        return render(
            request,
            "errors/403.html",
            {"page_title": "Access denied"},
            status_code=403,
        )
    if exc.status_code == 404:
        return render(
            request,
            "errors/404.html",
            {"page_title": "Not found"},
            status_code=404,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
