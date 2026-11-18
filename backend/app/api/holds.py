from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.db import get_db
from app.models import Event, Seat, SeatStatus, User
from app.schemas.holds import HoldRequest, HoldResponse, ReleaseResponse

router = APIRouter(prefix="/events", tags=["holds"])


@router.post("/{event_id}/hold", response_model=HoldResponse)
async def hold_seats(
    event_id: int,
    body: HoldRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    event = await db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    now = datetime.now(timezone.utc)
    if now < event.sale_opens_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket sales have not opened yet")
    if now >= event.starts_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "Event has already started")

    # Step 1: lock the requested seats that are free right now.
    # - a seat is free if it is available, or its hold has run out
    #   (so expired holds free themselves, even before a cleanup job exists)
    # - FOR UPDATE locks the rows until we commit, so no one else can take them
    # - SKIP LOCKED: if another request is holding a row lock this very moment,
    #   skip that seat instead of waiting (it counts as taken, and we never deadlock)
    # - ORDER BY id: always lock in the same order
    free = or_(
        Seat.status == SeatStatus.AVAILABLE,
        and_(Seat.status == SeatStatus.HELD, Seat.held_until < now),
    )
    result = await db.execute(
        select(Seat.id)
        .where(Seat.id.in_(body.seat_ids), Seat.event_id == event_id, free)
        .order_by(Seat.id)
        .with_for_update(skip_locked=True)
    )
    locked_ids = list(result.scalars().all())

    # Step 2: all or nothing. If even one seat is missing, hold none of them.
    if len(locked_ids) != len(body.seat_ids):
        await db.rollback()  # releases the row locks we did get
        unavailable = sorted(set(body.seat_ids) - set(locked_ids))
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "Some seats are not available", "unavailable_seat_ids": unavailable},
        )

    # Step 3: we own the locks, so this update cannot race with anyone
    held_until = now + timedelta(minutes=settings.hold_minutes)
    await db.execute(
        update(Seat)
        .where(Seat.id.in_(locked_ids))
        .values(status=SeatStatus.HELD, held_by_user_id=user.id, held_until=held_until)
    )
    await db.commit()

    return HoldResponse(event_id=event_id, seat_ids=locked_ids, held_until=held_until)


@router.delete("/{event_id}/hold", response_model=ReleaseResponse)
async def release_seats(
    event_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # give back every seat this user is holding for this event
    result = await db.execute(
        update(Seat)
        .where(
            Seat.event_id == event_id,
            Seat.held_by_user_id == user.id,
            Seat.status == SeatStatus.HELD,
        )
        .values(status=SeatStatus.AVAILABLE, held_by_user_id=None, held_until=None)
    )
    await db.commit()
    return ReleaseResponse(released=result.rowcount)
