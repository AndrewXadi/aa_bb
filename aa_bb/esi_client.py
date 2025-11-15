"""Shared configuration for the Django-ESI OpenAPI 3.1 client."""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone
from email.utils import parsedate_to_datetime
from esi.openapi_clients import ESIClientProvider

from . import __title__, __version__

DEFAULT_COMPATIBILITY_DATE = "2025-07-23"
DEFAULT_OPERATIONS = [
    "PostUniverseNames",
    "PostUniverseIds",
    "GetCharactersCharacterIdCorporationhistory",
    "GetCorporationsCorporationId",
    "GetCorporationsCorporationIdAlliancehistory",
    "GetAlliancesAllianceId",
    "GetSovereigntyMap",
    "GetKillmailsKillmailIdKillmailHash",
]


def _setting(name: str, default):
    """Convenience helper to read optional Django settings with fallbacks."""
    return getattr(settings, name, default)


esi = ESIClientProvider(
    compatibility_date=_setting("BB_ESI_COMPATIBILITY_DATE", DEFAULT_COMPATIBILITY_DATE),
    ua_appname=_setting("BB_ESI_UA_APPNAME", __title__),
    ua_version=_setting("BB_ESI_UA_VERSION", __version__),
    ua_url=_setting("BB_ESI_UA_URL", None),
    operations=_setting("BB_ESI_OPERATIONS", DEFAULT_OPERATIONS),
)


def to_plain(value):
    """Recursively convert Pydantic models returned by the OpenAPI client to plain Python types."""
    if hasattr(value, "model_dump"):
        return to_plain(value.model_dump())
    if isinstance(value, list):
        return [to_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: to_plain(val) for key, val in value.items()}
    return value


def parse_expires(headers: dict | None):
    """Extract a timezone-aware datetime from HTTP Expires headers (if present)."""
    if not headers:
        return None
    value = headers.get("Expires")
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def call_result(operation, **kwargs):
    """Execute an OpenAPI operation.result() call and return (data, expires_at)."""
    data, response = operation.result(return_response=True, **kwargs)
    return to_plain(data), parse_expires(response.headers)


def call_results(operation, **kwargs):
    """Execute operation.results() and return (list_data, expires_at) with plain types."""
    data, response = operation.results(return_response=True, **kwargs)
    return to_plain(data), parse_expires(response.headers)
