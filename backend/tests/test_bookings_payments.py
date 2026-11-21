import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Booking, BookingSeat, OutboxEvent, Payment, Seat, SeatStatus


# ---------- small helpers so each test reads like a story ----------
async def hold(client, user, event_id, seat_ids):
    r = await client.post(f"/events/{event_id}/hold", json={"seat_ids": seat_ids}, headers=user.headers)
    assert r.status_code == 200, r.text


async def book(client, user, event_id):
    return await client.post("/bookings", json={"event_id": event_id}, headers=user.headers)


async def pay(client, user, booking_id, key):
    return await client.post(f"/bookings/{booking_id}/pay", headers={**user.headers, "Idempotency-Key": key})


async def webhook(client, gateway_ref, result, secret=None):
    return await client.post(
        "/payments/webhook",
        json={"gateway_ref": gateway_ref, "status": result},
        headers={"X-Webhook-Secret": secret or settings.payment_webhook_secret},
    )


async def count(stmt):
    async with SessionLocal() as db:
        return (await db.execute(stmt)).scalar()


@pytest.fixture
async def booked(client, make_user, make_event):
    """Alice holds A1, A2 and has a pending booking for them."""
    event, alice = await make_event(price=250), await make_user()
    await hold(client, alice, event.id, event.seat_ids[:2])
    r = await book(client, alice, event.id)
    assert r.status_code == 201, r.text
    return event, alice, r.json()


# ---------- bookings ----------
async def test_booking_takes_all_my_held_seats(booked):
    _, _, booking = booked
    assert booking["status"] == "pending"
    assert booking["total_amount"] == "500.00"
    assert [s["seat_number"] for s in booking["seats"]] == ["A1", "A2"]


async def test_cannot_book_the_same_seats_twice(client, booked):
    event, alice, _ = booked
    assert (await book(client, alice, event.id)).status_code == 409


async def test_cannot_book_without_holding_seats(client, make_user, make_event):
    event, alice = await make_event(), await make_user()
    assert (await book(client, alice, event.id)).status_code == 409


async def test_release_hold_skips_seats_in_a_booking(client, booked):
    event, alice, _ = booked
    r = await client.delete(f"/events/{event.id}/hold", headers=alice.headers)
    assert r.json() == {"released": 0}


async def test_other_users_cannot_see_my_booking(client, make_user, booked):
    _, alice, booking = booked
    bob = await make_user()
    assert (await client.get(f"/bookings/{booking['id']}", headers=alice.headers)).status_code == 200
    assert (await client.get(f"/bookings/{booking['id']}", headers=bob.headers)).status_code == 404


async def test_my_bookings_newest_first(client, make_user, make_event):
    alice = await make_user()
    ids = []
    for _ in range(2):
        event = await make_event()
        await hold(client, alice, event.id, event.seat_ids[:1])
        ids.append((await book(client, alice, event.id)).json()["id"])
    mine = (await client.get("/bookings/me", headers=alice.headers)).json()
    assert [b["id"] for b in mine] == ids[::-1]


async def test_cancel_frees_seats_and_only_once(client, booked, fetch):
    event, alice, booking = booked
    r = await client.post(f"/bookings/{booking['id']}/cancel", headers=alice.headers)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (await fetch(Seat, event.seat_ids[0])).status == SeatStatus.AVAILABLE
    assert (await client.post(f"/bookings/{booking['id']}/cancel", headers=alice.headers)).status_code == 409
    assert (await pay(client, alice, booking["id"], "pay-after-cancel")).status_code == 409


# ---------- payments ----------
async def test_happy_path_pay_and_confirm(client, booked, fetch):
    event, alice, booking = booked
    r = await pay(client, alice, booking["id"], "alice-pay-0001")
    assert r.status_code == 201
    payment = r.json()
    assert payment["status"] == "pending" and payment["amount"] == "500.00"

    w = await webhook(client, payment["gateway_ref"], "succeeded")
    assert w.json() == {"payment_status": "succeeded", "booking_status": "confirmed"}

    seat = await fetch(Seat, event.seat_ids[0])
    assert seat.status == SeatStatus.BOOKED and seat.held_by_user_id is None
    assert await count(select(func.count()).select_from(OutboxEvent)
                       .where(OutboxEvent.event_type == "booking.confirmed")) == 1


async def test_same_idempotency_key_never_charges_twice(client, booked):
    _, alice, booking = booked
    first = await pay(client, alice, booking["id"], "alice-pay-0001")
    again = await pay(client, alice, booking["id"], "alice-pay-0001")
    assert again.status_code == first.status_code == 201
    assert again.json() == first.json()   # the saved answer is replayed

    # a different key while a payment is already in progress is blocked too
    assert (await pay(client, alice, booking["id"], "alice-pay-0002")).status_code == 409
    assert await count(select(func.count()).select_from(Payment)) == 1


async def test_five_simultaneous_clicks_make_one_payment(client, booked):
    _, alice, booking = booked
    responses = await asyncio.gather(*[pay(client, alice, booking["id"], "double-click") for _ in range(5)])
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert await count(select(func.count()).select_from(Payment)) == 1


async def test_idempotency_key_reused_for_another_booking_is_rejected(client, make_event, booked):
    _, alice, booking = booked
    other = await make_event()
    await hold(client, alice, other.id, other.seat_ids[:1])
    other_booking = (await book(client, alice, other.id)).json()

    await pay(client, alice, booking["id"], "shared-key-01")
    assert (await pay(client, alice, other_booking["id"], "shared-key-01")).status_code == 422


async def test_pay_needs_an_idempotency_key(client, booked):
    _, alice, booking = booked
    assert (await client.post(f"/bookings/{booking['id']}/pay", headers=alice.headers)).status_code == 422


async def test_webhook_needs_the_secret(client, booked):
    _, alice, booking = booked
    payment = (await pay(client, alice, booking["id"], "alice-pay-0001")).json()
    assert (await webhook(client, payment["gateway_ref"], "succeeded", secret="wrong")).status_code == 401
    assert (await webhook(client, "mock_does_not_exist", "succeeded")).status_code == 404


async def test_webhook_retry_changes_nothing(client, booked):
    _, alice, booking = booked
    payment = (await pay(client, alice, booking["id"], "alice-pay-0001")).json()
    await webhook(client, payment["gateway_ref"], "succeeded")
    retry = await webhook(client, payment["gateway_ref"], "failed")   # even with a different status
    assert retry.json() == {"payment_status": "succeeded", "booking_status": "confirmed"}
    assert await count(select(func.count()).select_from(OutboxEvent)) == 1


async def test_failed_payment_frees_the_seats(client, booked, fetch):
    event, alice, booking = booked
    payment = (await pay(client, alice, booking["id"], "alice-pay-0001")).json()
    w = await webhook(client, payment["gateway_ref"], "failed")
    assert w.json() == {"payment_status": "failed", "booking_status": "failed"}
    assert (await fetch(Seat, event.seat_ids[0])).status == SeatStatus.AVAILABLE
    assert await count(select(func.count()).select_from(BookingSeat).where(BookingSeat.active)) == 0


# ---------- running out of time ----------
async def expire(booking_id, seat_ids):
    """Pretend 5 minutes passed for this booking and its seats."""
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    async with SessionLocal() as db:
        await db.execute(update(Booking).where(Booking.id == booking_id).values(expires_at=past))
        await db.execute(update(Seat).where(Seat.id.in_(seat_ids)).values(held_until=past))
        await db.commit()


async def test_cannot_pay_an_expired_booking(client, booked):
    event, alice, booking = booked
    await expire(booking["id"], event.seat_ids[:2])
    assert (await pay(client, alice, booking["id"], "too-late-001")).status_code == 409


async def test_expired_booking_does_not_block_the_next_buyer(client, make_user, booked, fetch):
    event, alice, booking = booked
    dave = await make_user()
    await expire(booking["id"], event.seat_ids[:2])

    await hold(client, dave, event.id, event.seat_ids[:1])
    assert (await book(client, dave, event.id)).status_code == 201
    assert (await fetch(Booking, booking["id"])).status.value == "expired"


async def test_late_payment_on_expired_booking_asks_for_refund(client, make_user, booked, fetch):
    event, alice, booking = booked
    payment = (await pay(client, alice, booking["id"], "alice-pay-0001")).json()
    await expire(booking["id"], event.seat_ids[:2])
    dave = await make_user()
    await hold(client, dave, event.id, event.seat_ids[:1])   # Dave takes A1 meanwhile

    w = await webhook(client, payment["gateway_ref"], "succeeded")
    assert w.json() == {"payment_status": "succeeded", "booking_status": "expired"}
    assert await count(select(func.count()).select_from(OutboxEvent)
                       .where(OutboxEvent.event_type == "payment.refund_required")) == 1
    assert (await fetch(Seat, event.seat_ids[0])).held_by_user_id == dave.id   # Dave keeps his seat


# ---------- dev-only mock gateway (what the website uses instead of the webhook) ----------
async def mock_complete(client, user, gateway_ref, result):
    return await client.post(f"/payments/mock/{gateway_ref}/complete", json={"status": result}, headers=user.headers)


async def test_mock_gateway_is_off_unless_enabled(client, booked):
    _, alice, booking = booked
    payment = (await pay(client, alice, booking["id"], "alice-pay-0001")).json()
    assert (await mock_complete(client, alice, payment["gateway_ref"], "succeeded")).status_code == 404


async def test_mock_gateway_confirms_only_for_the_buyer(client, make_user, booked, monkeypatch):
    monkeypatch.setattr(settings, "mock_gateway_enabled", True)
    _, alice, booking = booked
    bob = await make_user()
    payment = (await pay(client, alice, booking["id"], "alice-pay-0001")).json()

    assert (await mock_complete(client, bob, payment["gateway_ref"], "succeeded")).status_code == 404
    r = await mock_complete(client, alice, payment["gateway_ref"], "succeeded")
    assert r.json() == {"payment_status": "succeeded", "booking_status": "confirmed"}
