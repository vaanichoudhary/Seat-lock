"""Tidy up holds and bookings whose time ran out.

The hold/booking code already treats expired things as free, so this job is
not needed for correctness. It makes what users SEE correct: the seat map
shows the seat as available again, and "my bookings" says expired, not pending.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Booking, BookingSeat, BookingStatus, Seat, SeatStatus
from app.services.bookings import release_booking

logger = logging.getLogger("seatlock.cleanup")

BATCH_SIZE = 500


@dataclass
class CleanupResult:
    bookings_expired: int
    holds_released: int


async def run_cleanup_once(db: AsyncSession) -> CleanupResult:
    now = datetime.now(timezone.utc)

    # 1. pending bookings past their expiry -> expired, and free their seats.
    # SKIP LOCKED: if a request (say, a payment webhook) is working on a booking
    # right now, leave it alone; that request decides, and we retry next run.
    result = await db.execute(
        select(Booking)
        .where(Booking.status == BookingStatus.PENDING, Booking.expires_at <= now)
        .order_by(Booking.id)
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    bookings = list(result.scalars().all())
    for booking in bookings:
        await release_booking(db, booking, BookingStatus.EXPIRED)
    await db.commit()

    # 2. held seats whose hold ran out -> available.
    # Seats that belong to an active booking are skipped: only booking code
    # (step 1, cancel, webhook) may free those, so a booking never loses its seats.
    in_booking = select(BookingSeat.seat_id).where(BookingSeat.active)
    result = await db.execute(
        update(Seat)
        .where(
            Seat.status == SeatStatus.HELD,
            Seat.held_until <= now,
            Seat.id.not_in(in_booking),
        )
        .values(status=SeatStatus.AVAILABLE, held_by_user_id=None, held_until=None)
    )
    await db.commit()

    cleaned = CleanupResult(bookings_expired=len(bookings), holds_released=result.rowcount)
    if cleaned.bookings_expired or cleaned.holds_released:
        logger.info("cleanup: %s bookings expired, %s holds released",
                    cleaned.bookings_expired, cleaned.holds_released)
    return cleaned
