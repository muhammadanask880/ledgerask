"""
SQLAlchemy setup for the *app* database: user accounts and chat history.

This is a completely separate SQLite file from the business database
(business_db.py). The AI's tools never open this file, and this module
never opens the business database - that separation is deliberate, so a bug
in the chat/auth code can never accidentally touch business data.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

settings.app_db_full_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.app_db_full_path}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app import models  # noqa: F401 - import registers the models with Base

    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
