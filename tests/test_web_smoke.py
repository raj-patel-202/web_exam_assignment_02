import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database
import app.main
from app.web import templates
from app.database import Base
from app.models import (
    AuditLog,
    Exam,
    ExamAttempt,
    ExamType,
    ExaminerAssignment,
    ProctorEvent,
    Response,
    User,
    UserRole,
)
from app.security import hash_password
from app.services.exam_parser import parse_exam_text
from app.services.exam_service import (
    activate_attempt,
    create_exam,
    create_or_resume_attempt,
    save_response,
    submit_attempt,
)
from app.services.live_monitor import record_event
from tests.test_exam_services import SOURCE


def test_public_page_and_student_registration(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(app.database, "SessionLocal", testing_session)
    monkeypatch.setattr(app.main, "SessionLocal", testing_session)

    with TestClient(app.main.app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "Run exams without juggling tools" in home.text
        assert 'id="app-message-modal"' in home.text

        register_page = client.get("/auth/register")
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', register_page.text).group(1)
        response = client.post(
            "/auth/register",
            data={
                "csrf_token": csrf,
                "full_name": "Web Student",
                "username": "webstudent",
                "password": "StrongPass123",
                "role": "student",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/student"
        dashboard = client.get("/student")
        assert dashboard.status_code == 200
        assert "Welcome, Web" in dashboard.text
        assert '<a class="brand" href="/student"' in dashboard.text
        assert ">Overview</a>" not in dashboard.text
        assert 'href="/student/exams"' in dashboard.text
        assert 'href="/student/results"' in dashboard.text
        with testing_session() as db:
            instructor = User(
                full_name="Catalogue Instructor",
                username="catalogue_teacher",
                password_hash=hash_password("StrongPass123"),
                role=UserRole.INSTRUCTOR,
            )
            db.add(instructor)
            db.commit()
            create_exam(
                db,
                name="Upcoming Web Exam",
                exam_type=ExamType.SCHEDULED,
                start_at=datetime.now(timezone.utc) + timedelta(hours=1),
                duration_minutes=30,
                join_grace_minutes=5,
                source_filename="exam.txt",
                source_text=SOURCE,
                creator=instructor,
                questions=parse_exam_text(SOURCE),
            )
        catalogue = client.get("/student/exams")
        assert catalogue.status_code == 200
        assert 'href="/student/exams" class="active" aria-current="page"' in catalogue.text
        assert "Upcoming Web Exam" in catalogue.text
        assert 'class="student-start-countdown"' in catalogue.text
        assert "Starts in ·" in catalogue.text
        results_page = client.get("/student/results")
        assert results_page.status_code == 200
        assert 'href="/student/results" class="active" aria-current="page"' in results_page.text

    with testing_session() as db:
        user = db.scalar(select(User).where(User.username == "webstudent"))
        assert user is not None
        assert user.role == UserRole.STUDENT


def test_instructor_can_upload_txt_exam(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(app.database, "SessionLocal", testing_session)
    monkeypatch.setattr(app.main, "SessionLocal", testing_session)

    with TestClient(app.main.app) as client:
        register_page = client.get("/auth/register")
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', register_page.text).group(1)
        registered = client.post(
            "/auth/register",
            data={
                "csrf_token": csrf,
                "full_name": "Web Instructor",
                "username": "webteacher",
                "password": "StrongPass123",
                "role": "instructor",
            },
            follow_redirects=False,
        )
        assert registered.status_code == 303
        dashboard = client.get("/instructor")
        assert dashboard.status_code == 200
        assert "Welcome, Web" in dashboard.text
        assert '<a class="brand" href="/instructor"' in dashboard.text
        assert 'href="/instructor/exams/new"' in dashboard.text
        assert 'href="/instructor/exams"' in dashboard.text
        assert 'href="/instructor/performance"' in dashboard.text
        assert 'href="/instructor/invigilators"' in dashboard.text
        upload_page = client.get("/instructor/exams/new")
        assert upload_page.status_code == 200
        assert 'href="/instructor/exams/new" class="active" aria-current="page"' in upload_page.text
        assert 'type="datetime-local"' in upload_page.text
        assert "In 2 minutes" in upload_page.text
        assert "In 5 minutes" in upload_page.text
        assert "Tomorrow at 9 AM" not in upload_page.text
        assert "Times are shown in" not in upload_page.text
        assert "TXT format" not in upload_page.text
        assert "For example" not in upload_page.text
        assert "← Home" in upload_page.text
        assert '<a class="button button-ghost" href="/instructor">Cancel</a>' in upload_page.text
        assert "Practice" not in upload_page.text
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', upload_page.text).group(1)
        scheduled_start = (
            datetime.now(ZoneInfo("Asia/Calcutta")) + timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M")
        uploaded = client.post(
            "/instructor/exams/new",
            data={
                "csrf_token": csrf,
                "name": "Web Upload",
                "duration_minutes": "30",
                "join_grace_minutes": "5",
                "positive_marks": "1.5",
                "negative_marks": "-0.25",
                "start_at": scheduled_start,
            },
            files={"exam_file": ("exam.txt", SOURCE, "text/plain")},
            follow_redirects=False,
        )
        assert uploaded.status_code == 303
        detail = client.get(uploaded.headers["location"])
        assert detail.status_code == 200
        assert "Web Upload" in detail.text
        assert "was parsed and published" in detail.text
        assert "← My exams" not in detail.text
        assert "Assigned invigilators" in detail.text
        assert "New assignment" in detail.text
        assert "← Uploaded exams" in detail.text
        assert "Scheduled · created" not in detail.text
        assert "5 min grace" in detail.text
        assert 'id="manage-exam-time"' in detail.text
        assert "summary-chip summary-questions" in detail.text
        assert "Duration · 30 min" in detail.text
        assert "Marks · +1.5 / -0.25" in detail.text
        assert 'class="manage-exam-timer timer-published" id="manage-exam-time"' in detail.text
        assert "Starts in ·" in detail.text
        assert 'id="manage-exam-status">Status · Published</span>' in detail.text
        assert "compact-metric-grid" not in detail.text
        assert "Show question paper" in detail.text
        assert 'id="question-paper-list" hidden' in detail.text
        assert "View results" not in detail.text
        analysis = client.get(f"{uploaded.headers['location']}/analysis")
        assert analysis.status_code == 200
        assert 'href="/instructor/performance" class="active" aria-current="page"' in analysis.text
        assert "analysis-summary-capsules" in analysis.text
        assert "summary-chip analysis-students" in analysis.text
        assert "summary-chip analysis-average" in analysis.text
        assert "summary-chip analysis-time" in analysis.text
        assert "summary-chip analysis-duration" in analysis.text
        assert "summary-chip analysis-questions" in analysis.text
        assert "summary-chip analysis-marks" in analysis.text
        assert "Duration · 30 min" in analysis.text
        assert "Marks · +1.5 / -0.25" in analysis.text
        assert "metric-grid" not in analysis.text
        dashboard_after_upload = client.get("/instructor")
        assert dashboard_after_upload.status_code == 200
        assert "Web Upload" not in dashboard_after_upload.text
        assert "Create exams, review results, and manage invigilators." not in dashboard_after_upload.text
        assert "Add exam" not in dashboard_after_upload.text
        assert "Published exams" not in dashboard_after_upload.text
        assert "Total attempts" not in dashboard_after_upload.text
        assert "Examiner accounts" not in dashboard_after_upload.text
        uploaded_exams = client.get("/instructor/exams")
        assert uploaded_exams.status_code == 200
        assert "← Home" in uploaded_exams.text
        assert "Web Upload" in uploaded_exams.text
        assert "Published" in uploaded_exams.text
        assert "exam-fact-pill fact-questions" in uploaded_exams.text
        assert "exam-fact-pill fact-created" in uploaded_exams.text
        assert "exam-fact-pill fact-starts" in uploaded_exams.text
        assert "exam-fact-pill fact-ends" in uploaded_exams.text
        assert "exam-fact-pill fact-students" in uploaded_exams.text
        assert "Students attempted" in uploaded_exams.text
        assert "View detailed results" not in uploaded_exams.text
        performance_page = client.get("/instructor/performance")
        assert performance_page.status_code == 200
        assert "← Home" in performance_page.text
        assert "Web Upload" in performance_page.text
        assert "Published" in performance_page.text
        assert ">scheduled<" not in performance_page.text.lower()
        assert "View detailed results" in performance_page.text
        assert 'href="/instructor/performance" class="active" aria-current="page"' in performance_page.text
        assert "exam-fact-pill fact-students" in performance_page.text
        assert "fact-submissions" not in performance_page.text
        assert "exam-fact-pill fact-duration" in performance_page.text
        assert "exam-fact-pill fact-score" in performance_page.text
        assert "exam-fact-pill fact-average-time" in performance_page.text
        assert "exam-fact-pill fact-marks" in performance_page.text
        assert "Submissions" not in performance_page.text

        created_examiner = client.post(
            "/instructor/examiners/new",
            data={
                "csrf_token": csrf,
                "full_name": "Web Examiner",
                "username": "webexaminer",
                "password": "StrongPass123",
            },
            follow_redirects=False,
        )
        assert created_examiner.status_code == 303
        invigilators_page = client.get("/instructor/invigilators")
        assert invigilators_page.status_code == 200
        assert "← Home" in invigilators_page.text
        assert "Manage invigilators" in invigilators_page.text
        assert 'href="/instructor/invigilators" class="active" aria-current="page"' in invigilators_page.text
        assert "Web Examiner" in invigilators_page.text
        assert "immediately removes its monitoring and invigilation-report access" in invigilators_page.text
        created_second_examiner = client.post(
            "/instructor/examiners/new",
            data={
                "csrf_token": csrf,
                "full_name": "Second Invigilator",
                "username": "second_invigilator",
                "password": "StrongPass123",
            },
            follow_redirects=False,
        )
        assert created_second_examiner.status_code == 303
        with testing_session() as db:
            examiner = db.scalar(select(User).where(User.username == "webexaminer"))
            second_examiner = db.scalar(
                select(User).where(User.username == "second_invigilator")
            )
            exam = db.scalar(select(Exam).where(Exam.name == "Web Upload"))
            exam.start_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
            examiner_id = examiner.id
            second_examiner_id = second_examiner.id
            exam_id = exam.id
            student = User(
                full_name="Delete Test Student",
                username="delete_student",
                password_hash=hash_password("StrongPass123"),
                role=UserRole.STUDENT,
            )
            db.add(student)
            db.commit()
            attempt, _ = create_or_resume_attempt(db, exam, student)
            activate_attempt(db, attempt)
            record_event(db, attempt, "tab_hidden")
            attempt_id = attempt.id
            response_ids = list(
                db.scalars(select(Response.id).where(Response.attempt_id == attempt.id))
            )
            event_ids = list(
                db.scalars(select(ProctorEvent.id).where(ProctorEvent.attempt_id == attempt.id))
            )
        assigned = client.post(
            f"/instructor/exams/{exam_id}/assign-examiner",
            data={"csrf_token": csrf, "examiner_id": str(examiner_id)},
            follow_redirects=False,
        )
        assert assigned.status_code == 303
        assigned_second = client.post(
            f"/instructor/exams/{exam_id}/assign-examiner",
            data={"csrf_token": csrf, "examiner_id": str(second_examiner_id)},
            follow_redirects=False,
        )
        assert assigned_second.status_code == 303
        with testing_session() as db:
            assigned_ids = set(
                db.scalars(
                    select(ExaminerAssignment.examiner_id).where(
                        ExaminerAssignment.exam_id == exam_id
                    )
                )
            )
            assert assigned_ids == {examiner_id, second_examiner_id}

        logged_out = client.post(
            "/auth/logout", data={"csrf_token": csrf}, follow_redirects=False
        )
        assert logged_out.status_code == 303
        login_page = client.get("/auth/login")
        login_csrf = re.search(
            r'name="csrf-token" content="([^"]+)"', login_page.text
        ).group(1)
        logged_in = client.post(
            "/auth/login",
            data={
                "csrf_token": login_csrf,
                "username": "webexaminer",
                "password": "StrongPass123",
            },
            follow_redirects=False,
        )
        assert logged_in.headers["location"] == "/examiner"
        examiner_dashboard = client.get("/examiner")
        assert examiner_dashboard.status_code == 200
        assert "type-scheduled" not in examiner_dashboard.text
        assert "examiner-exam-capsules" in examiner_dashboard.text
        assert "Duration · 30 min" in examiner_dashboard.text
        assert "Questions · 2" in examiner_dashboard.text
        assert "Starts ·" in examiner_dashboard.text
        assert "Ends in ·" in examiner_dashboard.text
        assert "Status · Running" in examiner_dashboard.text
        examiner_csrf = re.search(
            r'name="csrf-token" content="([^"]+)"', examiner_dashboard.text
        ).group(1)
        monitor = client.get(f"/examiner/exams/{exam_id}/monitor")
        assert monitor.status_code == 200
        assert '<a class="back-link" href="/examiner">← Monitoring rooms</a>' in monitor.text
        assert "Recent events" in monitor.text
        assert "monitor-summary-capsules" in monitor.text
        assert 'class="metric-grid"' not in monitor.text
        assert "Duration · 30 min" in monitor.text
        assert "Questions · 2" in monitor.text
        assert "Ends in ·" in monitor.text

        examiner_logout = client.post(
            "/auth/logout", data={"csrf_token": examiner_csrf}, follow_redirects=False
        )
        assert examiner_logout.status_code == 303
        instructor_login_page = client.get("/auth/login")
        instructor_csrf = re.search(
            r'name="csrf-token" content="([^"]+)"', instructor_login_page.text
        ).group(1)
        instructor_login = client.post(
            "/auth/login",
            data={
                "csrf_token": instructor_csrf,
                "username": "webteacher",
                "password": "StrongPass123",
            },
            follow_redirects=False,
        )
        assert instructor_login.headers["location"] == "/instructor"
        instructor_dashboard = client.get("/instructor")
        instructor_action_csrf = re.search(
            r'name="csrf-token" content="([^"]+)"', instructor_dashboard.text
        ).group(1)

        active_detail = client.get(f"/instructor/exams/{exam_id}")
        assert "Delete exam" in active_detail.text
        assert "End exam" not in active_detail.text
        assert "Running" in active_detail.text
        assert "Ends in ·" in active_detail.text
        assert 'disabled title="Wait for all active candidates to finish"' not in active_detail.text
        unassigned = client.post(
            f"/instructor/exams/{exam_id}/unassign-examiner/{examiner_id}",
            data={"csrf_token": instructor_action_csrf},
            follow_redirects=False,
        )
        assert unassigned.status_code == 303
        deleted = client.post(
            f"/instructor/examiners/{examiner_id}/delete",
            data={"csrf_token": instructor_action_csrf},
            follow_redirects=False,
        )
        assert deleted.status_code == 303

        unassigned_second = client.post(
            f"/instructor/exams/{exam_id}/unassign-examiner/{second_examiner_id}",
            data={"csrf_token": instructor_action_csrf},
            follow_redirects=False,
        )
        assert unassigned_second.status_code == 303

        with testing_session() as db:
            exam = db.get(Exam, exam_id)
            assert exam is not None
            assert exam.total_questions == 2
            assert exam.positive_marks == Decimal("1.50")
            assert exam.negative_marks == Decimal("-0.25")
            exam.start_at = datetime.now(timezone.utc) - timedelta(minutes=36)
            db.commit()

        ended_detail = client.get(f"/instructor/exams/{exam_id}")
        assert "Status · Ended" in ended_detail.text
        assert "New invigilators can no longer be assigned." in ended_detail.text
        assert f'action="/instructor/exams/{exam_id}/assign-examiner"' not in ended_detail.text
        blocked_assignment = client.post(
            f"/instructor/exams/{exam_id}/assign-examiner",
            data={
                "csrf_token": instructor_action_csrf,
                "examiner_id": str(second_examiner_id),
            },
            follow_redirects=False,
        )
        assert blocked_assignment.status_code == 303
        blocked_detail = client.get(blocked_assignment.headers["location"])
        assert "New invigilators cannot be assigned after an exam has ended." in blocked_detail.text
        with testing_session() as db:
            assert db.scalar(
                select(ExaminerAssignment.id).where(
                    ExaminerAssignment.exam_id == exam_id,
                    ExaminerAssignment.examiner_id == second_examiner_id,
                )
            ) is None

        removed_exam = client.post(
            f"/instructor/exams/{exam_id}/delete",
            data={"csrf_token": instructor_action_csrf},
            follow_redirects=False,
        )
        assert removed_exam.status_code == 303
        assert removed_exam.headers["location"] == "/instructor/exams"

    with testing_session() as db:
        assert db.get(Exam, exam_id) is None
        assert db.get(ExamAttempt, attempt_id) is None
        assert all(db.get(Response, response_id) is None for response_id in response_ids)
        assert all(db.get(ProctorEvent, event_id) is None for event_id in event_ids)
        assert db.get(User, examiner_id) is None
        assert db.scalar(
            select(ExaminerAssignment).where(
                ExaminerAssignment.exam_id == exam_id,
                ExaminerAssignment.examiner_id == examiner_id,
            )
        ) is None
        assert db.scalar(
            select(AuditLog).where(
                AuditLog.entity_type == "exam", AuditLog.entity_id == exam_id
            )
        ) is None


def test_student_results_hide_scoring_until_exam_ends(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(app.database, "SessionLocal", testing_session)
    monkeypatch.setattr(app.main, "SessionLocal", testing_session)

    with TestClient(app.main.app) as client:
        register_page = client.get("/auth/register")
        csrf = re.search(
            r'name="csrf-token" content="([^"]+)"', register_page.text
        ).group(1)
        registered = client.post(
            "/auth/register",
            data={
                "csrf_token": csrf,
                "full_name": "Result Student",
                "username": "result_student",
                "password": "StrongPass123",
                "role": "student",
            },
            follow_redirects=False,
        )
        assert registered.status_code == 303

        with testing_session() as db:
            student = db.scalar(select(User).where(User.username == "result_student"))
            instructor = User(
                full_name="Result Instructor",
                username="result_instructor",
                password_hash=hash_password("StrongPass123"),
                role=UserRole.INSTRUCTOR,
            )
            db.add(instructor)
            db.commit()
            exam = create_exam(
                db,
                name="Protected Result Exam",
                exam_type=ExamType.SCHEDULED,
                start_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                duration_minutes=30,
                join_grace_minutes=5,
                positive_marks=Decimal("4.00"),
                negative_marks=Decimal("-1.00"),
                source_filename="exam.txt",
                source_text=SOURCE,
                creator=instructor,
                questions=parse_exam_text(SOURCE),
            )
            attempt, _ = create_or_resume_attempt(db, exam, student)
            activate_attempt(db, attempt)
            question = next(
                question
                for question in exam.questions
                if question.id == int(attempt.question_order[0])
            )
            wrong_option = next(option for option in question.options if not option.is_correct)
            save_response(
                db,
                attempt,
                question_id=question.id,
                selected_option_id=wrong_option.id,
                time_spent_seconds=12,
                current_position=0,
            )
            submit_attempt(db, attempt)
            attempt_id = attempt.id
            exam_id = exam.id

        result_list = client.get("/student/results")
        assert result_list.status_code == 200
        assert "Attempt 1" not in result_list.text
        assert "Duration" in result_list.text
        assert "Questions" in result_list.text
        assert "Marks" in result_list.text
        assert "Held on ·" in result_list.text
        assert "Submitted ·" in result_list.text
        assert "+4 / -1" in result_list.text
        assert "After exam ends" in result_list.text

        pending_result = client.get(f"/student/attempts/{attempt_id}/result")
        assert pending_result.status_code == 200
        assert '<a class="back-link" href="/student/results">← Results</a>' in pending_result.text
        assert "Attempted · 1 / 2" in pending_result.text
        assert "Duration · 30 min" in pending_result.text
        assert "Questions · 2" in pending_result.text
        assert "Marks · +4 / -1" in pending_result.text
        assert "Selected" in pending_result.text
        assert "Correct answer" not in pending_result.text
        assert "Unanswered" not in pending_result.text
        assert "Wrong ·" not in pending_result.text
        assert "Monitoring flags" not in pending_result.text
        assert 'class="score-orb"' not in pending_result.text

        with testing_session() as db:
            exam = db.get(Exam, exam_id)
            exam.start_at = datetime.now(timezone.utc) - timedelta(minutes=36)
            db.commit()

        released_result = client.get(f"/student/attempts/{attempt_id}/result")
        assert released_result.status_code == 200
        assert 'class="score-orb"' in released_result.text
        assert "Correct answer" in released_result.text


def test_all_jinja_templates_compile():
    for template_name in templates.env.list_templates(extensions=["html"]):
        templates.get_template(template_name)
