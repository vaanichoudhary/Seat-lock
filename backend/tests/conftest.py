"""Shared test setup. pytest loads this file automatically before any test.

Tests use their own database, `seatlock_test`, so your real data is never touched:
- it is dropped and rebuilt from the migrations once per `pytest` run
- every table is emptied before each test, so tests can't affect each other
"""
import asyncio
import itertools
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from dotenv import dotenv_values

# ---- 1. point the app at the TEST database, before any `app` code is imported ----
# (app/core/db.py creates its engine the moment it is imported, using DATABASE_URL)
BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DB_NAME = "seatlock_test"

_main_url = os.environ.get("DATABASE_URL") or dotenv_values(BACKEND_DIR.parent / ".env")["DATABASE_URL"]
_server_url, _, _main_db_name = _main_url.rpartition("/")
if _main_db_name == TEST_DB_NAME:
    raise RuntimeError("DATABASE_URL already points at the test database")
os.environ["DATABASE_URL"] = f"{_server_url}/{TEST_DB_NAME}"
os.environ["RUN_WORKERS"] = "false"   # tests run the background jobs by hand
os.environ["MOCK_GATEWAY_ENABLED"] = "false"   # off like in production; tests that need it turn it on

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Event, Seat, UserRole, User  # noqa: E402

TEST_PASSWORD = "correct-horse-battery"
_TEST_HASH = hash_password(TEST_PASSWORD)   # hashing is slow on purpose, so do it once


# ---- 2. build the test database once per run ----
@pytest.fixture(scope="session", autouse=True)
def test_database():
    async def recreate():
        # connect to the main database just to drop/create the test one
        conn = await asyncpg.connect(_main_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
        finally:
            await conn.close()

    asyncio.run(recreate())

    # run every Alembic migration, so the test schema is exactly what production gets
    # (no ini file on purpose: alembic.ini's logging setup would silence our loggers)
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")
    yield


# ---- 3. empty every table before each test ----
@pytest.fixture(autouse=True)
async def clean_tables(test_database):
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE users, events, seats, bookings, booking_seats, payments, "
            "idempotency_keys, outbox RESTART IDENTITY CASCADE"
        ))
    yield


@pytest.fixture(scope="session", autouse=True)
async def close_engine():
    yield
    await engine.dispose()


# ---- 4. helpers that tests ask for by name ----
@pytest.fixture
async def client():
    """An HTTP client that talks to the app directly, no running server needed."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def make_user():
    """make_user() -> a logged-in user: .id, .email, .headers (the Authorization header)."""
    counter = itertools.count(1)

    async def _make(role: UserRole = UserRole.USER):
        n = next(counter)
        async with SessionLocal() as db:
            user = User(username=f"user{n}", email=f"user{n}@test.com", password_hash=_TEST_HASH, role=role)
            db.add(user)
            await db.commit()
        return SimpleNamespace(
            id=user.id, email=user.email,
            headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
        )

    return _make


@pytest.fixture
def make_event(make_user):
    """make_event(seats=5) -> an event whose sale is open: .id, .organizer, .seat_ids (A1, A2, ...)."""
    async def _make(seats: int = 5, sale_open: bool = True, price: int = 250):
        organizer = await make_user(UserRole.ORGANIZER)
        now = datetime.now(timezone.utc)
        async with SessionLocal() as db:
            event = Event(
                organizer_id=organizer.id, name="Test Show", ticket_price=price,
                starts_at=now + timedelta(days=7),
                sale_opens_at=now - timedelta(days=1) if sale_open else now + timedelta(days=1),
            )
            db.add(event)
            await db.flush()
            rows = [Seat(event_id=event.id, seat_number=f"A{n}") for n in range(1, seats + 1)]
            db.add_all(rows)
            await db.commit()
        return SimpleNamespace(id=event.id, organizer=organizer, seat_ids=[s.id for s in rows])

    return _make


@pytest.fixture
def fetch():
    """await fetch(Seat, 3) -> that row, read fresh from the database."""
    async def _fetch(model, id_):
        async with SessionLocal() as db:
            return await db.get(model, id_)

    return _fetch
