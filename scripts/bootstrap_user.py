import argparse

from sqlalchemy import select

from app.database import SessionLocal
from app.models import User, UserRole
from app.security import hash_password, normalize_username, validate_password_strength


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a user account in PostgreSQL.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--full-name", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--role", choices=[role.value for role in UserRole], required=True)
    args = parser.parse_args()

    error = validate_password_strength(args.password)
    if error:
        raise SystemExit(error)
    username = normalize_username(args.username)
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.username == username)):
            raise SystemExit(f"User '{username}' already exists.")
        user = User(
            username=username,
            full_name=args.full_name.strip(),
            password_hash=hash_password(args.password),
            role=UserRole(args.role),
        )
        db.add(user)
        db.commit()
        print(f"Created {user.role.value} account: {user.username}")


if __name__ == "__main__":
    main()

