from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import current_user_from_request, role_home
from app.models import User, UserRole
from app.security import (
    hash_password,
    normalize_username,
    validate_csrf,
    validate_password_strength,
    verify_password,
)
from app.web import flash, render


router = APIRouter(prefix="/auth", tags=["authentication"])
settings = get_settings()


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    user = current_user_from_request(request, db)
    if user:
        return RedirectResponse(role_home(user.role), status_code=303)
    return render(request, "auth/login.html", {"page_title": "Sign in"})


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    normalized = normalize_username(username)
    user = db.scalar(select(User).where(User.username == normalized))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return render(
            request,
            "auth/login.html",
            {
                "page_title": "Sign in",
                "error": "Incorrect username or password.",
                "username": normalized,
            },
            status_code=400,
        )
    request.session.clear()
    request.session["user_id"] = user.id
    flash(request, f"Welcome back, {user.full_name}.", "success")
    return RedirectResponse(role_home(user.role), status_code=303)


@router.get("/register")
def register_page(request: Request, db: Session = Depends(get_db)):
    user = current_user_from_request(request, db)
    if user:
        return RedirectResponse(role_home(user.role), status_code=303)
    return render(
        request,
        "auth/register.html",
        {
            "page_title": "Create account",
            "allow_instructor_registration": settings.allow_instructor_registration,
        },
    )


@router.post("/register")
def register(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("student"),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    allowed_roles = {UserRole.STUDENT}
    if settings.allow_instructor_registration:
        allowed_roles.add(UserRole.INSTRUCTOR)
    try:
        selected_role = UserRole(role)
    except ValueError:
        selected_role = UserRole.STUDENT
    if selected_role not in allowed_roles:
        selected_role = UserRole.STUDENT

    normalized = normalize_username(username)
    error = validate_password_strength(password)
    if not full_name.strip():
        error = "Full name is required."
    elif not normalized or len(normalized) > 80 or any(char.isspace() for char in normalized):
        error = "Username must be 1–80 characters and cannot contain spaces."
    if error:
        return render(
            request,
            "auth/register.html",
            {
                "page_title": "Create account",
                "error": error,
                "full_name": full_name,
                "username": normalized,
                "selected_role": selected_role.value,
                "allow_instructor_registration": settings.allow_instructor_registration,
            },
            status_code=400,
        )

    user = User(
        full_name=full_name.strip(),
        username=normalized,
        password_hash=hash_password(password),
        role=selected_role,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "auth/register.html",
            {
                "page_title": "Create account",
                "error": "That username is already in use.",
                "full_name": full_name,
                "username": normalized,
                "selected_role": selected_role.value,
                "allow_instructor_registration": settings.allow_instructor_registration,
            },
            status_code=409,
        )
    request.session.clear()
    request.session["user_id"] = user.id
    flash(request, "Your account is ready.", "success")
    return RedirectResponse(role_home(user.role), status_code=303)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/", status_code=303)

