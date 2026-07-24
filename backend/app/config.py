import os
from collections.abc import Mapping
from urllib.parse import urlsplit

CORS_ORIGINS_ENV = "WAREHOUSE_PATROL_CORS_ORIGINS"
DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5174",
    "http://localhost:5174",
)


def cors_allowed_origins(
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    source = os.environ if environ is None else environ
    if CORS_ORIGINS_ENV not in source:
        return list(DEFAULT_CORS_ORIGINS)
    values = [item.strip() for item in source[CORS_ORIGINS_ENV].split(",")]
    values = [item for item in values if item]
    if not values:
        raise ValueError(f"{CORS_ORIGINS_ENV} must contain at least one origin")

    origins: list[str] = []
    for value in values:
        parsed = urlsplit(value)
        try:
            parsed.port
        except ValueError as error:
            raise ValueError(f"{CORS_ORIGINS_ENV} contains invalid origin: {value}") from error
        invalid = (
            value == "*"
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != ""
            or parsed.query != ""
            or parsed.fragment != ""
        )
        if invalid:
            raise ValueError(f"{CORS_ORIGINS_ENV} contains invalid origin: {value}")
        if value not in origins:
            origins.append(value)
    return origins
