from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.dependencies import current_user_from_request, require_role
from app.models import (
    AuditLog,
    Exam,
    ExamAttempt,
    ExaminerAssignment,
    ExamStatus,
    ExamType,
    AttemptStatus,
    Question,
    Response,
    User,
    UserRole,
)
from app.security import (
    hash_password,
    normalize_username,
    validate_csrf,
    validate_password_strength,
)
from app.services.analytics import attempt_review, exam_analysis
from app.services.exam_parser import ExamParseError, decode_exam_upload, parse_exam_text
from app.services.exam_service import create_exam
from app.services.live_monitor import manager
from app.web import flash, render


router = APIRouter(prefix="/instructor", tags=["instructor"])
settings = get_settings()


def current_local_iso() -> str:
    return datetime.now(ZoneInfo(settings.app_timezone)).strftime("%Y-%m-%dT%H:%M")


def instructor_user(request: Request, db: Session):
    return require_role(current_user_from_request(request, db), UserRole.INSTRUCTOR)


def owned_exam(db: Session, exam_id: int, instructor_id: int) -> Exam:
    exam = db.scalar(
        select(Exam)
        .where(Exam.id == exam_id, Exam.created_by_id == instructor_id)
        .options(
            selectinload(Exam.questions).selectinload(Question.options),
            selectinload(Exam.examiner_assignments).selectinload(
                ExaminerAssignment.examiner
            ),
        )
    )
    if not exam:
        raise HTTPException(status_code=404)
    return exam


@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = instructor_user(request, db)
    exams = list(
        db.scalars(
            select(Exam)
            .where(Exam.created_by_id == user.id)
            .order_by(Exam.created_at.desc())
            .options(selectinload(Exam.attempts))
        )
    )
    examiners = list(
        db.scalars(
            select(User)
            .where(User.role == UserRole.EXAMINER, User.is_active.is_(True))
            .order_by(User.full_name)
            .options(
                selectinload(User.examiner_assignments).selectinload(
                    ExaminerAssignment.exam
                )
            )
        )
    )
    return render(
        request,
        "instructor/dashboard.html",
        {
            "page_title": "Instructor dashboard",
            "user": user,
            "exams": exams,
            "examiners": examiners,
        },
    )


@router.get("/exams/new")
def new_exam_page(request: Request, db: Session = Depends(get_db)):
    user = instructor_user(request, db)
    return render(
        request,
        "instructor/new_exam.html",
        {
            "page_title": "Upload exam",
            "user": user,
            "current_local_iso": current_local_iso(),
        },
    )


@router.post("/exams/new")
async def new_exam(
    request: Request,
    name: str = Form(...),
    exam_type: str = Form(...),
    duration_minutes: int = Form(...),
    join_grace_minutes: int = Form(5),
    positive_marks: Decimal = Form(Decimal("4.00")),
    negative_marks: Decimal = Form(Decimal("-1.00")),
    start_at: str = Form(""),
    csrf_token: str = Form(...),
    exam_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = instructor_user(request, db)
    validate_csrf(request, csrf_token)
    form_values = {
        "name": name,
        "exam_type": exam_type,
        "duration_minutes": duration_minutes,
        "join_grace_minutes": join_grace_minutes,
        "positive_marks": str(positive_marks),
        "negative_marks": str(negative_marks),
        "start_at": start_at,
    }
    error: str | None = None
    quantized_positive: Decimal | None = None
    quantized_negative: Decimal | None = None
    if positive_marks.is_finite() and negative_marks.is_finite():
        try:
            quantized_positive = positive_marks.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            quantized_negative = negative_marks.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        except InvalidOperation:
            pass
    try:
        selected_type = ExamType(exam_type)
    except ValueError:
        selected_type = ExamType.PRACTICE
        error = "Choose a valid exam type."

    if not name.strip() or len(name.strip()) > 160:
        error = "Exam name is required and must not exceed 160 characters."
    elif duration_minutes < 1 or duration_minutes > 720:
        error = "Duration must be between 1 and 720 minutes."
    elif join_grace_minutes < 0 or join_grace_minutes > 120:
        error = "Joining grace period must be between 0 and 120 minutes."
    elif quantized_positive is None or quantized_positive <= 0 or quantized_positive > Decimal("1000"):
        error = "Positive marks must be greater than 0 and no more than 1000."
    elif quantized_negative is None or quantized_negative > 0 or quantized_negative < Decimal("-1000"):
        error = "Negative marks must be between -1000 and 0."
    elif Path(exam_file.filename or "").suffix.lower() != ".txt":
        error = "Upload a .txt exam file."

    scheduled_start: datetime | None = None
    if selected_type == ExamType.SCHEDULED and not error:
        try:
            scheduled_start = datetime.fromisoformat(start_at).replace(
                tzinfo=ZoneInfo(settings.app_timezone)
            )
            if scheduled_start.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                error = "Scheduled exam start time must be in the future."
        except ValueError:
            error = "Enter a valid scheduled start date and time."

    raw = await exam_file.read(settings.max_exam_upload_bytes + 1)
    if len(raw) > settings.max_exam_upload_bytes:
        error = "The exam file is larger than the allowed upload size."

    parsed_questions = []
    source_text = ""
    if not error:
        try:
            source_text = decode_exam_upload(raw)
            parsed_questions = parse_exam_text(source_text)
        except ExamParseError as exc:
            error = str(exc)

    if db.scalar(select(Exam.id).where(func.lower(Exam.name) == name.strip().lower())):
        error = "An exam with that name already exists."

    if error:
        return render(
            request,
            "instructor/new_exam.html",
            {
                "page_title": "Upload exam",
                "user": user,
                "error": error,
                "form": form_values,
                "current_local_iso": current_local_iso(),
            },
            status_code=400,
        )

    try:
        exam = create_exam(
            db,
            name=name,
            exam_type=selected_type,
            start_at=scheduled_start,
            duration_minutes=duration_minutes,
            join_grace_minutes=join_grace_minutes,
            positive_marks=quantized_positive,
            negative_marks=quantized_negative,
            source_filename=Path(exam_file.filename or "exam.txt").name,
            source_text=source_text,
            creator=user,
            questions=parsed_questions,
        )
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "instructor/new_exam.html",
            {
                "page_title": "Upload exam",
                "user": user,
                "error": "Exam name already exists.",
                "form": form_values,
                "current_local_iso": current_local_iso(),
            },
            status_code=409,
        )
    flash(request, f"{exam.name} was parsed and published.", "success")
    return RedirectResponse(f"/instructor/exams/{exam.id}", status_code=303)


@router.get("/exams/{exam_id}")
def exam_detail(exam_id: int, request: Request, db: Session = Depends(get_db)):
    user = instructor_user(request, db)
    exam = owned_exam(db, exam_id, user.id)
    examiners = list(
        db.scalars(
            select(User)
            .where(User.role == UserRole.EXAMINER, User.is_active.is_(True))
            .order_by(User.full_name)
        )
    )
    assigned_examiner_ids = {
        assignment.examiner_id for assignment in exam.examiner_assignments
    }
    return render(
        request,
        "instructor/exam_detail.html",
        {
            "page_title": exam.name,
            "user": user,
            "exam": exam,
            "examiners": examiners,
            "available_examiners": [
                examiner
                for examiner in examiners
                if examiner.id not in assigned_examiner_ids
            ],
        },
    )


@router.post("/exams/{exam_id}/assign-examiner")
async def assign_examiner(
    exam_id: int,
    request: Request,
    examiner_id: int = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = instructor_user(request, db)
    validate_csrf(request, csrf_token)
    exam = owned_exam(db, exam_id, user.id)
    examiner = db.scalar(
        select(User).where(
            User.id == examiner_id,
            User.role == UserRole.EXAMINER,
            User.is_active.is_(True),
        )
    )
    if not examiner:
        raise HTTPException(status_code=400, detail="Invalid examiner")
    exists = db.scalar(
        select(ExaminerAssignment.id).where(
            ExaminerAssignment.exam_id == exam.id,
            ExaminerAssignment.examiner_id == examiner.id,
        )
    )
    if not exists:
        db.add(
            ExaminerAssignment(
                exam_id=exam.id, examiner_id=examiner.id, assigned_by_id=user.id
            )
        )
        db.add(
            AuditLog(
                actor_id=user.id,
                action="assign_examiner",
                entity_type="exam",
                entity_id=exam.id,
                details={"examiner_id": examiner.id},
            )
        )
        db.commit()
    await manager.allow_examiner(exam.id, examiner.id)
    flash(request, f"{examiner.full_name} is assigned to {exam.name}.", "success")
    return RedirectResponse(f"/instructor/exams/{exam.id}", status_code=303)


@router.post("/exams/{exam_id}/unassign-examiner/{examiner_id}")
async def unassign_examiner(
    exam_id: int,
    examiner_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = instructor_user(request, db)
    validate_csrf(request, csrf_token)
    exam = owned_exam(db, exam_id, user.id)
    assignment = db.scalar(
        select(ExaminerAssignment)
        .where(
            ExaminerAssignment.exam_id == exam.id,
            ExaminerAssignment.examiner_id == examiner_id,
        )
        .options(selectinload(ExaminerAssignment.examiner))
    )
    if assignment is None:
        flash(request, "That invigilator is no longer assigned to this exam.", "error")
        return RedirectResponse(f"/instructor/exams/{exam.id}", status_code=303)

    examiner_name = assignment.examiner.full_name
    db.delete(assignment)
    db.add(
        AuditLog(
            actor_id=user.id,
            action="unassign_examiner",
            entity_type="exam",
            entity_id=exam.id,
            details={"examiner_id": examiner_id},
        )
    )
    db.commit()
    await manager.revoke_examiner(examiner_id, exam.id)
    flash(request, f"{examiner_name} can no longer monitor {exam.name}.", "success")
    return RedirectResponse(f"/instructor/exams/{exam.id}", status_code=303)


@router.post("/examiners/{examiner_id}/delete")
async def delete_examiner(
    examiner_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = instructor_user(request, db)
    validate_csrf(request, csrf_token)
    examiner = db.scalar(
        select(User)
        .where(User.id == examiner_id, User.role == UserRole.EXAMINER)
        .options(selectinload(User.examiner_assignments))
    )
    if examiner is None:
        flash(request, "That invigilator account no longer exists.", "error")
        return RedirectResponse("/instructor", status_code=303)

    examiner_name = examiner.full_name
    assigned_exam_ids = [assignment.exam_id for assignment in examiner.examiner_assignments]
    db.delete(examiner)
    db.add(
        AuditLog(
            actor_id=user.id,
            action="delete_examiner",
            entity_type="user",
            entity_id=examiner_id,
            details={
                "username": examiner.username,
                "assigned_exam_ids": assigned_exam_ids,
            },
        )
    )
    db.commit()
    await manager.revoke_examiner(examiner_id)
    flash(request, f"{examiner_name}'s invigilator account was deleted.", "success")
    return RedirectResponse("/instructor", status_code=303)


@router.post("/exams/{exam_id}/end")
def end_exam(
    exam_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = instructor_user(request, db)
    validate_csrf(request, csrf_token)
    exam = db.scalar(
        select(Exam)
        .where(Exam.id == exam_id, Exam.created_by_id == user.id)
        .with_for_update()
    )
    if exam is None:
        raise HTTPException(status_code=404)
    if exam.status == ExamStatus.ARCHIVED:
        flash(request, "This exam is already closed to new candidates.", "success")
        return RedirectResponse(f"/instructor/exams/{exam.id}", status_code=303)

    active_count = int(
        db.scalar(
            select(func.count(ExamAttempt.id)).where(
                ExamAttempt.exam_id == exam.id,
                ExamAttempt.status == AttemptStatus.IN_PROGRESS,
            )
        )
        or 0
    )
    exam.status = ExamStatus.ARCHIVED
    db.add(
        AuditLog(
            actor_id=user.id,
            action="end_exam",
            entity_type="exam",
            entity_id=exam.id,
            details={"active_attempts_allowed_to_finish": active_count},
        )
    )
    db.commit()
    flash(
        request,
        (
            f"{exam.name} is closed to new candidates. "
            f"{active_count} active candidate{'s' if active_count != 1 else ''} may finish."
        ),
        "success",
    )
    return RedirectResponse(f"/instructor/exams/{exam.id}", status_code=303)


@router.post("/examiners/new")
def create_examiner(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = instructor_user(request, db)
    validate_csrf(request, csrf_token)
    normalized = normalize_username(username)
    error = validate_password_strength(password)
    if not full_name.strip() or not normalized or any(c.isspace() for c in normalized):
        error = "Provide a full name and a username without spaces."
    if error:
        flash(request, error, "error")
        return RedirectResponse("/instructor", status_code=303)
    examiner = User(
        full_name=full_name.strip(),
        username=normalized,
        password_hash=hash_password(password),
        role=UserRole.EXAMINER,
    )
    db.add(examiner)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "That username is already in use.", "error")
        return RedirectResponse("/instructor", status_code=303)
    flash(request, f"Examiner account created for {examiner.full_name}.", "success")
    return RedirectResponse("/instructor", status_code=303)


@router.get("/exams/{exam_id}/analysis")
def analysis(exam_id: int, request: Request, db: Session = Depends(get_db)):
    user = instructor_user(request, db)
    exam = owned_exam(db, exam_id, user.id)
    return render(
        request,
        "instructor/analysis.html",
        {
            "page_title": f"{exam.name} analysis",
            "user": user,
            "exam": exam,
            "analysis": exam_analysis(db, exam),
        },
    )


@router.get("/exams/{exam_id}/attempts/{attempt_id}")
def attempt_detail(
    exam_id: int,
    attempt_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = instructor_user(request, db)
    exam = owned_exam(db, exam_id, user.id)
    attempt = db.scalar(
        select(ExamAttempt)
        .where(
            ExamAttempt.id == attempt_id,
            ExamAttempt.exam_id == exam.id,
        )
        .options(
            selectinload(ExamAttempt.student),
            selectinload(ExamAttempt.exam)
            .selectinload(Exam.questions)
            .selectinload(Question.options),
            selectinload(ExamAttempt.responses).selectinload(Response.selected_option),
        )
    )
    if not attempt:
        raise HTTPException(status_code=404)
    return render(
        request,
        "instructor/attempt_detail.html",
        {
            "page_title": "Student attempt",
            "user": user,
            "exam": exam,
            "attempt": attempt,
            "review": attempt_review(attempt, True),
        },
    )
