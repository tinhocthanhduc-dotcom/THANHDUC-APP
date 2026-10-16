from sqlalchemy import text


def test_login_and_me(client, login):
    h = login("thu.ngan", "sales")
    r = client.get("/auth/me", headers=h)
    assert r.status_code == 200
    assert r.json()["roles"] == ["sales"]


def test_bad_password(client, make_user):
    make_user("kho58", "warehouse")
    r = client.post("/auth/login", data={"username": "kho58", "password": "sai-mat-khau-123"})
    assert r.status_code == 401
    assert r.json()["error"] == "BAD_LOGIN"


def test_requires_token(client):
    r = client.get("/customers")
    assert r.status_code == 401
    assert client.get("/customers", headers={"Authorization": "Bearer abc"}).status_code == 401


def test_inactive_user_rejected(client, login, db):
    h = login("nghi.viec", "sales")
    db.execute(text("update users set active = false where username = 'nghi.viec'"))
    db.commit()
    assert client.get("/auth/me", headers=h).status_code == 401


def test_only_admin_creates_users(client, login):
    director = login("giamdoc", "director")
    admin = login("quantri", "admin")
    body = {"username": "ketoan", "full_name": "Kế toán", "password": "matkhau-dai-123", "roles": ["finance"]}
    assert client.post("/users", json=body, headers=director).status_code == 403
    r = client.post("/users", json=body, headers=admin)
    assert r.status_code == 201
    assert client.post("/users", json=body, headers=admin).json()["error"] == "USERNAME_TAKEN"
    weak = {**body, "username": "yeu", "password": "123"}
    assert client.post("/users", json=weak, headers=admin).json()["error"] == "WEAK_PASSWORD"
    bad = {**body, "username": "lala", "roles": ["god"]}
    assert client.post("/users", json=bad, headers=admin).json()["error"] == "UNKNOWN_ROLE"


def test_audit_log_is_append_only(client, login, db):
    admin = login("quantri", "admin")
    client.post("/users", json={"username": "kt2", "full_name": "KT", "password": "matkhau-dai-123", "roles": ["finance"]}, headers=admin)
    import pytest
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        db.execute(text("delete from audit_log"))
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("update audit_log set reason = 'sua'"))
    db.rollback()
