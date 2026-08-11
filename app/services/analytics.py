from __future__ import annotations

from statistics import median
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import AttemptStatus, Exam, ExamAttempt, Question, Response


FINAL_STATUSES = (AttemptStatus.SUBMITTED, AttemptStatus.AUTO_SUBMITTED)


def exam_analysis(db: Session, exam: Exam) -> dict[str, object]:
    attempts = list(
        db.scalars(
            select(ExamAttempt)
            .where(
                ExamAttempt.exam_id == exam.id,
                ExamAttempt.status.in_(FINAL_STATUSES),
            )
            .options(
                selectinload(ExamAttempt.student),
                selectinload(ExamAttempt.responses).selectinload(Response.selected_option),
            )
        )
    )
    questions = list(
        db.scalars(
            select(Question)
            .where(Question.exam_id == exam.id)
            .order_by(Question.position)
            .options(selectinload(Question.options))
        )
    )
    total_attempts = len(attempts)
    scores = [attempt.score or Decimal("0.00") for attempt in attempts]
    times = [attempt.total_time_seconds for attempt in attempts]

    question_rows: list[dict[str, object]] = []
    for question in questions:
        correct_option = next(option for option in question.options if option.is_correct)
        responses = [
            response
            for attempt in attempts
            for response in attempt.responses
            if response.question_id == question.id
        ]
        attempted = sum(response.selected_option_id is not None for response in responses)
        correct = sum(
            response.selected_option_id == correct_option.id for response in responses
        )
        skipped = total_attempts - attempted
        wrong = attempted - correct
        percent_correct = (100 * correct / total_attempts) if total_attempts else 0.0
        average_time = (
            sum(response.time_spent_seconds for response in responses) / total_attempts
            if total_attempts
            else 0.0
        )
        if percent_correct >= 70:
            difficulty = "Easy"
        elif percent_correct >= 30:
            difficulty = "Medium"
        else:
            difficulty = "Hard"
        distribution = {
            option.label: sum(response.selected_option_id == option.id for response in responses)
            for option in question.options
        }
        distribution["NA"] = skipped
        question_rows.append(
            {
                "position": question.position,
                "attempted": attempted,
                "correct": correct,
                "wrong": wrong,
                "skipped": skipped,
                "percent_correct": percent_correct,
                "average_time": average_time,
                "difficulty": difficulty,
                "distribution": distribution,
            }
        )

    leaderboard = sorted(
        attempts,
        key=lambda attempt: (-(attempt.score or 0), attempt.total_time_seconds, attempt.id),
    )
    leaderboard_rows = [
        {
            "rank": rank,
            "attempt_id": attempt.id,
            "student": attempt.student,
            "score": attempt.score or Decimal("0.00"),
            "max_score": attempt.max_score or exam.total_questions * exam.positive_marks,
            "percent": (
                100 * (attempt.score or 0) / attempt.max_score
                if attempt.max_score
                else 0.0
            ),
            "time": attempt.total_time_seconds,
            "attempted": attempt.attempted_count,
            "wrong": attempt.wrong_count,
        }
        for rank, attempt in enumerate(leaderboard, start=1)
    ]
    return {
        "total_attempts": total_attempts,
        "average_score": (
            sum(scores, Decimal("0.00")) / total_attempts
            if total_attempts
            else Decimal("0.00")
        ),
        "median_score": median(scores) if scores else Decimal("0.00"),
        "average_time": sum(times) / total_attempts if total_attempts else 0.0,
        "max_score": exam.total_questions * exam.positive_marks,
        "questions": question_rows,
        "leaderboard": leaderboard_rows,
    }


def attempt_review(attempt: ExamAttempt, reveal_answers: bool) -> dict[str, object]:
    question_map = {question.id: question for question in attempt.exam.questions}
    response_map = {response.question_id: response for response in attempt.responses}
    rows: list[dict[str, object]] = []
    for display_position, question_id in enumerate(attempt.question_order, start=1):
        question = question_map[int(question_id)]
        response = response_map[question.id]
        selected = response.selected_option
        correct = next(option for option in question.options if option.is_correct)
        rows.append(
            {
                "position": display_position,
                "text": question.text,
                "selected": selected.text if selected else "Not answered",
                "selected_label": selected.label if selected else "—",
                "correct": correct.text if reveal_answers else "Available after the exam ends",
                "correct_label": correct.label if reveal_answers else "—",
                "marks": response.marks_awarded if reveal_answers else None,
                "time": response.time_spent_seconds,
                "is_correct": (
                    response.selected_option_id == correct.id if reveal_answers else None
                ),
            }
        )
    return {"attempt": attempt, "rows": rows, "reveal_answers": reveal_answers}
