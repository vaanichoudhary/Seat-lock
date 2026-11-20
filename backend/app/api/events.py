import string

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.db import get_db
from app.models import Event, Seat, User, UserRole
from app.schemas.events import BulkSeatsRequest, EventCreate, EventOut, EventUpdate, SeatOut

router = APIRouter(prefix="/events", tags=["events"])

# only these roles may create or change events
organizer_only = require_role(UserRole.ORGANIZER, UserRole.ADMIN)


async def _get_event_or_404(db: AsyncSession, event_id: int) -> Event:
    event = await db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return event


def _check_owner(event: Event, user: User) -> None:
    # admins can edit any event, organizers only their own
    if user.role != UserRole.ADMIN and event.organizer_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your event")


@router.post("", response_model=EventOut, status_code=status.HTTP_201_CREATED)
async def create_event(
    body: EventCreate,
    user: User = Depends(organizer_only),
    db: AsyncSession = Depends(get_db),
):
    event = Event(**body.model_dump(), organizer_id=user.id)
    db.add(event)
    await db.commit()
    return event


@router.get("/{event_id}", response_model=EventOut)
async def get_event(event_id: int, db: AsyncSession = Depends(get_db)):
    return await _get_event_or_404(db, event_id)


@router.patch("/{event_id}", response_model=EventOut)
async def update_event(
    event_id: int,
    body: EventUpdate,
    user: User = Depends(organizer_only),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_or_404(db, event_id)
    _check_owner(event, user)

    changes = body.model_dump(exclude_unset=True)

    # the schema can only compare the two dates when both are sent,
    # so compare again using the stored value for whichever one is missing
    starts_at = changes.get("starts_at", event.starts_at)
    sale_opens_at = changes.get("sale_opens_at", event.sale_opens_at)
    if sale_opens_at >= starts_at:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "sale_opens_at must be before starts_at")

    for field, value in changes.items():
        setattr(event, field, value)
    await db.commit()
    return event


@router.get("/{event_id}/seats", response_model=list[SeatOut])
async def list_seats(event_id: int, db: AsyncSession = Depends(get_db)):
    await _get_event_or_404(db, event_id)
    result = await db.execute(select(Seat).where(Seat.event_id == event_id).order_by(Seat.id))
    return result.scalars().all()


@router.post("/{event_id}/seats", response_model=list[SeatOut], status_code=status.HTTP_201_CREATED)
async def create_seats(
    event_id: int,
    body: BulkSeatsRequest,
    user: User = Depends(organizer_only),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_or_404(db, event_id)
    _check_owner(event, user)

    # rows=2, seats_per_row=3  ->  A1 A2 A3 B1 B2 B3
    row_letters = string.ascii_uppercase[: body.rows]
    seats = [
        Seat(event_id=event.id, seat_number=f"{row}{n}")
        for row in row_letters
        for n in range(1, body.seats_per_row + 1)
    ]
    db.add_all(seats)
    try:
        await db.commit()
    except IntegrityError:  # uq_seat_per_event: some of these seats already exist
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Seats already exist for this event")
    return seats

@router.get("", response_model=list[EventOut])
async def list_events(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Event).order_by(Event.id).limit(limit).offset(offset))
    return result.scalars().all()
