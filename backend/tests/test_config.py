import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from backend.app.config import CORS_ORIGINS_ENV, cors_allowed_origins


def test_cors_defaults_to_two_local_frontend_origins() -> None:
    assert cors_allowed_origins({}) == [
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ]


def test_cors_parses_trims_and_deduplicates_explicit_origins() -> None:
    assert cors_allowed_origins({
        "WAREHOUSE_PATROL_CORS_ORIGINS": (
            " http://192.168.1.10:5174,"
            "https://demo.example.com,"
            "http://192.168.1.10:5174 "
        )
    }) == [
        "http://192.168.1.10:5174",
        "https://demo.example.com",
    ]


def test_cors_normalizes_browser_serialized_origins_before_deduplication() -> None:
    assert cors_allowed_origins({
        CORS_ORIGINS_ENV: (
            "HTTP://EXAMPLE.COM,http://example.com:80,"
            "HTTPS://EXAMPLE.COM:443,https://example.com,"
            "https://example.com:8443,"
            "HTTP://[2001:0DB8:0000:0000:0000:0000:0000:0001]:80,"
            "http://[2001:db8::1]"
        )
    }) == [
        "http://example.com",
        "https://example.com",
        "https://example.com:8443",
        "http://[2001:db8::1]",
    ]


def test_cors_middleware_matches_browser_normalized_origin_from_explicit_config() -> None:
    temporary_app = FastAPI()
    temporary_app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allowed_origins({CORS_ORIGINS_ENV: "HTTP://EXAMPLE.COM:80"}),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Content-Type"],
    )

    @temporary_app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    response = TestClient(temporary_app).get(
        "/health",
        headers={"Origin": "http://example.com"},
    )

    assert response.headers["access-control-allow-origin"] == "http://example.com"


@pytest.mark.parametrize(
    "value",
    [
        "",
        " , ",
        "*",
        "ftp://example.com",
        "http://",
        "http://example.com/path",
        "http://example.com?x=1",
        "http://example.com#fragment",
        "http://user:password@example.com",
        "http://faß.de",
        "http://0x7f000001",
        "http://0x7f.0x0.0x0.0x1",
        "http://127.1",
        "http://2130706433",
    ],
)
def test_cors_rejects_invalid_or_non_origin_values(value: str) -> None:
    with pytest.raises(ValueError, match="WAREHOUSE_PATROL_CORS_ORIGINS"):
        cors_allowed_origins({"WAREHOUSE_PATROL_CORS_ORIGINS": value})
