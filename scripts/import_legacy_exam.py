import argparse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Exam, ExamType, User, UserRole
from app.services.exam_parser import parse_exam_text
from app.services.exam_service import create_exam


def main() -> None:
    parser = argparse.ArgumentParser(description="Import and schedule a TXT question paper.")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--instructor", required=True, help="Existing instructor username")
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--start-at", required=True, help="Future time in YYYY-MM-DD HH:MM format")
    parser.add_argument("--grace", type=int, default=5)
    args = parser.parse_args()

    source_text = args.file.read_text(encoding="utf-8-sig")
    parsed = parse_exam_text(source_text)
    start_at = datetime.strptime(args.start_at, "%Y-%m-%d %H:%M").replace(
        tzinfo=ZoneInfo(get_settings().app_timezone)
    )
    if start_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise SystemExit("--start-at must be in the future.")

    with SessionLocal() as db:
        instructor = db.scalar(
            select(User).where(
                User.username == args.instructor.strip().lower(),
                User.role == UserRole.INSTRUCTOR,
            )
        )
        if not instructor:
            raise SystemExit("Instructor account was not found.")
        if db.scalar(select(Exam.id).where(Exam.name == args.name.strip())):
            raise SystemExit("An exam with that name already exists.")
        exam = create_exam(
            db,
            name=args.name,
            exam_type=ExamType.SCHEDULED,
            start_at=start_at,
            duration_minutes=args.duration,
            join_grace_minutes=args.grace,
            source_filename=args.file.name,
            source_text=source_text,
            creator=instructor,
            questions=parsed,
        )
        print(f"Imported '{exam.name}' with {exam.total_questions} questions.")


if __name__ == "__main__":
    main()
