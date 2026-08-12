import re
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database
import app.main
from app.web import templates
from app.database import Base
from app.models import Exam, ExaminerAssignment, ExamStatus, User, UserRole
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
        assert "Hello, Web" in dashboard.text
        catalogue = client.get("/student/exams")
        assert catalogue.status_code == 200

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
        upload_page = client.get("/instructor/exams/new")
        assert upload_page.status_code == 200
        assert 'id="start-month"' in upload_page.text
        assert "Marks per question" in upload_page.text
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', upload_page.text).group(1)
        uploaded = client.post(
            "/instructor/exams/new",
            data={
                "csrf_token": csrf,
                "name": "Web Upload",
                "exam_type": "practice",
                "duration_minutes": "30",
                "join_grace_minutes": "5",
                "positive_marks": "1.5",
                "negative_marks": "-0.25",
                "start_at": "",
            },
            files={"exam_file": ("exam.txt", SOURCE, "text/plain")},
            follow_redirects=False,
        )
        assert uploaded.status_code == 303
        detail = client.get(uploaded.headers["location"])
        assert detail.status_code == 200
        assert "Web Upload" in detail.text
        assert "was parsed and published" in detail.text
        analysis = client.get(f"{uploaded.headers['location']}/analysis")
        assert analysis.status_code == 200
        dashboard_after_upload = client.get("/instructor")
        assert dashboard_after_upload.status_code == 200
        assert "Web Upload" in dashboard_after_upload.text
        assert "Published exams" not in dashboard_after_upload.text
        assert "Total attempts" not in dashboard_after_upload.text
        assert "Examiner accounts" not in dashboard_after_upload.text

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
        with testing_session() as db:
            examiner = db.scalar(select(User).where(User.username == "webexaminer"))
            exam = db.scalar(select(Exam).where(Exam.name == "Web Upload"))
            examiner_id = examiner.id
            exam_id = exam.id
        assigned = client.post(
            f"/instructor/exams/{exam_id}/assign-examiner",
            data={"csrf_token": csrf, "examiner_id": str(examiner_id)},
            follow_redirects=False,
        )
        assert assigned.status_code == 303

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
        examiner_csrf = re.search(
            r'name="csrf-token" content="([^"]+)"', examiner_dashboard.text
        ).group(1)
        monitor = client.get(f"/examiner/exams/{exam_id}/monitor")
        assert monitor.status_code == 200
        assert "Recent events" in monitor.text

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

        ended = client.post(
            f"/instructor/exams/{exam_id}/end",
            data={"csrf_token": instructor_action_csrf},
            follow_redirects=False,
        )
        assert ended.status_code == 303
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

    with testing_session() as db:
        exam = db.scalar(select(Exam).where(Exam.name == "Web Upload"))
        assert exam is not None
        assert exam.total_questions == 2
        assert exam.positive_marks == Decimal("1.50")
        assert exam.negative_marks == Decimal("-0.25")
        assert exam.status == ExamStatus.ARCHIVED
        assert db.get(User, examiner_id) is None
        assert db.scalar(
            select(ExaminerAssignment).where(
                ExaminerAssignment.exam_id == exam_id,
                ExaminerAssignment.examiner_id == examiner_id,
            )
        ) is None


def test_all_jinja_templates_compile():
    for template_name in templates.env.list_templates(extensions=["html"]):
        templates.get_template(template_name)
