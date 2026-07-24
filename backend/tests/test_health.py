from fastapi.testclient import TestClient

from backend.app.main import app


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_default_cors_allows_local_frontend_origin() -> None:
    client = TestClient(app)
    response = client.get("/health", headers={"Origin": "http://127.0.0.1:5174"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5174"


def test_default_cors_rejects_unlisted_browser_origin() -> None:
    client = TestClient(app)
    response = client.get("/health", headers={"Origin": "https://unlisted.example.com"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_allows_only_configured_method_and_header() -> None:
    client = TestClient(app)
    response = client.options(
        "/api/sessions",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5174"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers
