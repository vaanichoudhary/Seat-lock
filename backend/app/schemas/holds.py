from datetime import datetime

from pydantic import BaseModel, Field, field_validator

MAX_SEATS_PER_HOLD = 10


class HoldRequest(BaseModel):
    seat_ids: list[int] = Field(min_length=1, max_length=MAX_SEATS_PER_HOLD)

    @field_validator("seat_ids")
    @classmethod
    def no_duplicates(cls, v: list[int]) -> list[int]:
        if len(set(v)) != len(v):
            raise ValueError("seat_ids must not contain duplicates")
        return v


class HoldResponse(BaseModel):
    event_id: int
    seat_ids: list[int]
    held_until: datetime


class MyHoldResponse(BaseModel):
    seat_ids: list[int]              # empty if you hold nothing
    held_until: datetime | None


class ReleaseResponse(BaseModel):
    released: int
