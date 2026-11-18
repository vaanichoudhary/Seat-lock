from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import Booking, BookingSeat, BookingStatus, Event, Seat, SeatStatus, User
from app.schemas.bookings import BookingCreate, BookingOut, BookingSeatOut
from app.services.bookings import expire_stale_bookings, release_booking

router = APIRouter(prefix="/bookings", tags=["bookings"])


async def _with_seats(db: AsyncSession, bookings: list[Booking]) -> list[BookingOut]:
    """Attach seat numbers to bookings using ONE query, not one per booking."""
    rows = await db.execute(
        select(BookingSeat.booking_id, BookingSeat.seat_id, Seat.seat_number, BookingSeat.price)
        .join(Seat, Seat.id == BookingSeat.seat_id)
        .where(BookingSeat.booking_id.in_([b.id for b in bookings]))
        .order_by(BookingSeat.seat_id)
    )
    seats: dict[int, list[BookingSeatOut]] = {b.id: [] for b in bookings}
    for booking_id, seat_id, seat_number, price in rows:
        seats[booking_id].append(BookingSeatOut(seat_id=seat_id, seat_number=seat_number, price=price))
    return [
        BookingOut(
            id=b.id, event_id=b.event_id, status=b.status, total_amount=b.total_amount,
            expires_at=b.expires_at, created_at=b.created_at, seats=seats[b.id],
        )
        for b in bookings
    ]


async def get_own_booking(db: AsyncSession, booking_id: int, user: User, lock: bool = False) -> Booking:
    query = select(Booking).where(Booking.id == booking_id)
    if lock:
        query = query.with_for_update()
    booking = (await db.execute(query)).scalar_one_or_none()
    # someone else's booking gets the same 404 as a missing one,
    # so you can't discover which booking ids exist
    if booking is None or booking.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    return booking


@router.post("", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
async def create_booking(
    body: BookingCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    event = await db.get(Event, body.event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    now = datetime.now(timezone.utc)
    # every seat this user holds for the event, whose hold hasn't run out.
    # FOR UPDATE: nobody can change these rows until we commit
    result = await db.execute(
        select(Seat)
        .where(
            Seat.event_id == event.id,
            Seat.held_by_user_id == user.id,
            Seat.status == SeatStatus.HELD,
            Seat.held_until > now,
        )
        .order_by(Seat.id)
        .with_for_update()
    )
    seats = list(result.scalars().all())
    if not seats:
        raise HTTPException(status.HTTP_409_CONFLICT, "You are not holding any seats for this event")

    await expire_stale_bookings(db, [s.id for s in seats], now)

    # the booking lives exactly as long as the shortest hold, and all the
    # holds are lined up to end together with it
    expires_at = min(s.held_until for s in seats)
    for seat in seats:
        seat.held_until = expires_at

    booking = Booking(
        user_id=user.id,
        event_id=event.id,
        status=BookingStatus.PENDING,
        total_amount=event.ticket_price * len(seats),
        expires_at=expires_at,
    )
    db.add(booking)
    await db.flush()  # sends the INSERT now, so booking.id exists for the rows below
    db.add_all(BookingSeat(booking_id=booking.id, seat_id=s.id, price=event.ticket_price) for s in seats)

    try:
        await db.commit()
    except IntegrityError:  # uq_one_active_booking_per_seat
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "These seats are already in a pending booking")

    await db.refresh(booking)  # load created_at, which the database filled in
    return (await _with_seats(db, [booking]))[0]


@router.get("/me", response_model=list[BookingOut])
async def my_bookings(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Booking).where(Booking.user_id == user.id).order_by(Booking.id.desc())
    )
    return await _with_seats(db, list(result.scalars().all()))


@router.get("/{booking_id}", response_model=BookingOut)
async def get_booking(
    booking_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    booking = await get_own_booking(db, booking_id, user)
    return (await _with_seats(db, [booking]))[0]


@router.post("/{booking_id}/cancel", response_model=BookingOut)
async def cancel_booking(
    booking_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    booking = await get_own_booking(db, booking_id, user, lock=True)
    if booking.status != BookingStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot cancel a {booking.status.value} booking")
    await release_booking(db, booking, BookingStatus.CANCELLED)
    await db.commit()
    return (await _with_seats(db, [booking]))[0]
