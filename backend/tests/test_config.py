import pytest

from backend.app.config import cors_allowed_origins


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
    ],
)
def test_cors_rejects_invalid_or_non_origin_values(value: str) -> None:
    with pytest.raises(ValueError, match="WAREHOUSE_PATROL_CORS_ORIGINS"):
        cors_allowed_origins({"WAREHOUSE_PATROL_CORS_ORIGINS": value})
