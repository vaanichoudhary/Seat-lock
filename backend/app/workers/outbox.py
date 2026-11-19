"""Deliver outbox events (the "to-do list" written by the payment webhook).

Delivery is AT LEAST ONCE: if the server crashes after sending but before
saving processed_at, the event is sent again on the next run. So handlers
should be safe to run twice (a real email service would use the event id
to drop duplicates).
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import BookingSeat, OutboxEvent, Seat, User

logger = logging.getLogger("seatlock.outbox")

BATCH_SIZE = 20


async def _send_booking_confirmed(db: AsyncSession, event: OutboxEvent) -> None:
    user = await db.get(User, event.payload["user_id"])
    seats = await db.execute(
        select(Seat.seat_number)
        .join(BookingSeat, BookingSeat.seat_id == Seat.id)
        .where(BookingSeat.booking_id == event.payload["booking_id"])
        .order_by(Seat.id)
    )
    # a real app would call an email service (SES, SendGrid, ...) here
    logger.info("EMAIL to %s: booking #%s confirmed, seats %s",
                user.email, event.payload["booking_id"], ", ".join(seats.scalars()))


async def _send_refund_required(db: AsyncSession, event: OutboxEvent) -> None:
    # a real app would call the payment gateway's refund API here
    logger.info("REFUND %s for payment #%s (booking #%s arrived too late)",
                event.payload["amount"], event.payload["payment_id"], event.payload["booking_id"])


HANDLERS = {
    "booking.confirmed": _send_booking_confirmed,
    "payment.refund_required": _send_refund_required,
}


async def process_outbox_once(db: AsyncSession) -> int:
    """Handle one batch of unprocessed events. Returns how many succeeded."""
    # SKIP LOCKED: if two servers run this at once, each grabs different rows,
    # so no event is handled twice at the same time
    result = await db.execute(
        select(OutboxEvent)
        .where(
            OutboxEvent.processed_at.is_(None),
            OutboxEvent.attempts < settings.outbox_max_attempts,
        )
        .order_by(OutboxEvent.id)  # oldest first
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    events = list(result.scalars().all())

    done = 0
    for event in events:
        handler = HANDLERS.get(event.event_type)
        try:
            # savepoint: if this handler breaks the transaction (e.g. a bad query),
            # only its own work is undone; the other events in the batch still save
            async with db.begin_nested():
                if handler is None:
                    raise ValueError(f"no handler for event type {event.event_type!r}")
                await handler(db, event)
        except Exception:
            # failed: count the attempt and try again next run.
            # After outbox_max_attempts it is skipped for good, and stays in the
            # table (processed_at still empty) for a human to look at.
            event.attempts += 1
            if event.attempts >= settings.outbox_max_attempts:
                logger.exception("outbox event %s gave up after %s attempts", event.id, event.attempts)
            else:
                logger.exception("outbox event %s failed (attempt %s), will retry", event.id, event.attempts)
            continue
        event.processed_at = datetime.now(timezone.utc)
        done += 1

    await db.commit()
    return done
