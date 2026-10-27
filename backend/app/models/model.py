import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, Numeric,
    String, UniqueConstraint, func, text,
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
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def enum_col(e: type[enum.Enum]):
    return Enum(e, native_enum=False, length=20, values_callable=lambda x: [i.value for i in x])


def check_in(column: str, e: type[enum.Enum], name: str) -> CheckConstraint:
    """CHECK (column IN ('a','b',...)) built from the enum so they never drift apart."""
    values = ", ".join(repr(m.value) for m in e)
    return CheckConstraint(f"{column} IN ({values})", name=name)


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
    __table_args__ = (check_in("role", UserRole, "ck_users_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    email: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        enum_col(UserRole), default=UserRole.USER, server_default="user"
    )


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
        check_in("status", SeatStatus, "ck_seats_status"),
        # a HELD seat must always say who holds it and until when
        CheckConstraint(
            "status <> 'held' OR (held_by_user_id IS NOT NULL AND held_until IS NOT NULL)",
            name="ck_seats_held_has_owner_and_expiry",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    seat_number: Mapped[str] = mapped_column(String(10))
    status: Mapped[SeatStatus] = mapped_column(
        enum_col(SeatStatus), default=SeatStatus.AVAILABLE, server_default="available"
    )
    held_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    held_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Booking(TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (check_in("status", BookingStatus, "ck_bookings_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    status: Mapped[BookingStatus] = mapped_column(
        enum_col(BookingStatus), default=BookingStatus.PENDING, server_default="pending"
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BookingSeat(Base):
    __tablename__ = "booking_seats"
    __table_args__ = (
        # a seat can belong to at most ONE active booking, even if app code has a bug
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
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (check_in("status", PaymentStatus, "ck_payments_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[PaymentStatus] = mapped_column(
        enum_col(PaymentStatus), default=PaymentStatus.PENDING, server_default="pending"
    )
    gateway_ref: Mapped[str | None] = mapped_column(String(100), unique=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_idem_user_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OutboxEvent(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        Index(
            "ix_outbox_unprocessed",
            "id",
            postgresql_where=text("processed_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))