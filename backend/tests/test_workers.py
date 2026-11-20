"""The background jobs are plain functions, so tests call them directly (no waiting 30 seconds)."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Booking, BookingSeat, BookingStatus, OutboxEvent, Seat, SeatStatus
from app.workers.cleanup import run_cleanup_once
from app.workers.outbox import process_outbox_once

PAST = datetime.now(timezone.utc) - timedelta(minutes=1)
FUTURE = datetime.now(timezone.utc) + timedelta(hours=1)


async def run(job):
    async with SessionLocal() as db:
        return await job(db)


async def set_held(seat_id, user_id, until):
    async with SessionLocal() as db:
        seat = await db.get(Seat, seat_id)
        seat.status, seat.held_by_user_id, seat.held_until = SeatStatus.HELD, user_id, until
        await db.commit()


async def add_booking(user_id, event_id, seat_ids, expires_at, status=BookingStatus.PENDING):
    async with SessionLocal() as db:
        booking = Booking(user_id=user_id, event_id=event_id, status=status, total_amount=100, expires_at=expires_at)
        db.add(booking)
        await db.flush()
        db.add_all(BookingSeat(booking_id=booking.id, seat_id=s, price=100) for s in seat_ids)
        await db.commit()
        return booking.id


async def add_events(*events):
    async with SessionLocal() as db:
        db.add_all(OutboxEvent(event_type=t, payload=p) for t, p in events)
        await db.commit()


# ---------- cleanup job ----------
async def test_cleanup_frees_abandoned_holds_only(make_user, make_event, fetch):
    event, alice = await make_event(), await make_user()
    old, live = event.seat_ids[:2]
    await set_held(old, alice.id, PAST)
    await set_held(live, alice.id, FUTURE)

    result = await run(run_cleanup_once)
    assert result.holds_released == 1
    seat = await fetch(Seat, old)
    assert seat.status == SeatStatus.AVAILABLE and seat.held_by_user_id is None
    assert (await fetch(Seat, live)).status == SeatStatus.HELD


async def test_cleanup_expires_old_bookings_and_frees_their_seats(make_user, make_event, fetch):
    event, alice = await make_event(), await make_user()
    s1, s2, s3 = event.seat_ids[:3]
    for s in (s1, s2):
        await set_held(s, alice.id, PAST)
    await set_held(s3, alice.id, FUTURE)
    stale = await add_booking(alice.id, event.id, [s1, s2], PAST)
    live = await add_booking(alice.id, event.id, [s3], FUTURE)

    result = await run(run_cleanup_once)
    assert result.bookings_expired == 1
    assert (await fetch(Booking, stale)).status == BookingStatus.EXPIRED
    assert (await fetch(Seat, s1)).status == SeatStatus.AVAILABLE
    assert (await fetch(Booking, live)).status == BookingStatus.PENDING
    assert (await fetch(Seat, s3)).status == SeatStatus.HELD

    again = await run(run_cleanup_once)
    assert again.bookings_expired == again.holds_released == 0


async def test_cleanup_never_frees_a_seat_inside_a_live_booking(make_user, make_event, fetch):
    event, alice = await make_event(), await make_user()
    seat = event.seat_ids[0]
    await set_held(seat, alice.id, PAST)               # hold looks expired...
    await add_booking(alice.id, event.id, [seat], FUTURE)   # ...but its booking is still live
    await run(run_cleanup_once)
    assert (await fetch(Seat, seat)).status == SeatStatus.HELD


async def test_two_cleanups_at_once_do_each_booking_once(make_user, make_event):
    event, alice = await make_event(seats=20), await make_user()
    for s in event.seat_ids:
        await set_held(s, alice.id, PAST)
        await add_booking(alice.id, event.id, [s], PAST)

    results = await asyncio.gather(*[run(run_cleanup_once) for _ in range(3)])
    assert sum(r.bookings_expired for r in results) == 20


# ---------- outbox worker ----------
async def test_outbox_delivers_events_and_marks_them_done(make_user, make_event, caplog):
    caplog.set_level(logging.INFO, logger="seatlock")
    event, alice = await make_event(), await make_user()
    booking = await add_booking(alice.id, event.id, event.seat_ids[:2], FUTURE, BookingStatus.CONFIRMED)
    await add_events(
        ("booking.confirmed", {"booking_id": booking, "user_id": alice.id}),
        ("payment.refund_required", {"payment_id": 9, "booking_id": booking, "amount": "200.00"}),
    )

    assert await run(process_outbox_once) == 2
    assert f"EMAIL to {alice.email}: booking #{booking} confirmed, seats A1, A2" in caplog.text
    assert "REFUND 200.00 for payment #9" in caplog.text
    assert await run(process_outbox_once) == 0   # nothing left to do


async def test_outbox_retries_then_gives_up(caplog):
    caplog.set_level(logging.INFO, logger="seatlock")
    await add_events(("something.unknown", {}))
    for _ in range(settings.outbox_max_attempts + 2):
        await run(process_outbox_once)
    async with SessionLocal() as db:
        ev = (await db.execute(select(OutboxEvent))).scalar_one()
    assert ev.attempts == settings.outbox_max_attempts and ev.processed_at is None
    assert "gave up" in caplog.text


async def test_one_broken_event_does_not_spoil_the_batch(make_user, make_event):
    event, alice = await make_event(), await make_user()
    booking = await add_booking(alice.id, event.id, event.seat_ids[:1], FUTURE, BookingStatus.CONFIRMED)
    await add_events(
        ("booking.confirmed", {"booking_id": "not-a-number", "user_id": alice.id}),   # makes the DB query fail
        ("booking.confirmed", {"booking_id": booking, "user_id": alice.id}),
    )
    assert await run(process_outbox_once) == 1   # the good one still got delivered


async def test_three_workers_deliver_each_event_exactly_once(make_user, make_event, caplog):
    caplog.set_level(logging.INFO, logger="seatlock")
    event, alice = await make_event(), await make_user()
    booking = await add_booking(alice.id, event.id, event.seat_ids[:1], FUTURE, BookingStatus.CONFIRMED)
    await add_events(*[("booking.confirmed", {"booking_id": booking, "user_id": alice.id})] * 30)

    while sum(await asyncio.gather(*[run(process_outbox_once) for _ in range(3)])):
        pass
    assert caplog.text.count("EMAIL to") == 30
    async with SessionLocal() as db:
        left = (await db.execute(select(func.count()).select_from(OutboxEvent)
                                 .where(OutboxEvent.processed_at.is_(None)))).scalar()
    assert left == 0
