import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest

from app.db import Base, engine
from app.main import app as flask_app


@pytest.fixture()
def app():
    Base.metadata.create_all(bind=engine)
    yield flask_app
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(app):
    return app.test_client()
