from datetime import datetime, timedelta, timezone

from app.models import UserRole


def event_body(**overrides):
    now = datetime.now(timezone.utc)
    body = {
        "name": "Test Show",
        "starts_at": (now + timedelta(days=10)).isoformat(),
        "sale_opens_at": (now + timedelta(days=1)).isoformat(),
        "ticket_price": "499.00",
    }
    return body | overrides


async def test_only_organizers_can_create_events(client, make_user):
    user = await make_user(UserRole.USER)
    assert (await client.post("/events", json=event_body())).status_code == 401
    assert (await client.post("/events", json=event_body(), headers=user.headers)).status_code == 403


async def test_create_event_sets_organizer_from_token(client, make_user):
    org = await make_user(UserRole.ORGANIZER)
    r = await client.post("/events", json=event_body(organizer_id=999), headers=org.headers)
    assert r.status_code == 201
    assert r.json()["organizer_id"] == org.id   # never taken from the request body


async def test_sale_must_open_before_event_starts(client, make_user):
    org = await make_user(UserRole.ORGANIZER)
    now = datetime.now(timezone.utc)
    bad = event_body(sale_opens_at=(now + timedelta(days=20)).isoformat())
    assert (await client.post("/events", json=bad, headers=org.headers)).status_code == 422


async def test_get_and_list_events_with_pagination(client, make_user):
    org = await make_user(UserRole.ORGANIZER)
    ids = [(await client.post("/events", json=event_body(name=f"Show {i}"), headers=org.headers)).json()["id"]
           for i in range(3)]

    assert (await client.get(f"/events/{ids[0]}")).json()["name"] == "Show 0"
    assert (await client.get("/events/999999")).status_code == 404

    page1 = (await client.get("/events?limit=2")).json()
    page2 = (await client.get("/events?limit=2&offset=2")).json()
    assert [e["id"] for e in page1] == ids[:2]
    assert [e["id"] for e in page2] == ids[2:]
    assert (await client.get("/events?limit=1000")).status_code == 422


async def test_patch_checks_owner_and_dates(client, make_user):
    owner = await make_user(UserRole.ORGANIZER)
    other = await make_user(UserRole.ORGANIZER)
    admin = await make_user(UserRole.ADMIN)
    event_id = (await client.post("/events", json=event_body(), headers=owner.headers)).json()["id"]

    r = await client.patch(f"/events/{event_id}", json={"name": "Renamed"}, headers=owner.headers)
    assert r.status_code == 200 and r.json()["name"] == "Renamed"
    assert (await client.patch(f"/events/{event_id}", json={"name": "x"}, headers=other.headers)).status_code == 403
    assert (await client.patch(f"/events/{event_id}", json={"name": "By admin"}, headers=admin.headers)).status_code == 200

    # only one date sent: the route must compare it with the STORED starts_at
    late_sale = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    r = await client.patch(f"/events/{event_id}", json={"sale_opens_at": late_sale}, headers=owner.headers)
    assert r.status_code == 422


async def test_bulk_seats_create_once_then_409(client, make_user):
    org = await make_user(UserRole.ORGANIZER)
    event_id = (await client.post("/events", json=event_body(), headers=org.headers)).json()["id"]

    r = await client.post(f"/events/{event_id}/seats", json={"rows": 2, "seats_per_row": 3}, headers=org.headers)
    assert r.status_code == 201
    assert [s["seat_number"] for s in r.json()] == ["A1", "A2", "A3", "B1", "B2", "B3"]
    assert all(s["status"] == "available" for s in r.json())

    again = await client.post(f"/events/{event_id}/seats", json={"rows": 1, "seats_per_row": 1}, headers=org.headers)
    assert again.status_code == 409

    listed = (await client.get(f"/events/{event_id}/seats")).json()
    assert len(listed) == 6
    assert (await client.get("/events/999999/seats")).status_code == 404
