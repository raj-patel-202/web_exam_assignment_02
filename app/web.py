from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.security import ensure_csrf_token


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


def mark_value(value: Decimal | float | int | None, signed: bool = False) -> str:
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
