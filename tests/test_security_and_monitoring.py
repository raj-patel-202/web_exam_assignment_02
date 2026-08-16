import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.models import ExamType, ProctorEvent
from app.security import hash_password, verify_password
from app.services.exam_parser import parse_exam_text
from app.services.exam_service import activate_attempt, create_exam, create_or_resume_attempt
from app.services.live_monitor import ConnectionManager, exam_snapshot, record_event
from tests.test_exam_services import SOURCE, make_users


def test_argon2_password_hashing():
    password_hash = hash_password("StrongPass123")
    assert password_hash != "StrongPass123"
    assert verify_password("StrongPass123", password_hash)
    assert not verify_password("wrong", password_hash)


def test_proctor_event_is_persisted_and_counted(db):
    instructor, student = make_users(db)
    exam = create_exam(
        db,
        name="Monitored scheduled exam",
        exam_type=ExamType.SCHEDULED,
        start_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        duration_minutes=20,
        join_grace_minutes=5,
        source_filename="exam.txt",
        source_text=SOURCE,
        creator=instructor,
        questions=parse_exam_text(SOURCE),
    )
    attempt, _ = create_or_resume_attempt(db, exam, student)
    activate_attempt(db, attempt)
    event = record_event(db, attempt, "tab_hidden", {"state": "hidden"})
    assert event is not None
    assert attempt.suspicious_event_count == 1
    assert db.scalar(select(ProctorEvent).where(ProctorEvent.id == event.id)) is not None
    snapshot = exam_snapshot(db, exam.id)
    assert snapshot["attempts"][0]["flags"] == 1
    assert snapshot["events"][0]["event_type"] == "tab_hidden"


def test_invigilator_revocation_closes_only_the_target_connection():
    class FakeSocket:
        def __init__(self):
            self.accepted = False
            self.closed_code = None

        async def accept(self):
            self.accepted = True

        async def close(self, code=1000, reason=None):
            self.closed_code = code

    async def scenario():
        connection_manager = ConnectionManager()
        removed = FakeSocket()
        retained = FakeSocket()
        assert await connection_manager.connect_examiner(8, 21, removed)
        assert await connection_manager.connect_examiner(8, 22, retained)

        await connection_manager.revoke_examiner(21, exam_id=8)
        assert removed.closed_code == 4403
        assert retained.closed_code is None

        blocked = FakeSocket()
        assert not await connection_manager.connect_examiner(8, 21, blocked)
        assert blocked.closed_code == 4403

        await connection_manager.allow_examiner(8, 21)
        restored = FakeSocket()
        assert await connection_manager.connect_examiner(8, 21, restored)

    asyncio.run(scenario())


def test_student_connection_marks_only_a_real_reconnect():
    class FakeSocket:
        def __init__(self):
            self.accepted = False
            self.closed_code = None

        async def accept(self):
            self.accepted = True

        async def close(self, code=1000, reason=None):
            self.closed_code = code

    async def scenario():
        connection_manager = ConnectionManager()
        first = FakeSocket()
        replacement = FakeSocket()

        assert await connection_manager.connect_student(5, first) is False
        assert await connection_manager.connect_student(5, replacement) is True
        assert first.closed_code == 4001
        assert await connection_manager.disconnect_student(5, first) is False
        assert await connection_manager.disconnect_student(5, replacement) is True

    asyncio.run(scenario())


def test_browser_proctoring_uses_in_page_confirmation_and_coalesces_incidents():
    script = (Path(__file__).parents[1] / "app" / "static" / "js" / "exam.js").read_text(
        encoding="utf-8"
    )
    template = (
        Path(__file__).parents[1] / "app" / "templates" / "student" / "take_exam.html"
    ).read_text(encoding="utf-8")

    assert "let proctoringEnabled = false;" in script
    assert "let proctoringSuppressed = false;" in script
    assert "let pendingIncident = null;" in script
    assert "const incidentPriority" in script
    assert "queueProctorIncident(\"fullscreen_exit\")" in script
    assert "!proctoringEnabled || proctoringSuppressed" in script
    assert "window.confirm(" not in script
    assert 'proctorEvent("window_focus")' not in script
    assert 'proctorEvent("tab_visible")' not in script
    assert 'navigationEntry?.type === "reload" && examStarted' in script
    assert 'id="exam-submit-confirm"' in template
    assert 'aria-modal="true"' in template
    assert 'id="cancel-exam-submit"' in template
    assert 'id="confirm-exam-submit"' in template
