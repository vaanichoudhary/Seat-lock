import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.core.db import SessionLocal
from app.models import Seat, SeatStatus


async def hold(client, user, event_id, seat_ids):
    return await client.post(f"/events/{event_id}/hold", json={"seat_ids": seat_ids}, headers=user.headers)


async def test_hold_free_seats(client, make_user, make_event, fetch):
    event, alice = await make_event(), await make_user()
    r = await hold(client, alice, event.id, event.seat_ids[:2])
    assert r.status_code == 200
    assert r.json()["seat_ids"] == event.seat_ids[:2]

    seat = await fetch(Seat, event.seat_ids[0])
    assert seat.status == SeatStatus.HELD and seat.held_by_user_id == alice.id


async def test_taken_seat_gives_409_and_holds_nothing(client, make_user, make_event, fetch):
    event, alice, bob = await make_event(), await make_user(), await make_user()
    a1, a2 = event.seat_ids[:2]
    await hold(client, alice, event.id, [a1])

    r = await hold(client, bob, event.id, [a1, a2])
    assert r.status_code == 409
    assert r.json()["detail"]["unavailable_seat_ids"] == [a1]
    # all or nothing: the free seat in Bob's request was NOT held either
    assert (await fetch(Seat, a2)).status == SeatStatus.AVAILABLE


async def test_race_twenty_users_one_seat_exactly_one_wins(client, make_user, make_event):
    # race bugs depend on timing, so one round can pass by luck: try 10 seats, 20 users each
    event = await make_event(seats=10)
    users = [await make_user() for _ in range(20)]

    for seat in event.seat_ids:
        responses = await asyncio.gather(*[hold(client, u, event.id, [seat]) for u in users])
        codes = sorted(r.status_code for r in responses)
        assert codes == [200] + [409] * 19, f"seat {seat}: {codes}"


async def test_opposite_order_requests_never_deadlock(client, make_user, make_event):
    event = await make_event()
    a, b = await make_user(), await make_user()
    s1, s2 = event.seat_ids[:2]
    for _ in range(10):
        async with SessionLocal() as db:   # reset both seats to free
            await db.execute(update(Seat).where(Seat.id.in_([s1, s2]))
                             .values(status=SeatStatus.AVAILABLE, held_by_user_id=None, held_until=None))
            await db.commit()
        r1, r2 = await asyncio.gather(hold(client, a, event.id, [s1, s2]), hold(client, b, event.id, [s2, s1]))
        assert sorted([r1.status_code, r2.status_code]) in ([200, 409], [409, 409])   # never two winners, never a 500


async def test_expired_hold_can_be_taken(client, make_user, make_event):
    event, alice, bob = await make_event(), await make_user(), await make_user()
    seat = event.seat_ids[0]
    await hold(client, alice, event.id, [seat])
    async with SessionLocal() as db:   # pretend 5 minutes passed
        await db.execute(update(Seat).where(Seat.id == seat)
                         .values(held_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await db.commit()
    assert (await hold(client, bob, event.id, [seat])).status_code == 200


async def test_hold_rules(client, make_user, make_event):
    alice = await make_user()
    event, other_event = await make_event(), await make_event()
    closed = await make_event(sale_open=False)

    assert (await hold(client, alice, event.id, other_event.seat_ids[:1])).status_code == 409  # seat of another event
    assert (await hold(client, alice, closed.id, closed.seat_ids[:1])).status_code == 409     # sale not open
    assert (await hold(client, alice, 999999, event.seat_ids[:1])).status_code == 404
    assert (await hold(client, alice, event.id, [event.seat_ids[0]] * 2)).status_code == 422  # duplicates
    assert (await hold(client, alice, event.id, list(range(1, 12)))).status_code == 422       # more than 10
    no_login = await client.post(f"/events/{event.id}/hold", json={"seat_ids": event.seat_ids[:1]})
    assert no_login.status_code == 401


async def test_release_only_frees_my_seats(client, make_user, make_event, fetch):
    event, alice, bob = await make_event(), await make_user(), await make_user()
    a1, a2, a3 = event.seat_ids[:3]
    await hold(client, alice, event.id, [a1, a2])
    await hold(client, bob, event.id, [a3])

    r = await client.delete(f"/events/{event.id}/hold", headers=alice.headers)
    assert r.json() == {"released": 2}
    seat = await fetch(Seat, a1)
    assert seat.status == SeatStatus.AVAILABLE and seat.held_by_user_id is None and seat.held_until is None
    assert (await fetch(Seat, a3)).held_by_user_id == bob.id
