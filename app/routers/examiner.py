from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.dependencies import current_user_from_request, require_role
from app.models import Exam, ExaminerAssignment, UserRole
from app.services.exam_service import (
    aware_utc,
    exam_lifecycle_status,
    exam_time_status,
    scheduled_finish,
)
from app.services.live_monitor import exam_snapshot
from app.web import render


router = APIRouter(prefix="/examiner", tags=["examiner"])


def examiner_user(request: Request, db: Session):
    return require_role(current_user_from_request(request, db), UserRole.EXAMINER)


def assigned_exam(db: Session, exam_id: int, examiner_id: int) -> Exam:
    exam = db.scalar(
        select(Exam)
        .join(ExaminerAssignment)
        .where(
            Exam.id == exam_id,
            ExaminerAssignment.examiner_id == examiner_id,
        )
        .options(selectinload(Exam.creator))
    )
    if not exam:
        raise HTTPException(status_code=404)
    return exam


@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = examiner_user(request, db)
    now = datetime.now(timezone.utc)
    exams = list(
        db.scalars(
            select(Exam)
            .join(ExaminerAssignment)
            .where(ExaminerAssignment.examiner_id == user.id)
            .order_by(Exam.start_at.desc().nullslast())
            .options(selectinload(Exam.creator), selectinload(Exam.attempts))
        )
    )
    exam_rows = [
        {
            "exam": exam,
            "status": exam_lifecycle_status(exam, now),
            "time_status": exam_time_status(exam, now),
            "start_at_iso": aware_utc(exam.start_at).isoformat() if exam.start_at else "",
            "end_at_iso": (
                aware_utc(scheduled_finish(exam)).isoformat()
                if scheduled_finish(exam)
                else ""
            ),
        }
        for exam in exams
    ]
    return render(
        request,
        "examiner/dashboard.html",
        {"page_title": "Examiner dashboard", "user": user, "exam_rows": exam_rows},
    )


@router.get("/exams/{exam_id}/monitor")
def monitor(exam_id: int, request: Request, db: Session = Depends(get_db)):
    user = examiner_user(request, db)
    exam = assigned_exam(db, exam_id, user.id)
    now = datetime.now(timezone.utc)
    status = exam_lifecycle_status(exam, now)
    end_at = scheduled_finish(exam)
    return render(
        request,
        "examiner/monitor.html",
        {
            "page_title": f"Monitor {exam.name}",
            "user": user,
            "exam": exam,
            "exam_status": status,
            "exam_time_status": exam_time_status(exam, now),
            "start_at_iso": aware_utc(exam.start_at).isoformat() if exam.start_at else "",
            "end_at_iso": aware_utc(end_at).isoformat() if end_at else "",
            "monitor_config": {
                "examId": exam.id,
                "socketUrl": f"/ws/examiner/exams/{exam.id}",
                "snapshotUrl": f"/examiner/exams/{exam.id}/snapshot",
            },
            "initial_snapshot": exam_snapshot(db, exam.id),
        },
    )


@router.get("/exams/{exam_id}/snapshot")
def snapshot(exam_id: int, request: Request, db: Session = Depends(get_db)):
    user = examiner_user(request, db)
    assigned_exam(db, exam_id, user.id)
    return exam_snapshot(db, exam_id)
