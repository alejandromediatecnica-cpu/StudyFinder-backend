import os
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# La suite corre sin MongoDB real: mongomock en memoria y sin DNS SRV de Atlas.
os.environ.setdefault("MONGODB_URL", "mongodb://localhost:27017")
os.environ.setdefault("MONGODB_DATABASE", "studyfinder_test")

import mongomock
import pymongo

pymongo.MongoClient = mongomock.MongoClient

import pytest
from fastapi.testclient import TestClient

import app.database as database
import app.rate_limit as rate_limit
from app.main import app  # noqa: E402
from app.api import documents as documents_api  # noqa: E402
from app.api import users as users_api  # noqa: E402

_COLLECTIONS = (
    "users",
    "auth_sessions",
    "documents",
    "materia_documents",
    "contacts",
    "messages",
    "tasks",
    "calendar_events",
    "password_verifications",
)

PASSWORD = "ClaveSegura2026x"


@pytest.fixture(autouse=True)
def _reset_limits():
    rate_limit.reset_rate_limits()
    yield
    rate_limit.reset_rate_limits()


@pytest.fixture
def db(tmp_path, monkeypatch):
    # Aislamiento: la base mongomock es compartida entre tests, se vacía en cada uno.
    for collection_name in _COLLECTIONS:
        database.db.drop_collection(collection_name)
    uploads = tmp_path / "uploads"
    monkeypatch.setattr(database, "UPLOADS_PATH", uploads)
    # documents.py y users.py guardan UPLOADS_PATH como valor propio al importarlo.
    monkeypatch.setattr(documents_api, "UPLOADS_PATH", uploads)
    monkeypatch.setattr(users_api.database, "UPLOADS_PATH", uploads)
    database.initialize_database()
    return database


@pytest.fixture
def client(db):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def user(client):
    response = client.post(
        "/auth/signup",
        json={"name": "Ana", "email": "ana@correo.com", "password": PASSWORD},
    )
    assert response.status_code == 201
    data = response.json()
    return {
        "token": data["token"],
        "headers": {"Authorization": f"Bearer {data['token']}"},
        "user": data["user"],
        "email": "ana@correo.com",
        "password": PASSWORD,
    }
