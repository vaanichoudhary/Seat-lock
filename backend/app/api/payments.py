import hashlib
import hmac
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.bookings import get_own_booking
from app.api.deps import get_current_user
from app.core.config import settings
from app.core.db import get_db
from app.models import (
    Booking, BookingStatus, IdempotencyKey, OutboxEvent, Payment, PaymentStatus, User,
)
from app.schemas.bookings import PaymentOut, WebhookRequest, WebhookResponse
from app.services.bookings import confirm_booking, release_booking

router = APIRouter(tags=["payments"])


def _replay(saved: IdempotencyKey, request_hash: str) -> JSONResponse:
    """Answer a repeated request with the response we already sent the first time."""
    if saved.request_hash != request_hash:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This Idempotency-Key was already used for a different request",
        )
    return JSONResponse(status_code=saved.response_status, content=saved.response_body)


async def _find_key(db: AsyncSession, user: User, key: str) -> IdempotencyKey | None:
    result = await db.execute(
        select(IdempotencyKey).where(IdempotencyKey.user_id == user.id, IdempotencyKey.key == key)
    )
    return result.scalar_one_or_none()


@router.post("/bookings/{booking_id}/pay", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
async def pay_for_booking(
    booking_id: int,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # fingerprint of "what this request asks for"; the same key must always mean the same request
    request_hash = hashlib.sha256(f"pay:{booking_id}".encode()).hexdigest()

    # lock the booking first: two pay requests for one booking now run one after the other
    booking = await get_own_booking(db, booking_id, user, lock=True)

    # seen this key before? send back the saved answer instead of charging again
    saved = await _find_key(db, user, idempotency_key)
    if saved is not None:
        return _replay(saved, request_hash)

    if booking.status != BookingStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Booking is {booking.status.value}")
    if booking.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_409_CONFLICT, "Booking has expired, please hold the seats again")

    # a different key for a booking that already has a payment going is still a double charge
    open_payment = await db.execute(
        select(Payment.id).where(
            Payment.booking_id == booking.id,
            Payment.status.in_([PaymentStatus.PENDING, PaymentStatus.SUCCEEDED]),
        )
    )
    if open_payment.first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This booking already has a payment in progress")

    # with a real gateway (Stripe, Razorpay) we would call its API here and get its id back
    payment = Payment(
        booking_id=booking.id,
        amount=booking.total_amount,
        status=PaymentStatus.PENDING,
        gateway_ref=f"mock_{uuid.uuid4().hex}",
    )
    db.add(payment)
    await db.flush()  # gives payment.id

    body = jsonable_encoder(PaymentOut.model_validate(payment))
    # saved in the SAME transaction as the payment: both are saved, or neither
    db.add(IdempotencyKey(
        user_id=user.id, key=idempotency_key, request_hash=request_hash,
        response_status=status.HTTP_201_CREATED, response_body=body,
    ))
    try:
        await db.commit()
    except IntegrityError:  # uq_idem_user_key: an identical request just finished first
        await db.rollback()
        saved = await _find_key(db, user, idempotency_key)
        if saved is None:
            raise
        return _replay(saved, request_hash)

    return JSONResponse(status_code=status.HTTP_201_CREATED, content=body)


@router.post("/payments/webhook", response_model=WebhookResponse)
async def payment_webhook(
    body: WebhookRequest,
    webhook_secret: str = Header(alias="X-Webhook-Secret"),
    db: AsyncSession = Depends(get_db),
):
    """Called by the payment gateway (here: by you, from /docs) when a payment finishes."""
    # compare_digest takes the same time whether the first or last character is wrong,
    # so the secret can't be guessed one character at a time
    if not hmac.compare_digest(webhook_secret, settings.payment_webhook_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook secret")

    result = await db.execute(
        select(Payment).where(Payment.gateway_ref == body.gateway_ref).with_for_update()
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown payment")

    booking = (
        await db.execute(select(Booking).where(Booking.id == payment.booking_id).with_for_update())
    ).scalar_one()

    # gateways retry webhooks; if we already handled this payment, just report the result again
    if payment.status != PaymentStatus.PENDING:
        return WebhookResponse(payment_status=payment.status, booking_status=booking.status)

    if body.status == "failed":
        payment.status = PaymentStatus.FAILED
        if booking.status == BookingStatus.PENDING:
            await release_booking(db, booking, BookingStatus.FAILED)
    else:
        payment.status = PaymentStatus.SUCCEEDED
        if booking.status == BookingStatus.PENDING and booking.expires_at > datetime.now(timezone.utc):
            await confirm_booking(db, booking)
            # written in the same transaction, so "confirmed" and "send email" can't get out of sync;
            # a worker (built later) reads the outbox table and does the sending
            db.add(OutboxEvent(
                event_type="booking.confirmed",
                payload={"booking_id": booking.id, "user_id": booking.user_id},
            ))
        else:
            # money arrived, but the booking was cancelled or ran out of time: give it back
            if booking.status == BookingStatus.PENDING:
                await release_booking(db, booking, BookingStatus.EXPIRED)
            db.add(OutboxEvent(
                event_type="payment.refund_required",
                payload={"payment_id": payment.id, "booking_id": booking.id, "amount": str(payment.amount)},
            ))

    await db.commit()
    return WebhookResponse(payment_status=payment.status, booking_status=booking.status)
