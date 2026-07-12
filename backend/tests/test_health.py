from fastapi.testclient import TestClient

from backend.app.main import app


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dev_cors() -> None:
    client = TestClient(app)
    response = client.get("/health", headers={"Origin": "http://127.0.0.1:5174"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
