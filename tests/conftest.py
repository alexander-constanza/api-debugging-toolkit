import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest

from app.db import Base, engine
from app.main import create_app


@pytest.fixture(autouse=True)
def reset_db_down_toggle():
    """/simulate/db-down sets module-level state; keep it out of other tests."""
    import app.main

    app.main._force_db_down = False
    yield
    app.main._force_db_down = False


@pytest.fixture()
def app():
    flask_app = create_app()
    yield flask_app
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(app):
    return app.test_client()
