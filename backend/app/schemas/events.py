from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.models import SeatStatus


class EventCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    photo_url: str | None = Field(default=None, max_length=255)
    starts_at: AwareDatetime
    sale_opens_at: AwareDatetime
    ticket_price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)

    @model_validator(mode="after")
    def sale_opens_before_start(self):
        if self.sale_opens_at >= self.starts_at:
            raise ValueError("sale_opens_at must be before starts_at")
        return self


class EventUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    photo_url: str | None = Field(default=None, max_length=255)
    starts_at: AwareDatetime | None = None
    sale_opens_at: AwareDatetime | None = None
    ticket_price: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=2)

    @model_validator(mode="after")
    def sale_opens_before_start(self):
        # only checkable when the client sends both; the route must also
        # check against the stored value when only one is sent
        if self.starts_at and self.sale_opens_at and self.sale_opens_at >= self.starts_at:
            raise ValueError("sale_opens_at must be before starts_at")
        return self


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organizer_id: int
    name: str
    description: str | None
    photo_url: str | None
    starts_at: AwareDatetime
    sale_opens_at: AwareDatetime
    ticket_price: Decimal


class SeatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    seat_number: str
    status: SeatStatus


class BulkSeatsRequest(BaseModel):
    rows: int = Field(ge=1, le=26)             # 3 -> rows A, B, C
    seats_per_row: int = Field(ge=1, le=100)   # 10 -> A1..A10