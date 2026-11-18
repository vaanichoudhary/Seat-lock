from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models import BookingStatus, PaymentStatus


class BookingCreate(BaseModel):
    event_id: int   # books every seat you are currently holding for this event


class BookingSeatOut(BaseModel):
    seat_id: int
    seat_number: str
    price: Decimal


class BookingOut(BaseModel):
    id: int
    event_id: int
    status: BookingStatus
    total_amount: Decimal
    expires_at: datetime
    created_at: datetime
    seats: list[BookingSeatOut]


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int
    amount: Decimal
    status: PaymentStatus
    gateway_ref: str


class WebhookRequest(BaseModel):
    gateway_ref: str
    status: Literal["succeeded", "failed"]


class WebhookResponse(BaseModel):
    payment_status: PaymentStatus
    booking_status: BookingStatus
