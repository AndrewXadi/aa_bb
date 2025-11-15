"""
Supplemental helpers for BigBrother: corp/alliance info caching, DLC toggles,
webhook utilities, and deployment helpers that were split out of app_settings.
"""

from django.apps import apps
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from .models import Alliance_names, BigBrotherConfig
from .modelss import CorporationInfoCache
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

def get_main_corp_id():
    """Best-effort lookup of the primary corp id using the first superuser alt."""
    from allianceauth.eveonline.models import EveCharacter
    try:
        char = EveCharacter.objects.filter(character_ownership__user__is_superuser=True).first()
        if char:  # Prefer the first superuser's main corp when available.
            return char.corporation_id
    except Exception:
        pass
    return 123456789888888  # Fallback

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

def get_corp_info(corp_id):
    """Cached corp info fetcher that falls back to ESI on misses."""
    if not (1_000_000 <= corp_id < 3_000_000_000):  # Valid range for known corp IDs
        logger.warning(f"Skipping invalid corp_id: {corp_id}")
        return {
            "name": f"Invalid Corp ({corp_id})",
            "alliance_id": None
        }

    expiry_key = expiry_cache_key("corp_info", corp_id)
    expiry_hint = get_cached_expiry(expiry_key)

    # Try DB cache first
    cached_entry = None
    try:
        entry = CorporationInfoCache.objects.get(pk=corp_id)
        now_ts = timezone.now()
        if expiry_hint and expiry_hint > now_ts:  # Cache still valid per redis hint.
            return {"name": entry.name, "alliance_id": getattr(entry, "alliance_id", None)}
        if expiry_hint is None and now_ts - entry.updated < TTL_SHORT:  # DB entry still fresh without redis hint.
            return {"name": entry.name, "alliance_id": getattr(entry, "alliance_id", None)}
        else:
            cached_entry = {
                "name": entry.name,
                "alliance_id": getattr(entry, "alliance_id", None),
                "member_count": entry.member_count,
            }
            entry.delete()
    except CorporationInfoCache.DoesNotExist:
        pass

    # Fetch from ESI
    member_count = 0
    try:
        #logger.debug(f"Fetching corp info for corp_id {corp_id}")
        operation = esi.client.Corporation.GetCorporationsCorporationId(
            corporation_id=corp_id
        )
        result, expires_at = call_result(operation)
        set_cached_expiry(expiry_key, expires_at)
        data = {
            "name": result.get("name", f"Unknown ({corp_id})"),
            "alliance_id": result.get("alliance_id"),
        }
        member_count = result.get("member_count", 0)
    except HTTPNotModified as exc:
        set_cached_expiry(expiry_key, parse_expires(getattr(exc, "headers", {})))
        if cached_entry:  # Serve stale cache when ESI returns 304 and cached data exists.
            data = {
                "name": cached_entry["name"],
                "alliance_id": cached_entry["alliance_id"],
            }
            member_count = cached_entry.get("member_count", 0)
        else:
            try:
                result, expires_at = call_result(operation, use_etag=False)
                set_cached_expiry(expiry_key, expires_at)
                data = {
                    "name": result.get("name", f"Unknown ({corp_id})"),
                    "alliance_id": result.get("alliance_id"),
                }
                member_count = result.get("member_count", 0)
            except Exception as e:
                logger.warning(f"Error fetching corp {corp_id} after 304: {e}")
                data = {"name": f"Unknown Corp ({corp_id})", "alliance_id": None}
                member_count = 0
    except (HTTPClientError, HTTPServerError) as e:
        logger.warning(f"ESI error fetching corp {corp_id}: {e}")
        data = {"name": f"Unknown Corp ({corp_id})", "alliance_id": None}
    except (RequestError, requests.exceptions.RequestException) as e:
        logger.warning(f"Network error fetching corp {corp_id}: {e}")
        data = {"name": f"Unknown Corp ({corp_id})", "alliance_id": None}

    # Store/update DB cache
    try:
        CorporationInfoCache.objects.update_or_create(
            corp_id=corp_id,
            defaults={"name": data["name"], "member_count": member_count},
        )
    except Exception:
        pass

    return data
    
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


def install_package_and_migrate(link: str) -> bool:
    """
    Install a package from `link`, run migrations, and report via webhook.

    The helper tries in-process `call_command` first and falls back to running
    the project’s manage.py with the same interpreter if needed.
    """
    from .app_settings import send_message, get_pings
    import sys
    import subprocess
    from pathlib import Path

    send_message(f"Starting package update from link: {link}")

    # 1) Install in the same environment as this process
    try:
        pip_cmd = [sys.executable, "-m", "pip", "install", link]
        res = subprocess.run(pip_cmd, capture_output=True, text=True)
        if res.returncode != 0:  # pip install failed; log tail and abort.
            tail = (res.stderr or res.stdout or "").splitlines()[-20:]
            logger.error("pip install failed: %s", res.stderr)
            send_message(f"#{get_pings('Error')} pip install failed:\n```{os.linesep.join(tail)}```")
            return False
    except Exception as e:
        logger.exception("pip install raised exception")
        send_message(f"#{get_pings('Error')} pip install raised an exception:\n```{e}```")
        return False

    # 2) Run migrations, prefer in-process call_command
    try:
        from django.core.management import call_command
        call_command("migrate", interactive=False, verbosity=1)
        send_message("Migrations completed successfully, make sure to restart AA.")
        return True
    except Exception as e:
        logger.warning("call_command('migrate') failed; trying manage.py fallback: %s", e)

    # 3) Fallback: locate manage.py and run it with the same interpreter
    try:
        from django.conf import settings
        base = Path(getattr(settings, "BASE_DIR", ".")).resolve()
        manage_path = None

        # Common locations relative to BASE_DIR
        for candidate in (base, base.parent, base.parent.parent):
            cand = candidate / "manage.py"
            if cand.exists():  # Use the first manage.py found near BASE_DIR.
                manage_path = cand
                break

        # Last resort: shallow search up one level
        if manage_path is None:  # Expand search one level up when common paths failed.
            for p in base.parent.glob("**/manage.py"):
                manage_path = p
                break

        if manage_path is None:  # Give up when manage.py cannot be located.
            raise FileNotFoundError("manage.py not found under project path(s)")

        mig = subprocess.run(
            [sys.executable, str(manage_path), "migrate", "--noinput"],
            capture_output=True,
            text=True,
        )
        if mig.returncode != 0:  # manage.py migrate failed; capture output and abort.
            tail = (mig.stderr or mig.stdout or "").splitlines()[-40:]
            logger.error("manage.py migrate failed: %s", mig.stderr)
            send_message(f"#{get_pings('Error')} manage.py migrate failed:\n```{os.linesep.join(tail)}```")
            return False

        send_message("Migrations completed successfully, make sure to restart AA.")
        return True

    except Exception as e2:
        logger.exception("Fallback manage.py migrate failed")
        send_message(f"#{get_pings('Error')} Could not run migrations:\n```{e2}```")
        return False



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
