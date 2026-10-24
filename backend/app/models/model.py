import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


# ---------- enums ----------
class UserRole(str, enum.Enum):
    USER = "user"
    ORGANIZER = "organizer"
    ADMIN = "admin"


class SeatStatus(str, enum.Enum):
    AVAILABLE = "available"
    HELD = "held"
    BOOKED = "booked"


class BookingStatus(str, enum.Enum):
    PENDING = "pending"        # seats held, waiting for payment
    CONFIRMED = "confirmed"    # payment succeeded
    FAILED = "failed"          # payment failed
    EXPIRED = "expired"        # hold timed out
    CANCELLED = "cancelled"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def enum_col(e: type[enum.Enum]):
    return Enum(e, native_enum=False, length=20, values_callable=lambda x: [i.value for i in x])


# ---------- shared timestamps ----------
class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ---------- tables ----------
class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    email: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(enum_col(UserRole), default=UserRole.USER)


class Event(TimestampMixin, Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    organizer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(255))
    photo_url: Mapped[str | None] = mapped_column(String(255))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sale_opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ticket_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class Seat(TimestampMixin, Base):
    __tablename__ = "seats"
    __table_args__ = (
        UniqueConstraint("event_id", "seat_number", name="uq_seat_per_event"),
        Index("ix_seats_event_status", "event_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    seat_number: Mapped[str] = mapped_column(String(10))
    status: Mapped[SeatStatus] = mapped_column(
        enum_col(SeatStatus), default=SeatStatus.AVAILABLE
    )
    held_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    held_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Booking(TimestampMixin, Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    status: Mapped[BookingStatus] = mapped_column(
        enum_col(BookingStatus), default=BookingStatus.PENDING
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BookingSeat(Base):
    __tablename__ = "booking_seats"
    __table_args__ = (
        # THE SAFETY NET: a seat can belong to at most ONE active booking,
        # even if application code has a bug.
        Index(
            "uq_one_active_booking_per_seat",
            "seat_id",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id"))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))  # price at time of booking
    active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[PaymentStatus] = mapped_column(
        enum_col(PaymentStatus), default=PaymentStatus.PENDING
    )
    # id from the (mock) gateway; lets webhooks find their payment
    gateway_ref: Mapped[str | None] = mapped_column(String(100), unique=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_idem_user_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))  # same key + different body = error
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OutboxEvent(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        # worker only scans unprocessed rows, so keep that index tiny
        Index(
            "ix_outbox_unprocessed",
            "id",
            postgresql_where=text("processed_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(50))   # e.g. "booking.confirmed"
    payload: Mapped[dict] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))