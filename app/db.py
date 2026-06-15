"""Database engine and session management."""

import os

from dotenv import load_dotenv
from sqlmodel import Session, SQLModel, create_engine

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///setu.db")

# SQLite needs check_same_thread=False for FastAPI's threadpool; Postgres doesn't.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
# pool_pre_ping revives connections that cloud Postgres (Supabase/Render) dropped while idle.
engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)


def init_db() -> None:
    # Import models so their tables register on SQLModel.metadata before create_all.
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
