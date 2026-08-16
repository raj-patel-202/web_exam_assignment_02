from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, initialize_database


def test_initialize_database_creates_all_tables_without_migrations():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    initialize_database(session_factory)

    table_names = set(inspect(engine).get_table_names())
    assert table_names == set(Base.metadata.tables)

    Base.metadata.drop_all(engine)
