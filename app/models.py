from __future__ import annotations

import enum
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def enum_type(enum_class: type[enum.Enum], length: int = 32) -> Enum:
    return Enum(
        enum_class,
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,
        validate_strings=True,
        length=length,
    )


class UserRole(str, enum.Enum):
    STUDENT = "student"
    INSTRUCTOR = "instructor"
    EXAMINER = "examiner"


class ExamType(str, enum.Enum):
    PRACTICE = "practice"
    SCHEDULED = "scheduled"


class ExamStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class AttemptStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    AUTO_SUBMITTED = "auto_submitted"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(enum_type(UserRole), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    created_exams: Mapped[list[Exam]] = relationship(
        back_populates="creator", foreign_keys="Exam.created_by_id"
    )
    attempts: Mapped[list[ExamAttempt]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )
    examiner_assignments: Mapped[list[ExaminerAssignment]] = relationship(
        back_populates="examiner",
        cascade="all, delete-orphan",
        foreign_keys="ExaminerAssignment.examiner_id",
    )


class Exam(Base):
    __tablename__ = "exams"
    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="ck_exam_duration_positive"),
        CheckConstraint("join_grace_minutes >= 0", name="ck_exam_grace_nonnegative"),
        CheckConstraint("positive_marks > 0", name="ck_exam_positive_marks"),
        CheckConstraint("negative_marks <= 0", name="ck_exam_negative_marks"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    exam_type: Mapped[ExamType] = mapped_column(enum_type(ExamType), index=True)
    status: Mapped[ExamStatus] = mapped_column(
        enum_type(ExamStatus), default=ExamStatus.PUBLISHED, index=True
    )
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    join_grace_minutes: Mapped[int] = mapped_column(Integer, default=5)
    positive_marks: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), default=Decimal("4.00")
    )
    negative_marks: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), default=Decimal("-1.00")
    )
    total_questions: Mapped[int] = mapped_column(Integer)
    source_filename: Mapped[str] = mapped_column(String(255))
    source_text: Mapped[str] = mapped_column(Text)
    created_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    creator: Mapped[User] = relationship(
        back_populates="created_exams", foreign_keys=[created_by_id]
    )
    questions: Mapped[list[Question]] = relationship(
        back_populates="exam",
        cascade="all, delete-orphan",
        order_by="Question.position",
    )
    attempts: Mapped[list[ExamAttempt]] = relationship(
        back_populates="exam", cascade="all, delete-orphan"
    )
    examiner_assignments: Mapped[list[ExaminerAssignment]] = relationship(
        back_populates="exam", cascade="all, delete-orphan"
    )


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("exam_id", "position", name="uq_question_exam_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)

    exam: Mapped[Exam] = relationship(back_populates="questions")
    options: Mapped[list[QuestionOption]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionOption.position",
    )
    responses: Mapped[list[Response]] = relationship(back_populates="question")


class QuestionOption(Base):
    __tablename__ = "question_options"
    __table_args__ = (
        UniqueConstraint("question_id", "position", name="uq_option_question_position"),
        CheckConstraint("position >= 0 AND position <= 3", name="ck_option_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(1))
    text: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    question: Mapped[Question] = relationship(back_populates="options")


class ExaminerAssignment(Base):
    __tablename__ = "examiner_assignments"
    __table_args__ = (
        UniqueConstraint("exam_id", "examiner_id", name="uq_examiner_assignment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True
    )
    examiner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    assigned_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    exam: Mapped[Exam] = relationship(back_populates="examiner_assignments")
    examiner: Mapped[User] = relationship(
        back_populates="examiner_assignments", foreign_keys=[examiner_id]
    )


class ExamAttempt(Base):
    __tablename__ = "exam_attempts"
    __table_args__ = (
        UniqueConstraint(
            "exam_id", "student_id", "attempt_number", name="uq_exam_student_attempt"
        ),
        CheckConstraint("attempt_number > 0", name="ck_attempt_number_positive"),
        Index("ix_attempt_exam_status", "exam_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[AttemptStatus] = mapped_column(
        enum_type(AttemptStatus), default=AttemptStatus.IN_PROGRESS, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    question_order: Mapped[list[int]] = mapped_column(JSON, default=list)
    option_orders: Mapped[dict[str, list[int]]] = mapped_column(JSON, default=dict)
    current_question_position: Mapped[int] = mapped_column(Integer, default=0)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    max_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    attempted_count: Mapped[int] = mapped_column(Integer, default=0)
    wrong_count: Mapped[int] = mapped_column(Integer, default=0)
    total_time_seconds: Mapped[int] = mapped_column(Integer, default=0)
    suspicious_event_count: Mapped[int] = mapped_column(Integer, default=0)

    exam: Mapped[Exam] = relationship(back_populates="attempts")
    student: Mapped[User] = relationship(back_populates="attempts")
    responses: Mapped[list[Response]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )
    proctor_events: Mapped[list[ProctorEvent]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )


class Response(Base):
    __tablename__ = "responses"
    __table_args__ = (
        UniqueConstraint("attempt_id", "question_id", name="uq_attempt_question_response"),
        CheckConstraint("time_spent_seconds >= 0", name="ck_response_time_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("exam_attempts.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), index=True
    )
    selected_option_id: Mapped[int | None] = mapped_column(
        ForeignKey("question_options.id", ondelete="SET NULL")
    )
    time_spent_seconds: Mapped[int] = mapped_column(Integer, default=0)
    marks_awarded: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), default=Decimal("0.00")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    attempt: Mapped[ExamAttempt] = relationship(back_populates="responses")
    question: Mapped[Question] = relationship(back_populates="responses")
    selected_option: Mapped[QuestionOption | None] = relationship()


class ProctorEvent(Base):
    __tablename__ = "proctor_events"
    __table_args__ = (Index("ix_proctor_attempt_occurred", "attempt_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("exam_attempts.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[int] = mapped_column(Integer, default=1)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    client_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    attempt: Mapped[ExamAttempt] = relationship(back_populates="proctor_events")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
