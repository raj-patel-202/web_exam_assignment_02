from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models import ExamAttempt, ProctorEvent

EVENT_SEVERITY = {
    "tab_hidden": 2,
    "tab_visible": 0,
    "window_blur": 2,
    "window_focus": 0,
    "fullscreen_exit": 3,
    "copy_attempt": 2,
    "paste_attempt": 3,
    "context_menu": 1,
    "page_reload": 1,
    "disconnected": 2,
    "reconnected": 0,
}


class ConnectionManager:
    def __init__(self) -> None:
        self.student_connections: dict[int, WebSocket] = {}
        self.seen_student_connections: set[int] = set()
        self.examiner_connections: dict[int, dict[WebSocket, int]] = defaultdict(dict)
        self.revoked_examiner_assignments: set[tuple[int, int]] = set()
        self.revoked_examiner_users: set[int] = set()
        self._lock = threading.Lock()

    async def connect_student(self, attempt_id: int, websocket: WebSocket) -> bool:
        await websocket.accept()
        with self._lock:
            is_reconnect = attempt_id in self.seen_student_connections
            previous = self.student_connections.get(attempt_id)
            self.student_connections[attempt_id] = websocket
            self.seen_student_connections.add(attempt_id)
        if previous and previous is not websocket:
            try:
                await previous.close(code=4001)
            except RuntimeError:
                pass
        return is_reconnect

    def disconnect_student(self, attempt_id: int, websocket: WebSocket) -> bool:
        with self._lock:
            if self.student_connections.get(attempt_id) is websocket:
                self.student_connections.pop(attempt_id, None)
                return True
        return False

    async def connect_examiner(
        self, exam_id: int, examiner_id: int, websocket: WebSocket
    ) -> bool:
        await websocket.accept()
        with self._lock:
            if (
                examiner_id in self.revoked_examiner_users
                or (exam_id, examiner_id) in self.revoked_examiner_assignments
            ):
                allowed = False
            else:
                self.examiner_connections[exam_id][websocket] = examiner_id
                allowed = True
        if not allowed:
            await websocket.close(code=4403)
        return allowed

    def disconnect_examiner(self, exam_id: int, websocket: WebSocket) -> None:
        with self._lock:
            self.examiner_connections[exam_id].pop(websocket, None)

    def allow_examiner(self, exam_id: int, examiner_id: int) -> None:
        with self._lock:
            self.revoked_examiner_users.discard(examiner_id)
            self.revoked_examiner_assignments.discard((exam_id, examiner_id))

    async def revoke_examiner(
        self, examiner_id: int, exam_id: int | None = None
    ) -> None:
        sockets: list[WebSocket] = []
        with self._lock:
            if exam_id is None:
                self.revoked_examiner_users.add(examiner_id)
                for connections in self.examiner_connections.values():
                    sockets.extend(
                        websocket
                        for websocket, connected_user_id in connections.items()
                        if connected_user_id == examiner_id
                    )
            else:
                self.revoked_examiner_assignments.add((exam_id, examiner_id))
                sockets.extend(
                    websocket
                    for websocket, connected_user_id in self.examiner_connections.get(
                        exam_id, {}
                    ).items()
                    if connected_user_id == examiner_id
                )
            for websocket in sockets:
                for connections in self.examiner_connections.values():
                    connections.pop(websocket, None)
        for websocket in sockets:
            try:
                await websocket.close(code=4403, reason="Invigilator access revoked")
            except RuntimeError:
                pass

    async def remove_exam(self, exam_id: int, attempt_ids: list[int]) -> None:
        """Close and forget all live connections belonging to a deleted exam."""
        sockets: list[WebSocket] = []
        with self._lock:
            sockets.extend(self.examiner_connections.pop(exam_id, {}).keys())
            for attempt_id in attempt_ids:
                student_socket = self.student_connections.pop(attempt_id, None)
                self.seen_student_connections.discard(attempt_id)
                if student_socket is not None:
                    sockets.append(student_socket)
            self.revoked_examiner_assignments = {
                assignment
                for assignment in self.revoked_examiner_assignments
                if assignment[0] != exam_id
            }
        for websocket in sockets:
            try:
                await websocket.close(code=4404, reason="Exam deleted")
            except RuntimeError:
                pass

    def is_online(self, attempt_id: int) -> bool:
        return attempt_id in self.student_connections

    async def broadcast_exam(self, exam_id: int, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for websocket in list(self.examiner_connections.get(exam_id, {})):
            try:
                await websocket.send_json(payload)
            except RuntimeError:
                stale.append(websocket)
        for websocket in stale:
            self.disconnect_examiner(exam_id, websocket)


manager = ConnectionManager()
settings = get_settings()


def parse_client_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def record_event(
    db: Session,
    attempt: ExamAttempt,
    event_type: str,
    details: dict[str, Any] | None = None,
    client_occurred_at: object = None,
) -> ProctorEvent | None:
    if event_type not in EVENT_SEVERITY:
        return None
    severity = EVENT_SEVERITY[event_type]
    event = ProctorEvent(
        attempt_id=attempt.id,
        event_type=event_type,
        severity=severity,
        client_occurred_at=parse_client_time(client_occurred_at),
        details=dict(details or {}),
    )
    if severity > 0:
        attempt.suspicious_event_count += 1
    attempt.last_heartbeat_at = datetime.now(timezone.utc)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def attempt_summary(attempt: ExamAttempt) -> dict[str, Any]:
    answered = sum(response.selected_option_id is not None for response in attempt.responses)
    heartbeat = attempt.last_heartbeat_at
    if heartbeat and heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    heartbeat_fresh = bool(
        heartbeat
        and heartbeat
        >= datetime.now(timezone.utc)
        - timedelta(seconds=max(20, settings.proctor_heartbeat_seconds * 3))
    )
    return {
        "attempt_id": attempt.id,
        "student_name": attempt.student.full_name,
        "username": attempt.student.username,
        "status": attempt.status.value,
        "started_at": (attempt.activated_at or attempt.started_at).isoformat(),
        "activated": attempt.activated_at is not None,
        "expires_at": attempt.expires_at.isoformat() if attempt.expires_at else None,
        "last_heartbeat_at": (
            attempt.last_heartbeat_at.isoformat() if attempt.last_heartbeat_at else None
        ),
        "current_question": attempt.current_question_position + 1,
        "total_questions": attempt.exam.total_questions,
        "answered": answered,
        "flags": attempt.suspicious_event_count,
        "online": manager.is_online(attempt.id) and heartbeat_fresh,
    }


def exam_snapshot(db: Session, exam_id: int) -> dict[str, Any]:
    attempts = list(
        db.scalars(
            select(ExamAttempt)
            .where(ExamAttempt.exam_id == exam_id)
            .order_by(ExamAttempt.started_at.desc())
            .options(
                selectinload(ExamAttempt.student),
                selectinload(ExamAttempt.exam),
                selectinload(ExamAttempt.responses),
            )
        )
    )
    recent_events = list(
        db.scalars(
            select(ProctorEvent)
            .join(ExamAttempt)
            .where(ExamAttempt.exam_id == exam_id)
            .order_by(ProctorEvent.occurred_at.desc())
            .limit(50)
        )
    )
    attempt_lookup = {attempt.id: attempt for attempt in attempts}
    return {
        "attempts": [attempt_summary(attempt) for attempt in attempts],
        "events": [
            {
                "id": event.id,
                "attempt_id": event.attempt_id,
                "student_name": attempt_lookup[event.attempt_id].student.full_name,
                "event_type": event.event_type,
                "severity": event.severity,
                "occurred_at": event.occurred_at.isoformat(),
                "details": event.details,
            }
            for event in recent_events
            if event.attempt_id in attempt_lookup
        ],
    }
