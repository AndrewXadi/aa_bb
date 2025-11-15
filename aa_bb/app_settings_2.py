"""
Supplemental helpers for BigBrother: corp/alliance info caching, DLC toggles,
webhook utilities, and deployment helpers that were split out of app_settings.
"""

from django.apps import apps
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from .models import Alliance_names, BigBrotherConfig
import re
import os
import time
from collections import deque
import subprocess
import sys
import requests
from httpx import RequestError
from esi.exceptions import HTTPClientError, HTTPServerError, HTTPNotModified
from .esi_client import esi, to_plain, call_result, parse_expires
from .esi_cache import expiry_cache_key, get_cached_expiry, set_cached_expiry


import logging
logger = logging.getLogger(__name__)
TTL_SHORT = timedelta(hours=4)

def get_owner_name():
    """Return the character name used to sign API requests / dashboards."""
    from allianceauth.eveonline.models import EveCharacter
    try:
        char = EveCharacter.objects.filter(character_ownership__user__is_superuser=True).first()
        if char:  # Prefer the first superuser's main pilot name.
            return char.character_name
    except Exception:
        pass
    return None  # Fallback

def get_alliance_name(alliance_id):
    """Resolve an alliance id to its name with DB/ESI caching."""
    if not alliance_id:  # Allow callers to pass None when corp not in alliance.
        return "None"
    # Try DB cache first with 4h TTL
    try:
        rec = Alliance_names.objects.get(pk=alliance_id)
    except Alliance_names.DoesNotExist:
        rec = None

    expiry_key = expiry_cache_key("alliance_name", alliance_id)
    expiry_hint = get_cached_expiry(expiry_key)
    if rec:  # Return cached names when TTL has not expired.
        now_ts = timezone.now()
        if expiry_hint and expiry_hint > now_ts:  # Redis TTL still valid.
            return rec.name
        if expiry_hint is None and now_ts - rec.updated < TTL_SHORT:  # DB TTL still valid.
            return rec.name

    cached_name = rec.name if rec else None
    operation = esi.client.Alliance.GetAlliancesAllianceId(
        alliance_id=alliance_id
    )
    try:
        result, expires_at = call_result(operation)
        set_cached_expiry(expiry_key, expires_at)
        name = result.get("name", f"Unknown ({alliance_id})")
    except HTTPNotModified as exc:
        set_cached_expiry(expiry_key, parse_expires(getattr(exc, "headers", {})))
        if cached_name:  # Use stale DB name when ESI returned 304.
            name = cached_name
        else:
            try:
                result, expires_at = call_result(operation, use_etag=False)
                set_cached_expiry(expiry_key, expires_at)
                name = result.get("name", f"Unknown ({alliance_id})")
            except Exception as e:
                logger.warning(f"Error fetching alliance {alliance_id} after 304: {e}")
                name = f"Unknown ({alliance_id})"
    except (HTTPClientError, HTTPServerError) as e:
        logger.warning(f"ESI error fetching alliance {alliance_id}: {e}")
        name = f"Unknown ({alliance_id})"
    except (RequestError, requests.exceptions.RequestException) as e:
        logger.warning(f"Network error fetching alliance {alliance_id}: {e}")
        name = f"Unknown ({alliance_id})"

    try:
        Alliance_names.objects.update_or_create(pk=alliance_id, defaults={"name": name})
    except Exception:
        pass

    return name

def get_site_url():  # regex sso url
    """Derive the site root from the configured SSO callback URL."""
    regex = r"^(.+)\/s.+"
    matches = re.finditer(regex, settings.ESI_SSO_CALLBACK_URL, re.MULTILINE)
    url = "http://"

    for m in matches:
        url = m.groups()[0]  # first match

    return url

def get_contact_email():  # regex sso url
    """Contact email published to CCP via ESI user agent metadata."""
    return settings.ESI_USER_CONTACT_EMAIL


def aablacklist_active():
    """Return True when the optional AllianceAuth blacklist app is installed."""
    return apps.is_installed("blacklist")


def afat_active():
    """Return True when the AFAT plugin is loaded in this deployment."""
    return apps.is_installed("afat")




_webhook_history = deque()  # stores timestamp floats of last webhook sends
_channel_history = deque()  # stores timestamp floats of last channel sends

def send_message(message: str, hook: str = None):
    """
    Sends `message` via Discord webhook, splitting long messages,
    honoring Retry-After on 429, AND proactively rate-limiting:
      - ≤5 req per 2s
      - ≤30 msgs per 60s
    """
    if hook:  # Allow callers to override the default webhook target.
        webhook_url = hook
    else:
        webhook_url = BigBrotherConfig.get_solo().webhook
    MAX_LEN     = 2000
    SPLIT_LEN   = 1900

    def _throttle():
        """Block until both webhook/channel rate limits allow another send."""
        now = time.monotonic()

        # -- webhook limit: max 5 per 2s --
        while len(_webhook_history) >= 5:
            earliest = _webhook_history[0]
            elapsed = now - earliest
            if elapsed >= 2.0:  # Drop timestamps once they fall outside 2s window.
                _webhook_history.popleft()
            else:
                time_to_wait = 2.0 - elapsed
                time.sleep(time_to_wait)
                now = time.monotonic()

        # -- channel limit: max 30 per 60s --
        while len(_channel_history) >= 30:
            earliest = _channel_history[0]
            elapsed = now - earliest
            if elapsed >= 60.0:  # Drop timestamps once they fall outside 60s window.
                _channel_history.popleft()
            else:
                time_to_wait = 60.0 - elapsed
                time.sleep(time_to_wait)
                now = time.monotonic()

        # record this send
        _webhook_history.append(now)
        _channel_history.append(now)

    def _post_with_retries(content: str):
        """Send a payload with retry/backoff logic for rate limits or hiccups."""
        payload = {"content": content}
        while True:
            _throttle()  # Ensure proactive rate limits are honored before sending.
            try:
                response = requests.post(webhook_url, json=payload)
                response.raise_for_status()
                return  # success
            except requests.exceptions.HTTPError:
                if response.status_code == 429:  # Discord rate-limited the request; honor Retry-After.
                    # obey Discord's Retry-After header
                    retry_after = response.headers.get("Retry-After")
                    try:
                        backoff = float(retry_after)
                    except (TypeError, ValueError):
                        backoff = 1.0
                    time.sleep(backoff)
                    continue  # retry
                else:
                    # other HTTP errors: log once and give up
                    logger.error(f"HTTP error sending: {response.status_code} {response.text}")
                    return
            except Exception as e:
                # network hiccup: wait briefly and retry
                logger.error(f"Error sending message: {e!r}, retrying in 2s")
                time.sleep(2.0)
                continue

    # if short enough, send directly
    if len(message) <= MAX_LEN:  # No need to chunk short messages.
        _post_with_retries(message)
        return

    # else split on newlines and chunk
    raw_lines = message.split("\n")
    parts = []
    for line in raw_lines:
        if len(line) <= MAX_LEN:  # Keep original line when it fits under Discord limit.
            parts.append(line)
        else:
            for i in range(0, len(line), SPLIT_LEN):
                chunk = line[i : i + SPLIT_LEN]
                prefix = "# split due to length\n" if i > 0 else ""
                parts.append(prefix + chunk)

    buffer = ""
    for part in parts:
        candidate = buffer + ("\n" if buffer else "") + part
        if len(candidate) > MAX_LEN:  # Current buffer would exceed Discord limit; flush first.
            _post_with_retries(buffer)
            buffer = part
        else:
            buffer = candidate

    if buffer:  # Flush the remaining text after chunking.
        _post_with_retries(buffer)
