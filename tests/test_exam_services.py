from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import ExamStatus, ExamType, Question, User, UserRole
from app.security import hash_password
from app.services.analytics import exam_analysis
from app.services.exam_parser import parse_exam_text
from app.services.exam_service import (
    AttemptRuleError,
    activate_attempt,
    create_exam,
    create_or_resume_attempt,
    exam_availability,
    save_response,
    submit_attempt,
)


SOURCE = """Q: Capital of France?
A) Paris
B) London
C) Rome
D) Madrid
A: A

Q: Two plus two?
A) Three
B) Four
C) Five
D) Six
A: B
"""


def make_users(db):
    instructor = User(
        username="teacher",
        full_name="Test Teacher",
        password_hash=hash_password("StrongPass123"),
        role=UserRole.INSTRUCTOR,
    )
    student = User(
        username="student",
        full_name="Test Student",
        password_hash=hash_password("StrongPass123"),
        role=UserRole.STUDENT,
    )
    db.add_all([instructor, student])
    db.commit()
    return instructor, student


def make_exam(
    db,
    instructor,
    exam_type=ExamType.PRACTICE,
    start_at=None,
    positive_marks=Decimal("4.00"),
    negative_marks=Decimal("-1.00"),
):
    return create_exam(
        db,
        name=f"Test {exam_type.value}",
        exam_type=exam_type,
        start_at=start_at,
        duration_minutes=30,
        join_grace_minutes=5,
        positive_marks=positive_marks,
        negative_marks=negative_marks,
        source_filename="exam.txt",
        source_text=SOURCE,
        creator=instructor,
        questions=parse_exam_text(SOURCE),
    )


def test_scoring_time_and_analysis(db):
    instructor, student = make_users(db)
    exam = make_exam(
        db,
        instructor,
        positive_marks=Decimal("1.50"),
        negative_marks=Decimal("-0.50"),
    )
    attempt, created = create_or_resume_attempt(db, exam, student)
    assert created is True
    assert attempt.expires_at is None
    with pytest.raises(AttemptRuleError, match="fullscreen"):
        submit_attempt(db, attempt)
    activated_at = datetime.now(timezone.utc)
    activate_attempt(db, attempt, now=activated_at)
    assert attempt.expires_at.replace(tzinfo=timezone.utc) == activated_at + timedelta(minutes=30)

    questions = list(
        db.scalars(select(Question).where(Question.exam_id == exam.id).order_by(Question.position))
    )
    correct_first = next(option for option in questions[0].options if option.is_correct)
    wrong_second = next(option for option in questions[1].options if not option.is_correct)
    save_response(
        db,
        attempt,
        question_id=questions[0].id,
        selected_option_id=correct_first.id,
        time_spent_seconds=12,
        current_position=0,
    )
    save_response(
        db,
        attempt,
        question_id=questions[1].id,
        selected_option_id=wrong_second.id,
        time_spent_seconds=8,
        current_position=1,
    )
    submit_attempt(db, attempt)

    assert attempt.score == Decimal("1.00")
    assert attempt.max_score == Decimal("3.00")
    assert attempt.attempted_count == 2
    assert attempt.wrong_count == 1
    assert attempt.total_time_seconds == 20
    analysis = exam_analysis(db, exam)
    assert analysis["average_score"] == Decimal("1.00")
    assert analysis["leaderboard"][0]["student"].username == "student"


def test_scheduled_exam_allows_only_one_attempt(db):
    instructor, student = make_users(db)
    now = datetime.now(timezone.utc)
    exam = make_exam(
        db,
        instructor,
        exam_type=ExamType.SCHEDULED,
        start_at=now - timedelta(minutes=1),
    )
    attempt, _ = create_or_resume_attempt(db, exam, student, now=now)
    activate_attempt(db, attempt, now=now)
    submit_attempt(db, attempt, now=now + timedelta(minutes=1))
    with pytest.raises(AttemptRuleError, match="Already attempted"):
        create_or_resume_attempt(db, exam, student, now=now + timedelta(minutes=2))


def test_practice_exam_can_be_repeated(db):
    instructor, student = make_users(db)
    exam = make_exam(db, instructor)
    first, _ = create_or_resume_attempt(db, exam, student)
    activate_attempt(db, first)
    submit_attempt(db, first)
    second, created = create_or_resume_attempt(db, exam, student)
    assert created is True
    assert second.attempt_number == 2


def test_ended_exam_blocks_new_attempts_but_active_attempt_can_finish(db):
    instructor, student = make_users(db)
    exam = make_exam(db, instructor)
    attempt, _ = create_or_resume_attempt(db, exam, student)
    activate_attempt(db, attempt)

    exam.status = ExamStatus.ARCHIVED
    db.commit()
    assert exam_availability(exam, [attempt])["state"] == "resume"

    submit_attempt(db, attempt)
    with pytest.raises(AttemptRuleError, match="Not available"):
        create_or_resume_attempt(db, exam, student)
    assert exam_availability(exam, [attempt])["state"] == "closed"
