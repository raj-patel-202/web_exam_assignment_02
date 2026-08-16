# ExamFlow

ExamFlow is a database-backed web examination system built with FastAPI, PostgreSQL, SQLAlchemy, Jinja2, HTML, CSS, vanilla JavaScript, and WebSockets.

## Features

- Student, instructor, and examiner/invigilator roles with Argon2 password hashing and signed sessions.
- Scheduled exams with one attempt per student and a controlled joining window.
- Instructor-configurable decimal positive and negative marks per question.
- TXT question-paper upload using the existing `Q:`, `A)`–`D)`, `A:` format.
- Randomized question and option order saved separately for every attempt.
- Fullscreen-gated exam start, server-authoritative timers, autosave, automatic submission, and reconnect/resume support.
- Exams close automatically at their scheduled start time plus joining grace and duration.
- Instructors can permanently delete an exam and all related questions, assignments, attempts, answers, scores, results, and monitoring events.
- Instructor controls to manage invigilator assignments and permanently revoke invigilator accounts.
- Live monitoring of tab visibility, window focus, fullscreen exits, copy/paste, context menus, reloads, connection state, progress, and submissions.
- Results, question-level timing, difficulty analysis, option distribution, attempt history, and leaderboards.

Browser monitoring events are signals for human review. A normal webpage cannot prove cheating, inspect another device, or observe activity the browser does not expose.

## Get the project

```bash
git clone https://github.com/raj-patel-202/web_exam_assignment_02.git
cd web_exam_assignment_02
```

Choose either Docker or the direct Python setup below. Docker is the easiest option because it starts PostgreSQL automatically.

## Method 1: Run with Docker

### Requirements

- Docker Desktop with Docker Compose
- Ports `8000` and `5432` available on the computer

### Start the application

1. Create the local environment file.

   Windows PowerShell:

   ```powershell
   Copy-Item .env.example .env
   ```

   Linux or macOS:

   ```bash
   cp .env.example .env
   ```

2. Open `.env` and replace `SECRET_KEY` with a long random value. Do not commit this file.

3. Build and start the application and PostgreSQL:

   ```bash
   docker compose up --build
   ```

4. Open [http://localhost:8000](http://localhost:8000).

The application container waits for PostgreSQL, creates any missing database tables through SQLAlchemy, and then starts FastAPI. The first build may take a few minutes.

To run in the background:

```bash
docker compose up --build -d
docker compose logs -f app
```

To stop the containers without deleting database data:

```bash
docker compose down
```

## Method 2: Run directly without Docker

### Requirements

- Python 3.11 or newer
- PostgreSQL (PostgreSQL 16 is recommended)
- `psql`, pgAdmin, or another way to create a PostgreSQL user and database

### 1. Create the PostgreSQL database

Connect as a PostgreSQL administrator:

```bash
psql -U postgres
```

Run:

```sql
CREATE USER exam_user WITH PASSWORD 'exam_password';
CREATE DATABASE exam_system OWNER exam_user;
```

Exit `psql`:

```text
\q
```

If you choose different credentials, update `DATABASE_URL` in `.env` accordingly.

### 2. Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Linux or macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4. Configure the application

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Linux or macOS:

```bash
cp .env.example .env
```

Edit `.env` and verify these values:

```dotenv
SECRET_KEY=replace-this-with-a-long-random-secret
DATABASE_URL=postgresql+psycopg://exam_user:exam_password@localhost:5432/exam_system
APP_TIMEZONE=Asia/Kolkata
```

### 5. Start FastAPI

```bash
python -m uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000). Missing database tables are created automatically during startup. Stop the development server with `Ctrl+C`.

## First-time use

1. Open the registration page and create an instructor account.
2. Sign in as the instructor.
3. Upload a TXT question paper and choose its duration, future start time, and marking scheme.
4. Create an invigilator account from the instructor dashboard and assign it to an exam.
5. Register a separate student account to take the exam.

Instructors may also create accounts from the command line after starting the application once:

```bash
python -m scripts.bootstrap_user --username instructor1 --full-name "Lead Instructor" --password "replace-this-password" --role instructor
```

Valid roles are `student`, `instructor`, and `examiner`.

## Import the included sample exam

After creating an instructor account, run:

```bash
python -m scripts.import_legacy_exam --file sample_data/exam.txt --name "General Knowledge" --instructor instructor1 --duration 30 --start-at "2026-08-14 10:00"
```

The importer parses the file and stores normalized questions and options in PostgreSQL.

## TXT question format

```text
Q: Who was the first President of India?
A) Dr. Rajendra Prasad
B) Dr. S. Radhakrishnan
C) Jawaharlal Nehru
D) Mahatma Gandhi
A: A
```

Each question requires one `Q:` block, exactly four options, and a correct answer label. Question text may continue across multiple lines before option A. Uploaded TXT files are input only; application data is stored in PostgreSQL.

## Run tests

With the virtual environment active:

```bash
python -m pytest
```

## Useful Docker commands

```bash
docker compose ps
docker compose logs -f app
docker compose restart app
docker compose down
```

## Production notes

- Use a unique `SECRET_KEY`, HTTPS, and `SESSION_HTTPS_ONLY=true`.
- Put FastAPI behind a reverse proxy that supports WebSocket upgrades.
- Back up PostgreSQL regularly.
- Publish a clear monitoring and privacy policy.
- For multiple FastAPI processes, add a shared WebSocket fan-out layer such as PostgreSQL `LISTEN/NOTIFY` or Redis.
- This version creates missing tables automatically and does not include a schema-migration framework. Back up the database and manage schema changes manually before deploying model changes to an existing production database.
