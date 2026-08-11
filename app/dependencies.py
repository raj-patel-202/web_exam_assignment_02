from collections.abc import Iterable

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.models import User, UserRole


def current_user_from_request(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, int(user_id))
    if not user or not user.is_active:
        request.session.clear()
        return None
    return user


def require_role(user: User | None, roles: UserRole | Iterable[UserRole]) -> User:
    allowed = {roles} if isinstance(roles, UserRole) else set(roles)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if user.role not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def role_home(role: UserRole) -> str:
    return {
        UserRole.STUDENT: "/student",
        UserRole.INSTRUCTOR: "/instructor",
        UserRole.EXAMINER: "/examiner",
    }[role]

