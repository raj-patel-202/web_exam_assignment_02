from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.models import (
    AttemptStatus,
    ExamAttempt,
    ExaminerAssignment,
    Response,
    User,
    UserRole,
)
from app.services.live_monitor import (
    attempt_summary,
    exam_snapshot,
    manager,
    record_event,
)


router = APIRouter(tags=["live monitoring"])


def websocket_user(websocket: WebSocket, db):
    user_id = websocket.session.get("user_id")
    if not user_id:
        return None
    return db.get(User, int(user_id))


def same_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    return urlsplit(origin).netloc.lower() == (websocket.headers.get("host") or "").lower()


def event_message(event, attempt: ExamAttempt) -> dict:
    return {
        "type": "proctor_event",
        "event": {
            "id": event.id,
            "attempt_id": attempt.id,
            "student_name": attempt.student.full_name,
            "event_type": event.event_type,
            "severity": event.severity,
            "occurred_at": event.occurred_at.isoformat(),
            "details": event.details,
        },
    }


@router.websocket("/ws/student/attempts/{attempt_id}")
async def student_socket(websocket: WebSocket, attempt_id: int):
    db = SessionLocal()
    attempt: ExamAttempt | None = None
    connected = False
    try:
        if not same_origin(websocket):
            await websocket.close(code=4403)
            return
        user = websocket_user(websocket, db)
        attempt = db.scalar(
            select(ExamAttempt)
            .where(ExamAttempt.id == attempt_id)
            .options(
                selectinload(ExamAttempt.student),
                selectinload(ExamAttempt.exam),
                selectinload(ExamAttempt.responses),
            )
        )
        if (
            not user
            or user.role != UserRole.STUDENT
            or not attempt
            or attempt.student_id != user.id
            or attempt.status != AttemptStatus.IN_PROGRESS
            or attempt.activated_at is None
        ):
            await websocket.close(code=4403)
            return

        is_reconnect = await manager.connect_student(attempt.id, websocket)
        connected = True
        if is_reconnect:
            reconnect_event = record_event(db, attempt, "reconnected", {"transport": "websocket"})
            if reconnect_event:
                await manager.broadcast_exam(attempt.exam_id, event_message(reconnect_event, attempt))
        await manager.broadcast_exam(
            attempt.exam_id, {"type": "attempt_update", "attempt": attempt_summary(attempt)}
        )
        await websocket.send_json({"type": "connected", "attempt_id": attempt.id})

        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            if message_type == "heartbeat":
                attempt.last_heartbeat_at = datetime.now(timezone.utc)
                position = message.get("current_position")
                if isinstance(position, int):
                    attempt.current_question_position = min(
                        max(position, 0), max(attempt.exam.total_questions - 1, 0)
                    )
                db.commit()
                await manager.broadcast_exam(
                    attempt.exam_id,
                    {"type": "attempt_update", "attempt": attempt_summary(attempt)},
                )
            elif message_type == "event":
                event = record_event(
                    db,
                    attempt,
                    str(message.get("event_type", "")),
                    message.get("details") if isinstance(message.get("details"), dict) else {},
                    message.get("occurred_at"),
                )
                if event:
                    await manager.broadcast_exam(
                        attempt.exam_id, event_message(event, attempt)
                    )
                    await manager.broadcast_exam(
                        attempt.exam_id,
                        {"type": "attempt_update", "attempt": attempt_summary(attempt)},
                    )
    except WebSocketDisconnect:
        pass
    finally:
        if connected and attempt:
            was_current_connection = await manager.disconnect_student(attempt.id, websocket)
            if was_current_connection:
                db.expire_all()
                current_attempt = db.scalar(
                    select(ExamAttempt)
                    .where(ExamAttempt.id == attempt.id)
                    .options(
                        selectinload(ExamAttempt.student),
                        selectinload(ExamAttempt.exam),
                        selectinload(ExamAttempt.responses),
                    )
                )
                if current_attempt and current_attempt.status == AttemptStatus.IN_PROGRESS:
                    event = record_event(
                        db, current_attempt, "disconnected", {"transport": "websocket"}
                    )
                    if event:
                        await manager.broadcast_exam(
                            current_attempt.exam_id,
                            event_message(event, current_attempt),
                        )
                if current_attempt:
                    await manager.broadcast_exam(
                        current_attempt.exam_id,
                        {
                            "type": "attempt_update",
                            "attempt": attempt_summary(current_attempt),
                        },
                    )
        db.close()


@router.websocket("/ws/examiner/exams/{exam_id}")
async def examiner_socket(websocket: WebSocket, exam_id: int):
    db = SessionLocal()
    connected = False
    try:
        if not same_origin(websocket):
            await websocket.close(code=4403)
            return
        user = websocket_user(websocket, db)
        assignment = None
        if user and user.role == UserRole.EXAMINER:
            assignment = db.scalar(
                select(ExaminerAssignment.id).where(
                    ExaminerAssignment.exam_id == exam_id,
                    ExaminerAssignment.examiner_id == user.id,
                )
            )
        if not assignment:
            await websocket.close(code=4403)
            return
        connected = await manager.connect_examiner(exam_id, user.id, websocket)
        if not connected:
            return
        await websocket.send_json({"type": "snapshot", **exam_snapshot(db, exam_id)})
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "refresh":
                await websocket.send_json(
                    {"type": "snapshot", **exam_snapshot(db, exam_id)}
                )
    except WebSocketDisconnect:
        pass
    finally:
        if connected:
            await manager.disconnect_examiner(exam_id, websocket)
        db.close()
