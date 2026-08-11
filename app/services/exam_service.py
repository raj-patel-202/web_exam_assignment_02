from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
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
    join_grace_minutes: int,
    positive_marks: Decimal = Decimal("4.00"),
    negative_marks: Decimal = Decimal("-1.00"),
    source_filename: str,
    source_text: str,
    creator: User,
    questions: list[ParsedQuestion],
) -> Exam:
    exam = Exam(
        name=name.strip(),
        exam_type=exam_type,
        status=ExamStatus.PUBLISHED,
        start_at=aware_utc(start_at),
        duration_minutes=duration_minutes,
        join_grace_minutes=join_grace_minutes,
        positive_marks=positive_marks,
        negative_marks=negative_marks,
        total_questions=len(questions),
        source_filename=source_filename,
        source_text=source_text,
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
    # Students who join during the grace period still receive the full duration.
    # Delay answer release until the last valid late-start attempt must be over.
    return start_at + timedelta(
        minutes=exam.duration_minutes + exam.join_grace_minutes
    )


def exam_availability(
    exam: Exam,
    attempts: list[ExamAttempt],
    now: datetime | None = None,
) -> dict[str, object]:
    now = aware_utc(now) or datetime.now(timezone.utc)
    active = next((a for a in attempts if a.status == AttemptStatus.IN_PROGRESS), None)
    if active:
        if active.expires_at is None or now < aware_utc(active.expires_at):
            return {"state": "resume", "label": "Resume exam", "attempt_id": active.id}
        return {"state": "processing", "label": "Finalizing attempt"}

    if exam.status != ExamStatus.PUBLISHED:
        return {"state": "closed", "label": "Not available"}
    if exam.exam_type == ExamType.PRACTICE:
        return {"state": "available", "label": "Start practice"}

    if attempts:
        return {"state": "completed", "label": "Already attempted"}
    start_at = aware_utc(exam.start_at)
    if start_at is None:
        return {"state": "closed", "label": "Schedule unavailable"}
    if now < start_at:
        return {"state": "upcoming", "label": "Not started"}
    if now > start_at + timedelta(minutes=exam.join_grace_minutes):
        return {"state": "closed", "label": "Joining window closed"}
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

    question_ids = [question.id for question in exam.questions]
    rng = random.SystemRandom()
    rng.shuffle(question_ids)
    option_orders: dict[str, list[int]] = {}
    for question in exam.questions:
        option_ids = [option.id for option in question.options]
        rng.shuffle(option_ids)
        option_orders[str(question.id)] = option_ids

    attempt_number = 1 if exam.exam_type == ExamType.SCHEDULED else len(previous) + 1
    attempt = ExamAttempt(
        exam_id=exam.id,
        student_id=student.id,
        attempt_number=attempt_number,
        status=AttemptStatus.IN_PROGRESS,
        started_at=now,
        activated_at=None,
        expires_at=None,
        question_order=question_ids,
        option_orders=option_orders,
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
    """Start the authoritative exam timer after the browser enters fullscreen."""
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

    if attempt.exam.exam_type == ExamType.SCHEDULED:
        start_at = aware_utc(attempt.exam.start_at)
        if start_at is None or now < start_at:
            raise AttemptRuleError("This scheduled exam has not started yet.")
        if now > start_at + timedelta(minutes=attempt.exam.join_grace_minutes):
            submit_attempt(db, attempt, auto=True, now=now)
            raise AttemptRuleError("The joining window closed before the exam was started.")

    attempt.started_at = now
    attempt.activated_at = now
    attempt.expires_at = now + timedelta(minutes=attempt.exam.duration_minutes)
    attempt.last_heartbeat_at = now
    db.commit()
    db.refresh(attempt)
    return attempt


def attempt_payload(attempt: ExamAttempt) -> list[dict[str, object]]:
    questions = {question.id: question for question in attempt.exam.questions}
    response_map = {response.question_id: response for response in attempt.responses}
    payload: list[dict[str, object]] = []
    for display_position, question_id in enumerate(attempt.question_order):
        question = questions[int(question_id)]
        option_map = {option.id: option for option in question.options}
        ordered_options = attempt.option_orders.get(str(question.id), [])
        response = response_map.get(question.id)
        payload.append(
            {
                "id": question.id,
                "position": display_position,
                "text": question.text,
                "options": [
                    {
                        "id": option_map[int(option_id)].id,
                        "display_label": chr(ord("A") + index),
                        "text": option_map[int(option_id)].text,
                    }
                    for index, option_id in enumerate(ordered_options)
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
    attempted_count = 0
    wrong_count = 0
    total_time = 0
    for response in attempt.responses:
        question = question_map[response.question_id]
        correct = next(option for option in question.options if option.is_correct)
        marks = Decimal("0.00")
        if response.selected_option_id is not None:
            attempted_count += 1
            if response.selected_option_id == correct.id:
                marks = attempt.exam.positive_marks
            else:
                marks = attempt.exam.negative_marks
                wrong_count += 1
        response.marks_awarded = marks
        score += marks
        total_time += response.time_spent_seconds

    attempt.score = score
    attempt.max_score = attempt.exam.total_questions * attempt.exam.positive_marks
    attempt.attempted_count = attempted_count
    attempt.wrong_count = wrong_count
    attempt.total_time_seconds = min(
        total_time, attempt.exam.duration_minutes * 60
    )
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


def can_review_attempt(attempt: ExamAttempt, now: datetime | None = None) -> bool:
    if attempt.exam.exam_type == ExamType.PRACTICE:
        return True
    end_at = scheduled_end(attempt.exam)
    return bool(end_at and (aware_utc(now) or datetime.now(timezone.utc)) >= end_at)


def next_practice_attempt_number(db: Session, exam_id: int, student_id: int) -> int:
    value = db.scalar(
        select(func.max(ExamAttempt.attempt_number)).where(
            ExamAttempt.exam_id == exam_id, ExamAttempt.student_id == student_id
        )
    )
    return int(value or 0) + 1
