from datetime import datetime

from django.core.cache import cache
from django.utils import timezone


def expiry_cache_key(kind: str, identifier) -> str:
    return f"aa_bb:esi_expiry:{kind}:{identifier}"


def get_cached_expiry(key: str) -> datetime | None:
    ts = cache.get(key)
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (TypeError, ValueError):
        cache.delete(key)
        return None


def set_cached_expiry(key: str, expires_at: datetime | None) -> None:
    if not expires_at:
        cache.delete(key)
        return
    now = timezone.now()
    timeout = max(1, int((expires_at - now).total_seconds()))
    cache.set(key, expires_at.timestamp(), timeout)
