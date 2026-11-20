from tests.conftest import TEST_PASSWORD


async def register(client, email="alice@test.com", username="alice", password="secret-password"):
    return await client.post("/auth/register", json={"username": username, "email": email, "password": password})


async def test_register_returns_user_without_password(client):
    r = await register(client)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "alice@test.com"
    assert body["role"] == "user"
    assert "password" not in body and "password_hash" not in body


async def test_register_lowercases_email_and_blocks_duplicates(client):
    await register(client, email="Alice@Test.com")
    r = await register(client, email="alice@test.com", username="alice2")
    assert r.status_code == 409


async def test_register_validates_input(client):
    r = await register(client, password="short")
    assert r.status_code == 422


async def test_login_and_me(client):
    await register(client)
    r = await client.post("/auth/login", json={"email": "alice@test.com", "password": "secret-password"})
    assert r.status_code == 200
    token = r.json()["access_token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


async def test_login_wrong_password_and_unknown_email_look_the_same(client):
    await register(client)
    wrong_pw = await client.post("/auth/login", json={"email": "alice@test.com", "password": "nope-nope"})
    no_user = await client.post("/auth/login", json={"email": "ghost@test.com", "password": "nope-nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()   # same message: doesn't reveal which emails exist


async def test_me_rejects_bad_tokens(client):
    assert (await client.get("/auth/me", headers={"Authorization": "Bearer garbage"})).status_code == 401
    assert (await client.get("/auth/me")).status_code == 401   # no header at all


async def test_refresh_gives_new_tokens_but_needs_a_refresh_token(client, make_user):
    user = await make_user()
    r = await client.post("/auth/login", json={"email": user.email, "password": TEST_PASSWORD})
    tokens = r.json()

    ok = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert ok.status_code == 200 and "access_token" in ok.json()

    # an ACCESS token must not work as a refresh token
    bad = await client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert bad.status_code == 401
