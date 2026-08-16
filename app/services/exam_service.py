from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    AttemptStatus,
    Exam,
    ExamAttempt,
    ExamStatus,
    ExamType,
    Question,
    QuestionOption,
    Response,
    User,
)
from app.services.exam_parser import ParsedQuestion


class AttemptRuleError(ValueError):
    pass

def aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def create_exam(
    db: Session,
    *,
    name: str,
    exam_type: ExamType,
    start_at: datetime | None,
    duration_minutes: int,
    positive_marks: Decimal = Decimal("4.00"),
    creator: User,
    questions: list[ParsedQuestion],
) -> Exam:
    exam = Exam(
        name=name.strip(),
        exam_type=exam_type,
        status=ExamStatus.PUBLISHED,
        start_at=aware_utc(start_at),
        duration_minutes=duration_minutes,
        positive_marks=positive_marks,
        total_questions=len(questions),
        created_by_id=creator.id,
    )
    for q_position, parsed_question in enumerate(questions, start=1):
        question = Question(position=q_position, text=parsed_question.text)
        for option_position, parsed_option in enumerate(parsed_question.options):
            question.options.append(
                QuestionOption(
                    position=option_position,
                    label=parsed_option.label,
                    text=parsed_option.text,
                    is_correct=parsed_option.label == parsed_question.correct_label,
                )
            )
        exam.questions.append(question)
    db.add(exam)
    db.commit()
    db.refresh(exam)
    return exam

def scheduled_end(exam: Exam) -> datetime | None:
    start_at = aware_utc(exam.start_at)
    if start_at is None:
        return None
    return start_at + timedelta(minutes=exam.duration_minutes)

def scheduled_finish(exam: Exam) -> datetime | None:
    """Return start time plus the full exam duration."""
    return scheduled_end(exam)

def exam_lifecycle_status(exam: Exam, now: datetime | None = None) -> dict[str, str]:
    now = aware_utc(now) or datetime.now(timezone.utc)
    if exam.status == ExamStatus.ARCHIVED:
        return {"label": "Ended", "tone": "danger"}
    finish_at = scheduled_finish(exam)
    if finish_at is not None and now >= finish_at:
        return {"label": "Ended", "tone": "danger"}
    start_at = aware_utc(exam.start_at)
    if start_at is not None and now >= start_at:
        return {"label": "Running", "tone": "success"}
    return {"label": "Published", "tone": "warning"}

def exam_time_status(exam: Exam, now: datetime | None = None) -> str:
    now = aware_utc(now) or datetime.now(timezone.utc)
    status = exam_lifecycle_status(exam, now)
    if status["label"] == "Ended":
        return "Finished"
    start_at = aware_utc(exam.start_at)
    end_at = scheduled_finish(exam)
    target = start_at if start_at is not None and now < start_at else end_at
    if target is None:
        return "Time unavailable"
    total_minutes = max(1, math.ceil((target - now).total_seconds() / 60))
    days, remaining_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remaining_minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} {'day' if days == 1 else 'days'}")
    if hours:
        parts.append(f"{hours} hr")
    if minutes or not parts:
        parts.append(f"{minutes} min")
    value = " ".join(parts)
    if start_at is not None and now < start_at:
        return f"Starts in · {value}"
    return f"Ends in · {value}"

def exam_availability(
    exam: Exam,
    attempts: list[ExamAttempt],
    now: datetime | None = None,
) -> dict[str, object]:
    now = aware_utc(now) or datetime.now(timezone.utc)
    if exam.exam_type != ExamType.SCHEDULED:
        return {"state": "closed", "label": "Not available"}
    active = next((a for a in attempts if a.status == AttemptStatus.IN_PROGRESS), None)
    if active:
        if active.expires_at is None or now < aware_utc(active.expires_at):
            return {"state": "resume", "label": "Resume exam", "attempt_id": active.id}
        return {"state": "processing", "label": "Finalizing attempt"}

    if exam.status != ExamStatus.PUBLISHED:
        return {"state": "closed", "label": "Not available"}
    if attempts:
        return {"state": "completed", "label": "Already attempted"}
    start_at = aware_utc(exam.start_at)
    if start_at is None:
        return {"state": "closed", "label": "Schedule unavailable"}
    if now < start_at:
        return {"state": "upcoming", "label": "Not started"}
    finish_at = scheduled_finish(exam)
    if finish_at and now >= finish_at:
        return {"state": "closed", "label": "Exam ended"}
    return {"state": "available", "label": "Start scheduled exam"}

def create_or_resume_attempt(
    db: Session,
    exam: Exam,
    student: User,
    now: datetime | None = None,
) -> tuple[ExamAttempt, bool]:
    now = aware_utc(now) or datetime.now(timezone.utc)
    exam = db.scalar(
        select(Exam)
        .where(Exam.id == exam.id)
        .with_for_update()
        .options(selectinload(Exam.questions).selectinload(Question.options))
    )
    if exam is None:
        raise AttemptRuleError("Exam not found.")

    previous = list(
        db.scalars(
            select(ExamAttempt)
            .where(
                ExamAttempt.exam_id == exam.id,
                ExamAttempt.student_id == student.id,
            )
            .order_by(ExamAttempt.attempt_number)
        )
    )
    active = next((a for a in previous if a.status == AttemptStatus.IN_PROGRESS), None)
    if active and (active.expires_at is None or now < aware_utc(active.expires_at)):
        return active, False
    if active:
        submit_attempt(db, active, auto=True, now=now)

    availability = exam_availability(exam, previous, now)
    if availability["state"] != "available":
        raise AttemptRuleError(str(availability["label"]))

    attempt = ExamAttempt(
        exam_id=exam.id,
        student_id=student.id,
        attempt_number=1,
        status=AttemptStatus.IN_PROGRESS,
        started_at=now,
        activated_at=None,
        expires_at=None,
        current_question_position=0,
        last_heartbeat_at=now,
    )
    for question in exam.questions:
        attempt.responses.append(Response(question_id=question.id))
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AttemptRuleError("This exam has already been started.") from exc
    db.refresh(attempt)
    return attempt, True

def get_attempt_for_student(
    db: Session, attempt_id: int, student_id: int
) -> ExamAttempt | None:
    return db.scalar(
        select(ExamAttempt)
        .where(ExamAttempt.id == attempt_id, ExamAttempt.student_id == student_id)
        .options(
            selectinload(ExamAttempt.exam)
            .selectinload(Exam.questions)
            .selectinload(Question.options),
            selectinload(ExamAttempt.responses).selectinload(Response.selected_option),
        )
    )

def activate_attempt(
    db: Session,
    attempt: ExamAttempt,
    now: datetime | None = None,
) -> ExamAttempt:
    now = aware_utc(now) or datetime.now(timezone.utc)
    attempt = db.scalar(
        select(ExamAttempt)
        .where(ExamAttempt.id == attempt.id)
        .with_for_update()
        .options(selectinload(ExamAttempt.exam))
    )
    if attempt is None:
        raise AttemptRuleError("Attempt not found.")
    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise AttemptRuleError("This attempt has already been submitted.")
    if attempt.activated_at is not None and attempt.expires_at is not None:
        return attempt

    if attempt.exam.exam_type != ExamType.SCHEDULED:
        raise AttemptRuleError("This exam is not available.")
    start_at = aware_utc(attempt.exam.start_at)
    if start_at is None or now < start_at:
        raise AttemptRuleError("This scheduled exam has not started yet.")

    attempt.started_at = now
    attempt.activated_at = now
    attempt.expires_at = now + timedelta(minutes=attempt.exam.duration_minutes)
    attempt.last_heartbeat_at = now
    db.commit()
    db.refresh(attempt)
    return attempt

def attempt_payload(attempt: ExamAttempt) -> list[dict[str, object]]:
    response_map = {response.question_id: response for response in attempt.responses}
    payload: list[dict[str, object]] = []
    
    questions = sorted(attempt.exam.questions, key=lambda q: q.position)
    
    for display_position, question in enumerate(questions):
        options = sorted(question.options, key=lambda o: o.position)
        response = response_map.get(question.id)
        payload.append(
            {
                "id": question.id,
                "position": display_position,
                "text": question.text,
                "options": [
                    {
                        "id": option.id,
                        "display_label": chr(ord("A") + index),
                        "text": option.text,
                    }
                    for index, option in enumerate(options)
                ],
                "selected_option_id": response.selected_option_id if response else None,
                "time_spent_seconds": response.time_spent_seconds if response else 0,
            }
        )
    return payload

def save_response(
    db: Session,
    attempt: ExamAttempt,
    *,
    question_id: int,
    selected_option_id: int | None,
    time_spent_seconds: int,
    current_position: int,
    now: datetime | None = None,
) -> ExamAttempt:
    now = aware_utc(now) or datetime.now(timezone.utc)
    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise AttemptRuleError("This attempt has already been submitted.")
    if attempt.activated_at is None or attempt.expires_at is None:
        raise AttemptRuleError("Enter fullscreen to start this exam first.")
    if now >= aware_utc(attempt.expires_at):
        submit_attempt(db, attempt, auto=True, now=now)
        raise AttemptRuleError("Time is up. The exam was submitted automatically.")

    response = db.scalar(
        select(Response).where(
            Response.attempt_id == attempt.id,
            Response.question_id == question_id,
        )
    )
    if response is None:
        raise AttemptRuleError("Question does not belong to this attempt.")

    if selected_option_id is not None:
        valid_option = db.scalar(
            select(QuestionOption.id).where(
                QuestionOption.id == selected_option_id,
                QuestionOption.question_id == question_id,
            )
        )
        if valid_option is None:
            raise AttemptRuleError("Selected option does not belong to this question.")

    max_seconds = attempt.exam.duration_minutes * 60
    response.selected_option_id = selected_option_id
    response.time_spent_seconds = max(
        response.time_spent_seconds,
        min(max(0, int(time_spent_seconds)), max_seconds),
    )
    attempt.current_question_position = min(
        max(0, int(current_position)), max(0, attempt.exam.total_questions - 1)
    )
    attempt.last_heartbeat_at = now
    db.commit()
    return attempt

def submit_attempt(
    db: Session,
    attempt: ExamAttempt,
    *,
    auto: bool = False,
    now: datetime | None = None,
) -> ExamAttempt:
    if attempt.status != AttemptStatus.IN_PROGRESS:
        return attempt
    now = aware_utc(now) or datetime.now(timezone.utc)

    attempt = db.scalar(
        select(ExamAttempt)
        .where(ExamAttempt.id == attempt.id)
        .with_for_update()
        .options(
            selectinload(ExamAttempt.exam)
            .selectinload(Exam.questions)
            .selectinload(Question.options),
            selectinload(ExamAttempt.responses).selectinload(Response.selected_option),
        )
    )
    if attempt is None or attempt.status != AttemptStatus.IN_PROGRESS:
        return attempt
    if not auto and attempt.activated_at is None:
        raise AttemptRuleError("Enter fullscreen to start this exam first.")

    question_map = {question.id: question for question in attempt.exam.questions}
    score = Decimal("0.00")
    for response in attempt.responses:
        question = question_map[response.question_id]
        correct = next(option for option in question.options if option.is_correct)
        marks = Decimal("0.00")
        if response.selected_option_id is not None and response.selected_option_id == correct.id:
            marks = attempt.exam.positive_marks
        response.marks_awarded = marks
        score += marks

    attempt.score = score
    attempt.max_score = attempt.exam.total_questions * attempt.exam.positive_marks
    attempt.total_time_seconds = sum((r.time_spent_seconds for r in attempt.responses), 0)
    attempt.attempted_count = sum((1 for r in attempt.responses if r.selected_option_id is not None), 0)
    attempt.wrong_count = sum((1 for r in attempt.responses if r.selected_option_id is not None and r.marks_awarded <= 0), 0)
    attempt.status = AttemptStatus.AUTO_SUBMITTED if auto else AttemptStatus.SUBMITTED
    attempt.submitted_at = now
    attempt.last_heartbeat_at = now
    db.commit()
    db.refresh(attempt)
    return attempt

def auto_submit_expired_attempts(db: Session, now: datetime | None = None) -> int:
    now = aware_utc(now) or datetime.now(timezone.utc)
    attempts = list(
        db.scalars(
            select(ExamAttempt).where(
                ExamAttempt.status == AttemptStatus.IN_PROGRESS,
                ExamAttempt.expires_at.is_not(None),
                ExamAttempt.expires_at <= now,
            )
        )
    )
    for attempt in attempts:
        submit_attempt(db, attempt, auto=True, now=now)
    return len(attempts)

def auto_end_scheduled_exams(db: Session, now: datetime | None = None) -> int:
    now = aware_utc(now) or datetime.now(timezone.utc)
    exams = list(
        db.scalars(
            select(Exam)
            .where(
                Exam.status == ExamStatus.PUBLISHED,
                Exam.exam_type == ExamType.SCHEDULED,
                Exam.start_at.is_not(None),
                Exam.start_at <= now,
            )
            .with_for_update()
        )
    )
    ended = []
    for exam in exams:
        finish_at = scheduled_finish(exam)
        if finish_at is None or finish_at > now:
            continue
        exam.status = ExamStatus.ARCHIVED
        ended.append(exam)

    if ended:
        db.commit()
    return len(ended)

def can_review_attempt(attempt: ExamAttempt, now: datetime | None = None) -> bool:
    end_at = scheduled_end(attempt.exam)
    return bool(end_at and (aware_utc(now) or datetime.now(timezone.utc)) >= end_at)
