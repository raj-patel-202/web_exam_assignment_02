from __future__ import annotations

from decimal import Decimal
from statistics import median

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
    
    def attempt_time(attempt: ExamAttempt) -> int:
        return sum(r.time_spent_seconds for r in attempt.responses)
        
    times = [attempt_time(attempt) for attempt in attempts]

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

    def get_attempted_count(attempt: ExamAttempt) -> int:
        return sum(r.selected_option_id is not None for r in attempt.responses)
        
    def get_wrong_count(attempt: ExamAttempt) -> int:
        question_map = {q.id: q for q in questions}
        wrong = 0
        for r in attempt.responses:
            if r.selected_option_id is not None:
                q = question_map[r.question_id]
                correct_opt = next(o for o in q.options if o.is_correct)
                if r.selected_option_id != correct_opt.id:
                    wrong += 1
        return wrong

    leaderboard = sorted(
        attempts,
        key=lambda attempt: (-(attempt.score or 0), attempt_time(attempt), attempt.id),
    )
    leaderboard_rows = [
        {
            "rank": rank,
            "attempt_id": attempt.id,
            "student": attempt.student,
            "score": attempt.score or Decimal("0.00"),
            "max_score": exam.total_questions * exam.positive_marks,
            "percent": (
                100 * (attempt.score or 0) / (exam.total_questions * exam.positive_marks)
                if (exam.total_questions * exam.positive_marks) > 0
                else 0.0
            ),
            "time": attempt_time(attempt),
            "attempted": get_attempted_count(attempt),
            "wrong": get_wrong_count(attempt),
        }
        for rank, attempt in enumerate(leaderboard, start=1)
    ]
    return {
        "total_attempts": total_attempts,
        "student_count": len({attempt.student_id for attempt in attempts}),
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
    response_map = {response.question_id: response for response in attempt.responses}
    rows: list[dict[str, object]] = []
    
    questions = sorted(attempt.exam.questions, key=lambda q: q.position)
    
    for display_position, question in enumerate(questions, start=1):
        response = response_map[question.id]
        ordered_options = sorted(question.options, key=lambda o: o.position)
        
        display_options = []
        selected_label = "—"
        correct_label = "—"
        selected_text = "Not answered"
        correct_text = "Available after the exam ends"
        for option_index, option in enumerate(ordered_options):
            display_label = chr(ord("A") + option_index)
            is_selected = response.selected_option_id == option.id
            if is_selected:
                selected_label = display_label
                selected_text = option.text
            if option.is_correct and reveal_answers:
                correct_label = display_label
                correct_text = option.text
            display_options.append(
                {
                    "label": display_label,
                    "text": option.text,
                    "is_selected": is_selected,
                    "is_correct": option.is_correct if reveal_answers else False,
                }
            )
        correct = next(option for option in question.options if option.is_correct)
        rows.append(
            {
                "position": display_position,
                "text": question.text,
                "selected": selected_text,
                "selected_label": selected_label,
                "correct": correct_text,
                "correct_label": correct_label,
                "options": display_options,
                "marks": response.marks_awarded if reveal_answers else None,
                "time": response.time_spent_seconds,
                "is_correct": (
                    response.selected_option_id == correct.id if reveal_answers else None
                ),
            }
        )
    return {"attempt": attempt, "rows": rows, "reveal_answers": reveal_answers}
