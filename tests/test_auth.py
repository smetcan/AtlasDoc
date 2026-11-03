from fastapi.testclient import TestClient

from app import app, create_session_token


def test_unauthenticated_requests_are_rejected(monkeypatch):
    monkeypatch.setenv("ATLASDOC_ENFORCE_AUTH_IN_TEST", "1")
    monkeypatch.setenv("ATLASDOC_AUTH_USER", "testuser")
    monkeypatch.setenv("ATLASDOC_AUTH_PASS", "testpass-secret")

    client = TestClient(app)

    status_res = client.get("/api/auth/status").json()
    assert status_res == {"authenticated": False, "configured": True}

    assert client.post("/api/health", json={}).status_code == 401
    assert (
        client.post(
            "/api/pdf-info",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/convert",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        ).status_code
        == 401
    )


def test_login_logout_and_invalid_credentials(monkeypatch):
    monkeypatch.setenv("ATLASDOC_ENFORCE_AUTH_IN_TEST", "1")
    monkeypatch.setenv("ATLASDOC_AUTH_USER", "selcuk")
    monkeypatch.setenv("ATLASDOC_AUTH_PASS", "correct-horse-battery")

    client = TestClient(app)

    bad = client.post(
        "/api/auth/login",
        json={"username": "selcuk", "password": "wrong"},
    )
    assert bad.status_code == 401

    good = client.post(
        "/api/auth/login",
        json={"username": "selcuk", "password": "correct-horse-battery"},
    )
    assert good.status_code == 200
    assert good.json()["ok"] is True

    status_after = client.get("/api/auth/status").json()
    assert status_after["authenticated"] is True

    out = client.post("/api/auth/logout")
    assert out.status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is False


def test_login_fails_when_env_credentials_not_configured(monkeypatch):
    monkeypatch.setenv("ATLASDOC_ENFORCE_AUTH_IN_TEST", "1")
    monkeypatch.delenv("ATLASDOC_AUTH_USER", raising=False)
    monkeypatch.delenv("ATLASDOC_AUTH_PASS", raising=False)

    client = TestClient(app)
    status = client.get("/api/auth/status").json()
    assert status == {"authenticated": False, "configured": False}

    res = client.post(
        "/api/auth/login",
        json={"username": "any", "password": "any"},
    )
    assert res.status_code == 503


def test_expired_or_tampered_session_cookie_is_rejected(monkeypatch):
    monkeypatch.setenv("ATLASDOC_ENFORCE_AUTH_IN_TEST", "1")
    monkeypatch.setenv("ATLASDOC_AUTH_USER", "selcuk")
    monkeypatch.setenv("ATLASDOC_AUTH_PASS", "secret123")

    client = TestClient(app)
    token = create_session_token("selcuk", ttl_seconds=60)
    tampered = token[:-4] + "0000"
    client.cookies.set("atlasdoc_session", tampered)

    assert client.get("/api/auth/status").json()["authenticated"] is False
    assert client.post("/api/health", json={}).status_code == 401
