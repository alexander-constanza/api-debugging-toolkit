"""Database access layer.

Uses SQLAlchemy so the same code runs against SQLite (local dev / tests)
or Postgres (docker-compose), controlled entirely by DATABASE_URL.
"""
import os

from sqlalchemy import Column, DateTime, Integer, String, create_engine, func
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///local.db")

_is_sqlite = DATABASE_URL.startswith("sqlite")
_is_memory = DATABASE_URL == "sqlite:///:memory:"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    poolclass=StaticPool if _is_memory else None,
)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))
Base = declarative_base()


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    customer_name = Column(String, nullable=False)
    item = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def check_db_connection() -> bool:
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False
