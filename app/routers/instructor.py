from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.main import flash, render
from app.models import (
    AttemptStatus,
    Exam,
    ExamAttempt,
    ExaminerAssignment,
    ExamType,
    Question,
    Response,
    User,
    UserRole,
)
from app.security import (
    current_user_from_request,
    hash_password,
    normalize_username,
    require_role,
    validate_csrf,
    validate_password_strength,
)
from app.services.analytics import attempt_review, exam_analysis
from app.services.exam_parser import ExamParseError, decode_exam_upload, parse_exam_text
from app.services.exam_service import (
    aware_utc,
    create_exam,
    exam_lifecycle_status,
    exam_time_status,
    scheduled_finish,
)
from app.services.live_monitor import manager

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
    exam_count = int(
        db.scalar(select(func.count(Exam.id)).where(Exam.created_by_id == user.id))
        or 0
    )
    submitted_attempt_count = int(
        db.scalar(
            select(func.count(ExamAttempt.id))
            .join(Exam)
            .where(
                Exam.created_by_id == user.id,
                ExamAttempt.status != AttemptStatus.IN_PROGRESS,
            )
        )
        or 0
    )
    invigilator_count = int(
        db.scalar(
            select(func.count(User.id)).where(
                User.role == UserRole.EXAMINER, User.is_active.is_(True)
            )
        )
        or 0
    )
    return render(
        request,
        "instructor/dashboard.html",
        {
            "page_title": "Instructor dashboard",
            "user": user,
            "exam_count": exam_count,
            "submitted_attempt_count": submitted_attempt_count,
            "invigilator_count": invigilator_count,
        },
    )


@router.get("/exams")
def uploaded_exams(request: Request, db: Session = Depends(get_db)):
    user = instructor_user(request, db)
    now = datetime.now(timezone.utc)
    exams = list(
        db.scalars(
            select(Exam)
            .where(Exam.created_by_id == user.id)
            .order_by(Exam.created_at.desc())
            .options(selectinload(Exam.attempts))
        )
    )
    exam_rows = [
        {
            "exam": exam,
            "student_count": len({attempt.student_id for attempt in exam.attempts}),
            "status": exam_lifecycle_status(exam, now),
            "end_at": scheduled_finish(exam),
        }
        for exam in exams
    ]
    return render(
        request,
        "instructor/exams.html",
        {
            "page_title": "Uploaded exams",
            "user": user,
            "exam_rows": exam_rows,
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
def new_exam(
    request: Request,
    name: str = Form(...),
    duration_minutes: int = Form(...),
    positive_marks: Decimal = Form(Decimal("4.00")),
    start_at: str = Form(""),
    csrf_token: str = Form(...),
    exam_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = instructor_user(request, db)
    validate_csrf(request, csrf_token)
    form_values = {
        "name": name,
        "duration_minutes": duration_minutes,
        "positive_marks": str(positive_marks),
        "start_at": start_at,
    }
    error: str | None = None
    quantized_positive: Decimal | None = None
    if positive_marks.is_finite():
        try:
            quantized_positive = positive_marks.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        except InvalidOperation:
            pass
    selected_type = ExamType.SCHEDULED

    if not name.strip() or len(name.strip()) > 160:
        error = "Exam name is required and must not exceed 160 characters."
    elif duration_minutes < 1 or duration_minutes > 720:
        error = "Duration must be between 1 and 720 minutes."
    elif quantized_positive is None or quantized_positive <= 0 or quantized_positive > Decimal(1000):
        error = "Positive marks must be greater than 0 and no more than 1000."
    elif Path(exam_file.filename or "").suffix.lower() != ".txt":
        error = "Upload a .txt exam file."

    scheduled_start: datetime | None = None
    if not error:
        try:
            scheduled_start = datetime.fromisoformat(start_at).replace(
                tzinfo=ZoneInfo(settings.app_timezone)
            )
            if scheduled_start.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                error = "Scheduled exam start time must be in the future."
        except ValueError:
            error = "Enter a valid scheduled start date and time."

    raw = exam_file.file.read(settings.max_exam_upload_bytes + 1)
    if len(raw) > settings.max_exam_upload_bytes:
        error = "The exam file is larger than the allowed upload size."

    parsed_questions = []
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
            positive_marks=quantized_positive,
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
    now = datetime.now(timezone.utc)
    end_at = scheduled_finish(exam)
    exam_status = exam_lifecycle_status(exam, now)
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
            "exam_status": exam_status,
            "exam_time_status": exam_time_status(exam, now),
            "start_at_iso": aware_utc(exam.start_at).isoformat() if exam.start_at else "",
            "end_at_iso": aware_utc(end_at).isoformat() if end_at else "",
            "examiners": examiners,
            "available_examiners": [
                examiner
                for examiner in examiners
                if examiner.id not in assigned_examiner_ids
            ],
        },
    )


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


@router.post("/exams/{exam_id}/delete")
async def delete_exam(
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

    attempt_ids = list(
        db.scalars(select(ExamAttempt.id).where(ExamAttempt.exam_id == exam.id))
    )

    exam_name = exam.name
    db.delete(exam)
    db.commit()
    await manager.remove_exam(exam_id, attempt_ids)
    flash(
        request,
        f"{exam_name} and all of its exam data were permanently deleted.",
        "success",
    )
    return RedirectResponse("/instructor/exams", status_code=303)


@router.get("/performance")
def performance(request: Request, db: Session = Depends(get_db)):
    user = instructor_user(request, db)
    now = datetime.now(timezone.utc)
    exams = list(
        db.scalars(
            select(Exam)
            .where(Exam.created_by_id == user.id)
            .order_by(Exam.created_at.desc())
            .options(selectinload(Exam.attempts).selectinload(ExamAttempt.responses))
        )
    )
    performance_rows = []
    for exam in exams:
        submitted = [
            attempt
            for attempt in exam.attempts
            if attempt.status != AttemptStatus.IN_PROGRESS
        ]
        average_score = (
            sum((attempt.score or Decimal("0.00") for attempt in submitted), Decimal("0.00"))
            / len(submitted)
            if submitted
            else Decimal("0.00")
        )
        average_time = (
            sum(sum(r.time_spent_seconds for r in attempt.responses) for attempt in submitted) / len(submitted)
            if submitted
            else 0.0
        )
        performance_rows.append(
            {
                "exam": exam,
                "student_count": len({attempt.student_id for attempt in submitted}),
                "average_score": average_score,
                "average_time": average_time,
                "max_score": exam.total_questions * exam.positive_marks,
                "status": exam_lifecycle_status(exam, now),
                "end_at": scheduled_finish(exam),
            }
        )
    return render(
        request,
        "instructor/performance.html",
        {
            "page_title": "Student performance",
            "user": user,
            "performance_rows": performance_rows,
        },
    )


@router.get("/invigilators")
def invigilators(request: Request, db: Session = Depends(get_db)):
    user = instructor_user(request, db)
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
        "instructor/invigilators.html",
        {
            "page_title": "Invigilators",
            "user": user,
            "examiners": examiners,
        },
    )


@router.post("/examiners/new")
def create_examiner(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    instructor_user(request, db)
    validate_csrf(request, csrf_token)
    normalized = normalize_username(username)
    error = validate_password_strength(password)
    if not full_name.strip() or not normalized or any(c.isspace() for c in normalized):
        error = "Provide a full name and a username without spaces."
    if error:
        flash(request, error, "error")
        return RedirectResponse("/instructor/invigilators", status_code=303)
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
        return RedirectResponse("/instructor/invigilators", status_code=303)
    flash(request, f"Examiner account created for {examiner.full_name}.", "success")
    return RedirectResponse("/instructor/invigilators", status_code=303)


@router.post("/examiners/{examiner_id}/delete")
async def delete_examiner(
    examiner_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    instructor_user(request, db)
    validate_csrf(request, csrf_token)
    examiner = db.scalar(
        select(User)
        .where(User.id == examiner_id, User.role == UserRole.EXAMINER)
        .options(selectinload(User.examiner_assignments))
    )
    if examiner is None:
        flash(request, "That invigilator account no longer exists.", "error")
        return RedirectResponse("/instructor/invigilators", status_code=303)

    examiner_name = examiner.full_name
    db.delete(examiner)
    db.commit()
    await manager.revoke_examiner(examiner_id)
    flash(request, f"{examiner_name}'s invigilator account was deleted.", "success")
    return RedirectResponse("/instructor/invigilators", status_code=303)


@router.post("/exams/{exam_id}/assign-examiner")
def assign_examiner(
    exam_id: int,
    request: Request,
    examiner_id: int = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    user = instructor_user(request, db)
    validate_csrf(request, csrf_token)
    exam = owned_exam(db, exam_id, user.id)
    if exam_lifecycle_status(exam, datetime.now(timezone.utc))["label"] == "Ended":
        flash(request, "New invigilators cannot be assigned after an exam has ended.", "error")
        return RedirectResponse(f"/instructor/exams/{exam.id}", status_code=303)
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
        db.commit()
    manager.allow_examiner(exam.id, examiner.id)
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
    db.commit()
    await manager.revoke_examiner(examiner_id, exam.id)
    flash(request, f"{examiner_name} can no longer monitor {exam.name}.", "success")
    return RedirectResponse(f"/instructor/exams/{exam.id}", status_code=303)
