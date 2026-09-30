from agripulse_api.rbac import ROLES


def test_all_nine_roles_exist(client):
    names = {r["name"] for r in client.get("/auth/roles").json()}
    assert names == set(ROLES) and len(names) == 9


def test_every_role_can_log_in(client, as_role):
    for role in ROLES:
        me = client.get("/auth/me", headers=as_role(role))
        assert me.status_code == 200
        assert me.json()["role"] == role


def test_bad_password_and_missing_token(client):
    assert client.post("/auth/login", json={"email": "farmer@demo.agripulse", "password": "nope"}).status_code == 401
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_register_rules(client):
    ok = client.post(
        "/auth/register",
        json={"email": "new@x.in", "password": "longenough", "full_name": "New Farmer", "role": "farmer"},
    )
    assert ok.status_code == 201 and ok.json()["user"]["org_id"] is None
    dup = client.post(
        "/auth/register",
        json={"email": "NEW@x.in", "password": "longenough", "full_name": "Dup", "role": "farmer"},
    )
    assert dup.status_code == 409
    admin = client.post(
        "/auth/register", json={"email": "a@x.in", "password": "longenough", "full_name": "A", "role": "admin"}
    )
    assert admin.status_code == 403
    fpo_no_org = client.post(
        "/auth/register", json={"email": "f@x.in", "password": "longenough", "full_name": "F", "role": "fpo"}
    )
    assert fpo_no_org.status_code == 400
    fpo = client.post(
        "/auth/register",
        json={"email": "f@x.in", "password": "longenough", "full_name": "F", "role": "fpo", "org_name": "New FPO"},
    )
    assert fpo.status_code == 201 and fpo.json()["user"]["org_name"] == "New FPO"
    trader_no_mandi = client.post(
        "/auth/register", json={"email": "t@x.in", "password": "longenough", "full_name": "T", "role": "trader"}
    )
    assert trader_no_mandi.status_code == 400


def test_joining_someone_elses_fpo_is_blocked(client, as_role):
    fpo_org = client.get("/auth/me", headers=as_role("fpo")).json()["org_id"]
    r = client.post(
        "/auth/register",
        json={"email": "sneak@x.in", "password": "longenough", "full_name": "S", "role": "fpo", "org_id": fpo_org},
    )
    assert r.status_code == 403


def test_admin_endpoints_are_admin_only(client, as_role):
    for role in ROLES:
        code = client.get("/admin/users", headers=as_role(role)).status_code
        assert code == (200 if role == "admin" else 403), role


def test_admin_can_disable_user(client, as_role):
    users = client.get("/admin/users", headers=as_role("admin")).json()
    buyer = next(u for u in users if u["role"] == "buyer")
    headers = as_role("buyer")
    r = client.patch(f"/admin/users/{buyer['id']}", json={"is_active": False}, headers=as_role("admin"))
    assert r.status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 401
