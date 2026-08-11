# Exam Sentinel

Exam Sentinel is the web-based Python replacement for the original C++ terminal exam project. It uses FastAPI, PostgreSQL, SQLAlchemy, Alembic, Jinja2, HTML, CSS, vanilla JavaScript, and WebSockets. The legacy C++ directories outside this folder remain untouched.

## Included functionality

- Student, instructor, and examiner roles with Argon2 password hashing and signed sessions.
- Existing `Q:`, `A)`–`D)`, `A:` TXT input format, including multiline questions.
- Practice exams with unlimited attempts and scheduled exams with a configurable joining window and one attempt.
- Randomized question and option order persisted per attempt.
- Fullscreen-gated, server-authoritative timer with exam blurring on fullscreen exit, autosave, automatic submission, reconnect/resume, and browser recovery buffer.
- Instructor-configurable decimal positive and negative marks per question, answer review, attempt history, question timing, difficulty, option distribution, and leaderboards.
- Instructor-controlled examiner accounts and per-exam assignments.
- Instructor controls to end an exam without interrupting active candidates, remove a per-exam invigilator assignment, or permanently delete an invigilator account and revoke its live access.
- Live examiner monitoring for tab visibility, window focus, fullscreen exit, copy/paste, context menu, reload, heartbeat, connection loss, progress, and submission.
- PostgreSQL-backed answers, attempts, results, monitoring events, and audit records. TXT files are input only.

Browser monitoring signals are evidence for human review. A normal webpage cannot prove cheating, inspect another device, or see activity that the browser does not expose.

## Quick start with Docker

1. Copy `.env.example` to `.env` and replace `SECRET_KEY` with a long random value.
2. Start PostgreSQL and the application:

   ```bash
   docker compose up --build
   ```

3. Open `http://localhost:8000`.

The application container applies the Alembic migration before starting. Student and instructor accounts can be created from the registration page. Instructors create examiner accounts from their dashboard.

## Local development

Use Python 3.11 or later and a running PostgreSQL server.

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
copy .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

On Linux or macOS, activate the environment with `source .venv/bin/activate` and copy the environment file with `cp`.

## Account bootstrap

To provision an account directly after applying migrations:

```bash
python -m scripts.bootstrap_user --username instructor1 --full-name "Lead Instructor" --password "replace-this-password" --role instructor
```

Valid roles are `student`, `instructor`, and `examiner`.

## Importing the original sample exam

Create an instructor account, then run:

```bash
python -m scripts.import_legacy_exam --file ../data/exams/exam.txt --name "General Knowledge" --instructor instructor1 --type practice --duration 30
```

The importer uses the same parser as the instructor upload page and saves normalized questions and options in PostgreSQL.

## TXT format

```text
Q: Who was the first President of India?
A) Dr. Rajendra Prasad
B) Dr. S. Radhakrishnan
C) Jawaharlal Nehru
D) Mahatma Gandhi
A: A
```

Each question requires exactly one `Q:` block, four options, and a correct answer label. Question text may continue across lines before option A.

## Database migrations and tests

```bash
alembic upgrade head
pytest
```

The initial migration creates users, exams, questions, options, assignments, attempts, responses, proctor events, and audit logs. Tests cover parsing, scoring, practice/scheduled attempt rules, password hashing, analytics, and monitoring-event persistence.

## Production notes

- Set a unique `SECRET_KEY`, use HTTPS, and set `SESSION_HTTPS_ONLY=true`.
- Put FastAPI behind a reverse proxy that supports WebSocket upgrades.
- Back up PostgreSQL regularly.
- Keep examiner access assignment-based and publish a clear monitoring/privacy policy.
- For a multi-process deployment, add a shared WebSocket fan-out layer such as PostgreSQL `LISTEN/NOTIFY` or Redis; database event persistence already remains consistent.
