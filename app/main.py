from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.security import ensure_csrf_token

import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.database import SessionLocal, initialize_database

from app.security import current_user_from_request, role_home
from app.services.exam_service import (
    auto_end_scheduled_exams,
    auto_submit_expired_attempts,
)

settings = get_settings()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def local_datetime(value: datetime | None, format_string: str = "%d %b %Y, %I:%M %p") -> str:
    utc_value = as_utc(value)
    if utc_value is None:
        return "—"
    return utc_value.astimezone(ZoneInfo(settings.app_timezone)).strftime(format_string)


def human_seconds(seconds: int | None) -> str:
    value = max(0, int(seconds or 0))
    minutes, seconds = divmod(value, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def mark_value(value: Decimal | float | None, signed: bool = False) -> str:
    if value is None:
        return "—"
    number = Decimal(str(value))
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"", "-0"}:
        rendered = "0"
    if signed and number > 0:
        return f"+{rendered}"
    return rendered


templates.env.filters["local_datetime"] = local_datetime
templates.env.filters["human_seconds"] = human_seconds
templates.env.filters["mark"] = mark_value
templates.env.filters["signed_mark"] = lambda value: mark_value(value, signed=True)


def flash(request: Request, message: str, category: str = "info") -> None:
    request.session["flash"] = {"message": message, "category": category}


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    values = dict(context or {})
    values.update(
        {
            "request": request,
            "csrf_token": ensure_csrf_token(request),
            "flash": request.session.pop("flash", None),
            "app_name": settings.app_name,
            "app_timezone": settings.app_timezone,
        }
    )
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=values,
        status_code=status_code,
    )


def expiry_worker() -> None:
    import time
    while True:
        try:
            expire_once()
        except Exception:
            # The next cycle retries; request handling remains available.
            pass
        time.sleep(10)


def expire_once() -> None:
    with SessionLocal() as db:
        auto_end_scheduled_exams(db)
        auto_submit_expired_attempts(db)





app = FastAPI(title=settings.app_name, debug=settings.debug)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=settings.session_cookie_name,
    max_age=settings.session_max_age_seconds,
    same_site="lax",
    https_only=settings.session_https_only,
)

@app.on_event("startup")
def on_startup():
    initialize_database(SessionLocal)
    worker = threading.Thread(target=expiry_worker, daemon=True)
    worker.start()
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)
from app.routers import auth, examiner, instructor, student, websockets  # noqa: E402
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
def http_error(request: Request, exc: HTTPException):
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
