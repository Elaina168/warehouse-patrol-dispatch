import os
from collections.abc import Mapping
from ipaddress import IPv6Address, ip_address
from urllib.parse import urlsplit

CORS_ORIGINS_ENV = "WAREHOUSE_PATROL_CORS_ORIGINS"
DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5174",
    "http://localhost:5174",
)


def _normalized_host(hostname: str, value: str) -> str:
    try:
        address = ip_address(hostname)
    except ValueError:
        if not hostname.isascii() or hostname.endswith("."):
            raise ValueError(f"{CORS_ORIGINS_ENV} contains invalid origin: {value}")
        normalized = hostname.lower()
        labels = normalized.split(".")
        final_label = labels[-1]
        if (
            not normalized
            or final_label.isdigit()
            or (
                final_label.startswith("0x")
                and len(final_label) > 2
                and all(character in "0123456789abcdef" for character in final_label[2:])
            )
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in label)
                for label in labels
            )
        ):
            raise ValueError(f"{CORS_ORIGINS_ENV} contains invalid origin: {value}")
        return normalized
    if isinstance(address, IPv6Address):
        return f"[{address.compressed}]"
    return address.compressed


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
        try:
            parsed = urlsplit(value)
        except ValueError as error:
            raise ValueError(f"{CORS_ORIGINS_ENV} contains invalid origin: {value}") from error
        try:
            port = parsed.port
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
        scheme = parsed.scheme.lower()
        normalized = f"{scheme}://{_normalized_host(parsed.hostname, value)}"
        if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
            normalized = f"{normalized}:{port}"
        if normalized not in origins:
            origins.append(normalized)
    return origins
