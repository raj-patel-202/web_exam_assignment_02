"""Support decimal marking schemes and fullscreen-gated activation.

Revision ID: 0002_decimal_marks
Revises: 0001_initial_schema
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_decimal_marks"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> dict[str, dict]:
    return {
        column["name"]: column
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _checks(table_name: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_check_constraints(table_name)
        if constraint.get("name")
    }


def _is_integer(column: dict) -> bool:
    return isinstance(column["type"], sa.Integer) and not isinstance(
        column["type"], sa.Numeric
    )


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    attempt_columns = _columns("exam_attempts")
    if "activated_at" not in attempt_columns:
        op.add_column(
            "exam_attempts",
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(
            sa.text(
                "UPDATE exam_attempts SET activated_at = started_at "
                "WHERE activated_at IS NULL"
            )
        )

    exam_columns = _columns("exams")
    response_columns = _columns("responses")
    attempt_columns = _columns("exam_attempts")
    if dialect == "postgresql":
        if _is_integer(exam_columns["positive_marks"]):
            op.alter_column(
                "exams",
                "positive_marks",
                type_=sa.Numeric(8, 2),
                existing_type=sa.Integer(),
                postgresql_using="positive_marks::numeric(8,2)",
            )
        if _is_integer(exam_columns["negative_marks"]):
            op.alter_column(
                "exams",
                "negative_marks",
                type_=sa.Numeric(8, 2),
                existing_type=sa.Integer(),
                postgresql_using="negative_marks::numeric(8,2)",
            )
        if _is_integer(attempt_columns["score"]):
            op.alter_column(
                "exam_attempts",
                "score",
                type_=sa.Numeric(10, 2),
                existing_type=sa.Integer(),
                postgresql_using="score::numeric(10,2)",
            )
        if _is_integer(attempt_columns["max_score"]):
            op.alter_column(
                "exam_attempts",
                "max_score",
                type_=sa.Numeric(10, 2),
                existing_type=sa.Integer(),
                postgresql_using="max_score::numeric(10,2)",
            )
        if _is_integer(response_columns["marks_awarded"]):
            op.alter_column(
                "responses",
                "marks_awarded",
                type_=sa.Numeric(8, 2),
                existing_type=sa.Integer(),
                postgresql_using="marks_awarded::numeric(8,2)",
            )
        if not attempt_columns["expires_at"]["nullable"]:
            op.alter_column(
                "exam_attempts",
                "expires_at",
                existing_type=sa.DateTime(timezone=True),
                nullable=True,
            )
    else:
        needs_exam_rebuild = _is_integer(exam_columns["positive_marks"])
        if needs_exam_rebuild:
            with op.batch_alter_table("exams") as batch:
                batch.alter_column(
                    "positive_marks", existing_type=sa.Integer(), type_=sa.Numeric(8, 2)
                )
                batch.alter_column(
                    "negative_marks", existing_type=sa.Integer(), type_=sa.Numeric(8, 2)
                )
        needs_attempt_rebuild = (
            _is_integer(attempt_columns["score"])
            or not attempt_columns["expires_at"]["nullable"]
        )
        if needs_attempt_rebuild:
            with op.batch_alter_table("exam_attempts") as batch:
                if _is_integer(attempt_columns["score"]):
                    batch.alter_column(
                        "score", existing_type=sa.Integer(), type_=sa.Numeric(10, 2)
                    )
                    batch.alter_column(
                        "max_score", existing_type=sa.Integer(), type_=sa.Numeric(10, 2)
                    )
                if not attempt_columns["expires_at"]["nullable"]:
                    batch.alter_column(
                        "expires_at",
                        existing_type=sa.DateTime(timezone=True),
                        nullable=True,
                    )
        if _is_integer(response_columns["marks_awarded"]):
            with op.batch_alter_table("responses") as batch:
                batch.alter_column(
                    "marks_awarded", existing_type=sa.Integer(), type_=sa.Numeric(8, 2)
                )

    checks = _checks("exams")
    if "ck_exam_positive_marks" not in checks or "ck_exam_negative_marks" not in checks:
        if dialect == "sqlite":
            with op.batch_alter_table("exams") as batch:
                if "ck_exam_positive_marks" not in checks:
                    batch.create_check_constraint(
                        "ck_exam_positive_marks", "positive_marks > 0"
                    )
                if "ck_exam_negative_marks" not in checks:
                    batch.create_check_constraint(
                        "ck_exam_negative_marks", "negative_marks <= 0"
                    )
        else:
            if "ck_exam_positive_marks" not in checks:
                op.create_check_constraint(
                    "ck_exam_positive_marks", "exams", "positive_marks > 0"
                )
            if "ck_exam_negative_marks" not in checks:
                op.create_check_constraint(
                    "ck_exam_negative_marks", "exams", "negative_marks <= 0"
                )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    checks = _checks("exams")
    if dialect == "sqlite":
        with op.batch_alter_table("exams") as batch:
            if "ck_exam_positive_marks" in checks:
                batch.drop_constraint("ck_exam_positive_marks", type_="check")
            if "ck_exam_negative_marks" in checks:
                batch.drop_constraint("ck_exam_negative_marks", type_="check")
    else:
        if "ck_exam_positive_marks" in checks:
            op.drop_constraint("ck_exam_positive_marks", "exams", type_="check")
        if "ck_exam_negative_marks" in checks:
            op.drop_constraint("ck_exam_negative_marks", "exams", type_="check")

    if dialect == "postgresql":
        op.alter_column(
            "responses",
            "marks_awarded",
            type_=sa.Integer(),
            existing_type=sa.Numeric(8, 2),
            postgresql_using="ROUND(marks_awarded)::integer",
        )
        op.alter_column(
            "exam_attempts",
            "score",
            type_=sa.Integer(),
            existing_type=sa.Numeric(10, 2),
            postgresql_using="ROUND(score)::integer",
        )
        op.alter_column(
            "exam_attempts",
            "max_score",
            type_=sa.Integer(),
            existing_type=sa.Numeric(10, 2),
            postgresql_using="ROUND(max_score)::integer",
        )
        op.alter_column(
            "exams",
            "positive_marks",
            type_=sa.Integer(),
            existing_type=sa.Numeric(8, 2),
            postgresql_using="ROUND(positive_marks)::integer",
        )
        op.alter_column(
            "exams",
            "negative_marks",
            type_=sa.Integer(),
            existing_type=sa.Numeric(8, 2),
            postgresql_using="ROUND(negative_marks)::integer",
        )
    else:
        with op.batch_alter_table("responses") as batch:
            batch.alter_column(
                "marks_awarded", existing_type=sa.Numeric(8, 2), type_=sa.Integer()
            )
        with op.batch_alter_table("exam_attempts") as batch:
            batch.alter_column(
                "score", existing_type=sa.Numeric(10, 2), type_=sa.Integer()
            )
            batch.alter_column(
                "max_score", existing_type=sa.Numeric(10, 2), type_=sa.Integer()
            )
        with op.batch_alter_table("exams") as batch:
            batch.alter_column(
                "positive_marks", existing_type=sa.Numeric(8, 2), type_=sa.Integer()
            )
            batch.alter_column(
                "negative_marks", existing_type=sa.Numeric(8, 2), type_=sa.Integer()
            )

    if "activated_at" in _columns("exam_attempts"):
        op.drop_column("exam_attempts", "activated_at")
