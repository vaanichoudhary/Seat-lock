"""Booking state changes shared by the bookings and payments routes.

None of these commit; the caller commits, so each route's changes
are saved together or not at all.
"""
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Booking, BookingSeat, BookingStatus, Seat, SeatStatus


async def release_booking(db: AsyncSession, booking: Booking, new_status: BookingStatus) -> None:
    """End a pending booking (cancelled / failed / expired) and free its seats."""
    booking.status = new_status
    seat_ids = select(BookingSeat.seat_id).where(
        BookingSeat.booking_id == booking.id, BookingSeat.active
    )
    # only free seats this user still holds; if the hold ran out and
    # someone else grabbed the seat, it is theirs now, so leave it alone
    await db.execute(
        update(Seat)
        .where(
            Seat.id.in_(seat_ids),
            Seat.status == SeatStatus.HELD,
            Seat.held_by_user_id == booking.user_id,
        )
        .values(status=SeatStatus.AVAILABLE, held_by_user_id=None, held_until=None)
    )
    await db.execute(
        update(BookingSeat).where(BookingSeat.booking_id == booking.id).values(active=False)
    )


async def confirm_booking(db: AsyncSession, booking: Booking) -> None:
    """Payment succeeded: the booking is final and its seats are sold."""
    booking.status = BookingStatus.CONFIRMED
    seat_ids = select(BookingSeat.seat_id).where(
        BookingSeat.booking_id == booking.id, BookingSeat.active
    )
    # booking_seats stay active: that keeps the seat tied to this booking for good
    await db.execute(
        update(Seat)
        .where(Seat.id.in_(seat_ids))
        .values(status=SeatStatus.BOOKED, held_by_user_id=None, held_until=None)
    )


async def expire_stale_bookings(db: AsyncSession, seat_ids: list[int], now: datetime) -> None:
    """Mark old pending bookings on these seats as expired, so the seats can be booked again.

    There is no cleanup job yet, so a pending booking past its expiry still
    "owns" its seats in booking_seats. Call this before linking the seats to
    a new booking. It doesn't touch the seats themselves; the caller holds them.
    """
    stale_ids = (
        select(Booking.id)
        .join(BookingSeat, BookingSeat.booking_id == Booking.id)
        .where(
            BookingSeat.seat_id.in_(seat_ids),
            BookingSeat.active,
            Booking.status == BookingStatus.PENDING,
            Booking.expires_at <= now,
        )
    )
    stale = list((await db.execute(stale_ids)).scalars().all())
    if not stale:
        return
    await db.execute(
        update(Booking).where(Booking.id.in_(stale)).values(status=BookingStatus.EXPIRED)
    )
    await db.execute(
        update(BookingSeat).where(BookingSeat.booking_id.in_(stale)).values(active=False)
    )
