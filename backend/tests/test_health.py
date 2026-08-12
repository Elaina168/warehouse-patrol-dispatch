from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path

from backend.app.main import app, configure_frontend_static


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


def test_cors_preflight_rejects_unconfigured_method() -> None:
    response = TestClient(app).options(
        "/api/sessions",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "PUT",
        },
    )

    assert response.status_code == 400


def test_cors_preflight_rejects_unconfigured_header() -> None:
    response = TestClient(app).options(
        "/api/sessions",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Test",
        },
    )

    assert response.status_code == 400


def test_production_static_serves_assets_and_spa_without_shadowing_unknown_api(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<h1>competition app</h1>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    static_app = FastAPI()

    @static_app.get("/api/project")
    def project_route() -> dict[str, str]:
        return {"name": "api"}

    configure_frontend_static(static_app, dist)
    client = TestClient(static_app)

    assert client.get("/assets/app.js").text == "console.log('ok')"
    assert "competition app" in client.get("/competition/demo").text
    unknown_api = client.get("/api/does-not-exist")
    assert unknown_api.status_code == 404
    assert unknown_api.headers["content-type"].startswith("application/json")


def test_missing_frontend_dist_does_not_block_api_startup(tmp_path: Path) -> None:
    api_only_app = FastAPI()

    @api_only_app.get("/health")
    def health_route() -> dict[str, str]:
        return {"status": "ok"}

    configure_frontend_static(api_only_app, tmp_path / "missing")
    response = TestClient(api_only_app).get("/health")
    assert response.status_code == 200
