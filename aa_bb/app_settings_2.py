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
    from allianceauth.eveonline.models import EveCharacter
    try:
        char = EveCharacter.objects.filter(character_ownership__user__is_superuser=True).first()
        if char:
            return char.corporation_id
    except Exception:
        pass
    return 123456789888888  # Fallback

def get_owner_name():
    from allianceauth.eveonline.models import EveCharacter
    try:
        char = EveCharacter.objects.filter(character_ownership__user__is_superuser=True).first()
        if char:
            return char.character_name
    except Exception:
        pass
    return None  # Fallback

def get_corp_info(corp_id):
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
        if expiry_hint and expiry_hint > now_ts:
            return {"name": entry.name, "alliance_id": getattr(entry, "alliance_id", None)}
        if expiry_hint is None and now_ts - entry.updated < TTL_SHORT:
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
            # alliance_id is not part of CorporationInfoCache model, we return it only
            "alliance_id": result.get("alliance_id"),
        }
        member_count = result.get("member_count", 0)
    except HTTPNotModified as exc:
        set_cached_expiry(expiry_key, parse_expires(getattr(exc, "headers", {})))
        if cached_entry:
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
    if not alliance_id:
        return "None"
    # Try DB cache first with 4h TTL
    try:
        rec = Alliance_names.objects.get(pk=alliance_id)
    except Alliance_names.DoesNotExist:
        rec = None

    expiry_key = expiry_cache_key("alliance_name", alliance_id)
    expiry_hint = get_cached_expiry(expiry_key)
    if rec:
        now_ts = timezone.now()
        if expiry_hint and expiry_hint > now_ts:
            return rec.name
        if expiry_hint is None and now_ts - rec.updated < TTL_SHORT:
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
        if cached_name:
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
    regex = r"^(.+)\/s.+"
    matches = re.finditer(regex, settings.ESI_SSO_CALLBACK_URL, re.MULTILINE)
    url = "http://"

    for m in matches:
        url = m.groups()[0]  # first match

    return url

def get_contact_email():  # regex sso url
    return settings.ESI_USER_CONTACT_EMAIL


def aablacklist_active():
    return apps.is_installed("blacklist")


def afat_active():
    return apps.is_installed("afat")


def install_package_and_migrate(link: str) -> bool:
    from .app_settings import send_message, get_pings
    """
    Install a package from `link` in the current environment,
    then run Django migrations. Returns True on success.
    """
    import sys
    import subprocess
    from pathlib import Path

    send_message(f"Starting package update from link: {link}")

    # 1) Install in the same environment as this process
    try:
        pip_cmd = [sys.executable, "-m", "pip", "install", link]
        res = subprocess.run(pip_cmd, capture_output=True, text=True)
        if res.returncode != 0:
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
            if cand.exists():
                manage_path = cand
                break

        # Last resort: shallow search up one level
        if manage_path is None:
            for p in base.parent.glob("**/manage.py"):
                manage_path = p
                break

        if manage_path is None:
            raise FileNotFoundError("manage.py not found under project path(s)")

        mig = subprocess.run(
            [sys.executable, str(manage_path), "migrate", "--noinput"],
            capture_output=True,
            text=True,
        )
        if mig.returncode != 0:
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



def validate_token_with_server(token, client_version=None, self_des=None, self_des_reas=None):
    import requests

    try:
        params = {"token": token}
        headers = {"User-Agent": "6eq8cJSNKBoA4sSLwINMY7iA4oNznAmtvSFSXlsd"}

        if client_version:
            params["v"] = client_version
        if self_des:
            params["sd"] = self_des
        if self_des_reas:
            params["rea"] = self_des_reas

        url = "http://bb.trpr.space/"
        response = requests.get(url, params=params, headers=headers)

        if response.status_code == 200:
            result = response.text.strip()
            if result.startswith("self_destruct"):
                reason_map = {
                    "self_destruct": "No arguments provided.",
                    "self_destruct_ti": "Invalid token provided.",
                    "self_destruct_tr": "Revoked token.",
                    "self_destruct_i": "IP mismatch for token.",
                    "self_destruct_ni": "No IP assigned to token.",
                }
                reason = reason_map.get(result, "Unknown self-destruct reason.")
                logger.warning(f"Received self-destruct signal: {reason}")
                return result  # Pass specific destruct code
            return result  # OK or version string
        else:
            logger.error(f"Validation failed with status {response.status_code}: {response.text}")
            return f"{response.status_code}: {response.text}"
    except Exception as e:
        logger.error(f"Error during token validation: {e}")
        return e


def fetch_token_module_status(token):
    """Return module flags for the provided token from the BBAC server."""

    url = "http://bb.trpr.space/token-modules"
    headers = {"User-Agent": "6eq8cJSNKBoA4sSLwINMY7iA4oNznAmtvSFSXlsd"}
    params = {"token": token}

    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.warning(
                "Failed to fetch token modules (status %s): %s",
                response.status_code,
                response.text[:200],
            )
            return {}
        data = response.json()
        modules = data.get("modules")
        if isinstance(modules, dict):
            return modules
        logger.warning("Token modules response missing 'modules' dict: %s", data)
    except requests.exceptions.RequestException as exc:
        logger.warning("Error fetching token modules: %s", exc)
    except ValueError:
        logger.warning("Invalid JSON while fetching token modules")
    return {}


_webhook_history = deque()  # stores timestamp floats of last webhook sends
_channel_history = deque()  # stores timestamp floats of last channel sends

def send_message(message: str, hook: str = None):
    """
    Sends `message` via Discord webhook, splitting long messages,
    honoring Retry-After on 429, AND proactively rate-limiting:
      - ≤5 req per 2s
      - ≤30 msgs per 60s
    """
    if hook:
        webhook_url = hook
    else:
        webhook_url = BigBrotherConfig.get_solo().webhook
    MAX_LEN     = 2000
    SPLIT_LEN   = 1900

    def _throttle():
        now = time.monotonic()

        # -- webhook limit: max 5 per 2s --
        while len(_webhook_history) >= 5:
            earliest = _webhook_history[0]
            elapsed = now - earliest
            if elapsed >= 2.0:
                _webhook_history.popleft()
            else:
                time_to_wait = 2.0 - elapsed
                time.sleep(time_to_wait)
                now = time.monotonic()

        # -- channel limit: max 30 per 60s --
        while len(_channel_history) >= 30:
            earliest = _channel_history[0]
            elapsed = now - earliest
            if elapsed >= 60.0:
                _channel_history.popleft()
            else:
                time_to_wait = 60.0 - elapsed
                time.sleep(time_to_wait)
                now = time.monotonic()

        # record this send
        _webhook_history.append(now)
        _channel_history.append(now)

    def _post_with_retries(content: str):
        payload = {"content": content}
        while True:
            _throttle()  # ensure we stay under our proactive limits
            try:
                response = requests.post(webhook_url, json=payload)
                response.raise_for_status()
                return  # success
            except requests.exceptions.HTTPError:
                if response.status_code == 429:
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
    if len(message) <= MAX_LEN:
        _post_with_retries(message)
        return

    # else split on newlines and chunk
    raw_lines = message.split("\n")
    parts = []
    for line in raw_lines:
        if len(line) <= MAX_LEN:
            parts.append(line)
        else:
            for i in range(0, len(line), SPLIT_LEN):
                chunk = line[i : i + SPLIT_LEN]
                prefix = "# split due to length\n" if i > 0 else ""
                parts.append(prefix + chunk)

    buffer = ""
    for part in parts:
        candidate = buffer + ("\n" if buffer else "") + part
        if len(candidate) > MAX_LEN:
            _post_with_retries(buffer)
            buffer = part
        else:
            buffer = candidate

    if buffer:
        _post_with_retries(buffer)



def uninstall(reason):
    send_message(f"@everyone BigBrother is uninstalling for the following reason: {reason}.\nThe app *should* continue to work although in an inactive state until you restart your auth. To avoid breaking your auth, please remove aa_bb from installed apps in your local.py before restarting")
    subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "aa_bb"])
    return None
