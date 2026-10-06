import uuid

from fastapi.testclient import TestClient

from forge import ratelimit
from forge.api.main import create_app
from tests.conftest import auth_headers, signup


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["database"] is True


def test_signup_me_logout(client):
    me = signup(client)
    assert me["user"]["email"] == "a@example.com"
    assert len(me["workspaces"]) == 1
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout", headers=auth_headers(me)).status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_unauthenticated_is_rejected(client):
    assert client.get("/api/projects").status_code == 401


def test_session_cookie_is_httponly(client):
    r = client.post("/api/auth/signup", json={"email": "c@example.com", "password": "correct-horse-battery"})
    assert "httponly" in r.headers["set-cookie"].lower()
    assert "samesite=lax" in r.headers["set-cookie"].lower()


def test_csrf_required_on_mutation(client):
    signup(client)
    r = client.post("/api/projects", json={"name": "P"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"


def test_bad_login_is_generic(client):
    signup(client)
    other = TestClient(create_app())
    r = other.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong-password-xx"})
    assert r.status_code == 401
    r2 = other.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-xx"})
    assert r2.status_code == 401 and r.json() == r2.json()


def test_duplicate_email(client):
    signup(client)
    other = TestClient(create_app())
    r = other.post("/api/auth/signup", json={"email": "A@example.com", "password": "correct-horse-battery"})
    assert r.status_code == 409


def test_short_password_rejected(client):
    r = client.post("/api/auth/signup", json={"email": "z@example.com", "password": "short"})
    assert r.status_code == 422


def test_workspace_isolation_between_users(client):
    me_a = signup(client, "a@example.com")
    pa = client.post("/api/projects", json={"name": "A-only"}, headers=auth_headers(me_a)).json()

    b = TestClient(create_app())
    me_b = signup(b, "b@example.com")
    # B cannot read, list or delete A's project
    assert b.get(f"/api/projects/{pa['id']}").status_code == 404
    assert b.get("/api/projects").json()["projects"] == []
    assert b.delete(f"/api/projects/{pa['id']}", headers=auth_headers(me_b)).status_code == 404
    # B cannot select A's workspace via header
    a_ws = me_a["workspaces"][0]["id"]
    r = b.get("/api/projects", headers={"x-workspace-id": a_ws})
    assert r.status_code == 404
    # A's project still exists
    assert client.get(f"/api/projects/{pa['id']}").status_code == 200


def test_random_workspace_header_is_not_confirmed(client):
    signup(client)
    r = client.get("/api/projects", headers={"x-workspace-id": str(uuid.uuid4())})
    assert r.status_code == 404
    assert client.get("/api/projects", headers={"x-workspace-id": "not-a-uuid"}).status_code == 400


def test_project_crud(client):
    me = signup(client)
    h = auth_headers(me)
    p = client.post("/api/projects", json={"name": "Security Audit", "description": "d"}, headers=h).json()
    assert client.get("/api/projects").json()["projects"][0]["name"] == "Security Audit"
    assert client.delete(f"/api/projects/{p['id']}", headers=h).status_code == 204
    assert client.get(f"/api/projects/{p['id']}").status_code == 404
    assert client.get("/api/projects").json()["projects"] == []


def test_auth_rate_limit(client, monkeypatch):
    # A huge window makes the bucket boundary fixed, so the test cannot straddle a window rollover.
    monkeypatch.setitem(ratelimit.LIMITS, "auth", (10, 10**9))
    for _ in range(10):
        client.post("/api/auth/login", json={"email": "x@example.com", "password": "wrong-password-xx"})
    r = client.post("/api/auth/login", json={"email": "x@example.com", "password": "wrong-password-xx"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"
