import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from app.core.security import hash_password

from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.models.model import Event, Seat, SeatStatus, User, UserRole

ROWS = "ABCDEFGHIJ"      # 10 rows
SEATS_PER_ROW = 50       # 10 x 50 = 500 seats

TABLES = "outbox, idempotency_keys, payments, booking_seats, bookings, seats, events, users"


async def main() -> None:
    # Safety: TRUNCATE is destructive, so refuse to run against a non-local database.
    if engine.url.host not in ("localhost", "127.0.0.1"):
        raise SystemExit(f"Refusing to seed non-local database: {engine.url.host}")

    async with SessionLocal() as session:
        # 1. wipe everything and reset ids
        await session.execute(text(f"TRUNCATE TABLE {TABLES} RESTART IDENTITY CASCADE"))

        # 2. organizer
        organizer = User(
            username="organizer1",
            email="organizer@example.com",
            password_hash=hash_password("organizer123"),
            role=UserRole.ORGANIZER,
        )
        session.add(organizer)
        await session.flush()  # sends the INSERT so organizer.id is filled in

        # 3. event
        now = datetime.now(timezone.utc)
        event = Event(
            organizer_id=organizer.id,
            name="Flash Sale Concert",
            description="500 seats, one second, good luck.",
            starts_at=now + timedelta(days=30),
            sale_opens_at=now,
            ticket_price=Decimal("500.00"),
        )
        session.add(event)
        await session.flush()

        # 4. seats: A1..A50, B1..B50, ... J1..J50
        session.add_all(
            Seat(event_id=event.id, seat_number=f"{row}{n}", status=SeatStatus.AVAILABLE)
            for row in ROWS
            for n in range(1, SEATS_PER_ROW + 1)
        )

        await session.commit()
        print(f"Seeded organizer id={organizer.id}, event id={event.id}, "
              f"{len(ROWS) * SEATS_PER_ROW} seats")

    await engine.dispose()  # close the pool so the script exits cleanly


if __name__ == "__main__":
    asyncio.run(main())