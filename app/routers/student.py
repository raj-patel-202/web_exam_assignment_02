from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.main import render
from app.models import (
    AttemptStatus,
    Exam,
    ExamAttempt,
    ExamStatus,
    ExamType,
    UserRole,
)
from app.security import current_user_from_request, require_role, validate_csrf
from app.services.analytics import attempt_review, exam_analysis
from app.services.exam_service import (
    AttemptRuleError,
    activate_attempt,
    attempt_payload,
    aware_utc,
    can_review_attempt,
    create_or_resume_attempt,
    exam_availability,
    exam_time_status,
    get_attempt_for_student,
    save_response,
    submit_attempt,
)
from app.services.live_monitor import attempt_summary, manager

router = APIRouter(prefix="/student", tags=["student"])


class SaveResponsePayload(BaseModel):
    question_id: int
    selected_option_id: int | None = None
    time_spent_seconds: int = Field(ge=0)
    current_position: int = Field(ge=0)


def student_user(request: Request, db: Session):
    return require_role(current_user_from_request(request, db), UserRole.STUDENT)


@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = student_user(request, db)
    active_attempt_count = int(
        db.scalar(
            select(func.count(ExamAttempt.id)).join(Exam).where(
                ExamAttempt.student_id == user.id,
                ExamAttempt.status == AttemptStatus.IN_PROGRESS,
                Exam.exam_type == ExamType.SCHEDULED,
            )
        )
        or 0
    )
    result_count = int(
        db.scalar(
            select(func.count(ExamAttempt.id)).join(Exam).where(
                ExamAttempt.student_id == user.id,
                ExamAttempt.status != AttemptStatus.IN_PROGRESS,
                Exam.exam_type == ExamType.SCHEDULED,
            )
        )
        or 0
    )
    available_exam_count = int(
        db.scalar(
            select(func.count(Exam.id)).where(
                Exam.status == ExamStatus.PUBLISHED,
                Exam.exam_type == ExamType.SCHEDULED,
            )
        )
        or 0
    )
    return render(
        request,
        "student/dashboard.html",
        {
            "page_title": "Student dashboard",
            "user": user,
            "active_attempt_count": active_attempt_count,
            "result_count": result_count,
            "available_exam_count": available_exam_count,
        },
    )


@router.get("/exams")
def exams(request: Request, db: Session = Depends(get_db)):
    user = student_user(request, db)
    attempts = list(
        db.scalars(select(ExamAttempt).where(ExamAttempt.student_id == user.id))
    )
    active_exam_ids = {
        attempt.exam_id
        for attempt in attempts
        if attempt.status == AttemptStatus.IN_PROGRESS
    }
    exam_rows = list(
        db.scalars(
            select(Exam)
            .where(
                Exam.exam_type == ExamType.SCHEDULED,
                or_(
                    Exam.status == ExamStatus.PUBLISHED,
                    Exam.id.in_(active_exam_ids),
                )
            )
            .order_by(Exam.start_at.asc().nullsfirst(), Exam.created_at.desc())
            .options(selectinload(Exam.creator))
        )
    )
    by_exam: dict[int, list[ExamAttempt]] = {}
    for attempt in attempts:
        by_exam.setdefault(attempt.exam_id, []).append(attempt)
    cards = [
        {
            "exam": exam,
            "availability": exam_availability(exam, by_exam.get(exam.id, [])),
            "attempt_count": len(by_exam.get(exam.id, [])),
            "start_at_iso": aware_utc(exam.start_at).isoformat() if exam.start_at else "",
            "start_countdown": exam_time_status(exam),
        }
        for exam in exam_rows
    ]
    return render(
        request,
        "student/exams.html",
        {"page_title": "Available exams", "user": user, "exam_cards": cards},
    )


@router.post("/exams/{exam_id}/start")
def start_exam(
    exam_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = student_user(request, db)
    validate_csrf(request, request.headers.get("x-csrf-token"))
    exam = db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404)
    try:
        attempt, created = create_or_resume_attempt(db, exam, user)
    except AttemptRuleError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    return {
        "ok": True,
        "created": created,
        "redirect_url": f"/student/attempts/{attempt.id}",
    }


@router.get("/attempts/{attempt_id}")
def take_exam(attempt_id: int, request: Request, db: Session = Depends(get_db)):
    user = student_user(request, db)
    attempt = get_attempt_for_student(db, attempt_id, user.id)
    if not attempt:
        raise HTTPException(status_code=404)
    if attempt.status != AttemptStatus.IN_PROGRESS:
        return RedirectResponse(f"/student/attempts/{attempt.id}/result", status_code=303)
    if attempt.expires_at and datetime.now(timezone.utc) >= aware_utc(attempt.expires_at):
        submit_attempt(db, attempt, auto=True)
        return RedirectResponse(f"/student/attempts/{attempt.id}/result", status_code=303)

    questions = attempt_payload(attempt)
    exam_config = {
        "attemptId": attempt.id,
        "activated": attempt.activated_at is not None,
        "expiresAt": aware_utc(attempt.expires_at).isoformat() if attempt.expires_at else None,
        "activateUrl": f"/student/attempts/{attempt.id}/activate",
        "saveUrl": f"/student/attempts/{attempt.id}/save",
        "submitUrl": f"/student/attempts/{attempt.id}/submit",
        "socketUrl": f"/ws/student/attempts/{attempt.id}",
        "resultUrl": f"/student/attempts/{attempt.id}/result",
        "csrfToken": request.session.get("csrf_token"),
        "currentPosition": attempt.current_question_position,
    }
    return render(
        request,
        "student/take_exam.html",
        {
            "page_title": attempt.exam.name,
            "user": user,
            "attempt": attempt,
            "questions": questions,
            "exam_config": exam_config,
        },
    )


@router.post("/attempts/{attempt_id}/activate")
def activate(
    attempt_id: int,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = student_user(request, db)
    validate_csrf(request, x_csrf_token)
    attempt = get_attempt_for_student(db, attempt_id, user.id)
    if not attempt:
        raise HTTPException(status_code=404)
    try:
        attempt = activate_attempt(db, attempt)
    except AttemptRuleError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    manager.broadcast_exam(
        attempt.exam_id, {"type": "attempt_update", "attempt": attempt_summary(attempt)}
    )
    return {
        "ok": True,
        "activated_at": aware_utc(attempt.activated_at).isoformat(),
        "expires_at": aware_utc(attempt.expires_at).isoformat(),
    }


@router.post("/attempts/{attempt_id}/save")
def autosave(
    attempt_id: int,
    payload: SaveResponsePayload,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = student_user(request, db)
    validate_csrf(request, x_csrf_token)
    attempt = get_attempt_for_student(db, attempt_id, user.id)
    if not attempt:
        raise HTTPException(status_code=404)
    try:
        save_response(db, attempt, **payload.model_dump())
    except AttemptRuleError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    manager.broadcast_exam(
        attempt.exam_id, {"type": "attempt_update", "attempt": attempt_summary(attempt)}
    )
    return {"ok": True, "saved_at": datetime.now(timezone.utc).isoformat()}


@router.post("/attempts/{attempt_id}/submit")
def submit(
    attempt_id: int,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user = student_user(request, db)
    validate_csrf(request, x_csrf_token)
    attempt = get_attempt_for_student(db, attempt_id, user.id)
    if not attempt:
        raise HTTPException(status_code=404)
    try:
        submit_attempt(db, attempt)
    except AttemptRuleError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    manager.broadcast_exam(
        attempt.exam_id, {"type": "attempt_update", "attempt": attempt_summary(attempt)}
    )
    return {"ok": True, "redirect_url": f"/student/attempts/{attempt.id}/result"}


@router.get("/results")
def results(request: Request, db: Session = Depends(get_db)):
    user = student_user(request, db)
    attempts = list(
        db.scalars(
            select(ExamAttempt)
            .join(Exam)
            .where(
                ExamAttempt.student_id == user.id,
                ExamAttempt.status != AttemptStatus.IN_PROGRESS,
                Exam.exam_type == ExamType.SCHEDULED,
            )
            .order_by(ExamAttempt.submitted_at.desc())
            .options(selectinload(ExamAttempt.exam))
        )
    )
    result_rows = [
        {"attempt": attempt, "reveal_answers": can_review_attempt(attempt)}
        for attempt in attempts
    ]
    return render(
        request,
        "student/results.html",
        {
            "page_title": "Results",
            "user": user,
            "result_rows": result_rows,
        },
    )


@router.get("/attempts/{attempt_id}/result")
def result(attempt_id: int, request: Request, db: Session = Depends(get_db)):
    user = student_user(request, db)
    attempt = get_attempt_for_student(db, attempt_id, user.id)
    if not attempt:
        raise HTTPException(status_code=404)
    if attempt.status == AttemptStatus.IN_PROGRESS:
        if attempt.expires_at and datetime.now(timezone.utc) >= aware_utc(attempt.expires_at):
            submit_attempt(db, attempt, auto=True)
        else:
            return RedirectResponse(f"/student/attempts/{attempt.id}", status_code=303)
    reveal = can_review_attempt(attempt)
    review = attempt_review(attempt, reveal)
    analysis = exam_analysis(db, attempt.exam) if reveal else None
    return render(
        request,
        "student/result.html",
        {
            "page_title": "Attempt result",
            "user": user,
            "attempt": attempt,
            "review": review,
            "analysis": analysis,
            "reveal_answers": reveal,
        },
    )
