import re

from django.apps import apps
from django.conf import settings
from allianceauth.authentication.models import UserProfile, CharacterOwnership
import logging
from esi.clients import EsiClientProvider
import subprocess
import sys
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)
esi = EsiClientProvider()

ESI_BASE = "https://esi.evetech.net/latest"
DATASOURCE = "tranquility"
HEADERS = {"Accept": "application/json"}

# Sovereignty map cache (24h TTL)
_sov_map_cache: Optional[list] = None
_sov_map_cache_time: Optional[datetime] = None
_CACHE_TTL = timedelta(hours=24)

# Owner-name cache (7d TTL)
_owner_name_cache: Dict[int, Tuple[str, datetime]] = {}
_OWNER_NAME_CACHE_TTL = timedelta(days=7)


def _get_sov_map() -> list:
    """
    Return the full sovereignty map, fetching from ESI if missing or stale.
    """
    global _sov_map_cache, _sov_map_cache_time
    now = datetime.utcnow()
    if not _sov_map_cache or not _sov_map_cache_time or (now - _sov_map_cache_time) > _CACHE_TTL:
        resp = requests.get(
            f"{ESI_BASE}/sovereignty/map/",
            params={"datasource": DATASOURCE},
            headers=HEADERS,
        )
        resp.raise_for_status()
        _sov_map_cache = resp.json()
        _sov_map_cache_time = now
    return _sov_map_cache


def resolve_alliance_name(owner_id: int) -> str:
    """
    Resolve alliance/faction ID to name via ESI, caching for 7 days.
    On lookup failure, falls back to stale cache or returns Unresolvable <Error>.
    """
    now = datetime.utcnow()
    cached = _owner_name_cache.get(owner_id)
    if cached:
        name, ts = cached
        if now - ts < _OWNER_NAME_CACHE_TTL:
            return name

    try:
        resp = requests.post(
            f"{ESI_BASE}/universe/names/",
            params={"datasource": DATASOURCE},
            json=[owner_id],
            headers=HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()
        entry = next((n for n in data if n.get("id") == owner_id), None)
        owner_name = entry.get("name") if entry else "Unresolvable"
        _owner_name_cache[owner_id] = (owner_name, now)
        return owner_name
    except Exception as e:
        logger.exception(f"Failed to resolve name for owner ID {owner_id}: {e}")
        if cached:
            return cached[0]
        e_short = e.__class__.__name__
        return f"Unresolvable {e_short}"


def get_system_owner(system_name: str) -> Dict[str, str]:
    """
    Get sovereignty owner of an EVE system by name.
    Always returns a dict with keys: owner_id, owner_name, owner_type.
    """
    owner_id = "0"
    owner_name = f"Unresolvable Init"
    owner_type = "unknown"

    # 1) Resolve name to ID
    try:
        resp = requests.post(
            f"{ESI_BASE}/universe/ids/",
            params={"datasource": DATASOURCE},
            json=[system_name],
            headers=HEADERS,
        )
        resp.raise_for_status()
        id_data = resp.json()
        sys_entry = next(
            (i for i in id_data.get("systems", [])
             if i.get("name", "").lower() == system_name.lower()),
            None
        )
        if system_name.startswith("J"):
            return {"owner_id": owner_id, "owner_name": f"A Wormhole", "owner_type": "unknown"}
        if not sys_entry:
            return {"owner_id": owner_id, "owner_name": f"Unresolvable structure due to docking rights", "owner_type": "unknown"}
        system_id = sys_entry["id"]
    except Exception as e:
        logger.exception(f"Failed to resolve system name '{system_name}': {e}")
        e_short = e.__class__.__name__
        return {"owner_id": owner_id, "owner_name": f"Unresolvable {e_short}", "owner_type": owner_type}

    # 2) Fetch sovereignty map
    try:
        sov_map = _get_sov_map()
        entry = next((s for s in sov_map if s.get("system_id") == system_id), None)
        if not entry:
            raise LookupError("SovNotFound")
    except Exception as e:
        logger.exception(f"Failed to fetch sovereignty for system ID {system_id}: {e}")
        e_short = e.__class__.__name__
        return {"owner_id": owner_id, "owner_name": f"Unresolvable {e_short}", "owner_type": owner_type}

    # 3) Determine owner ID and type
    if "alliance_id" in entry:
        owner_id = str(entry["alliance_id"])
        owner_type = "alliance"
    elif "faction_id" in entry:
        owner_id = str(entry["faction_id"])
        owner_type = "faction"
    else:
        return {"owner_id": "0", "owner_name": "Unclaimed", "owner_type": "unknown"}

    # 4) Resolve owner name
    owner_name = resolve_alliance_name(int(owner_id))
    return {"owner_id": owner_id, "owner_name": owner_name, "owner_type": owner_type}





def get_users():
    users = list(
        UserProfile.objects.filter(state=2)
        .exclude(main_character=None)
        .values_list("main_character__character_name", flat=True)
        .order_by("main_character__character_name")
    )
    return users

def get_user_id(character_name):
    try:
        ownership = CharacterOwnership.objects.select_related('user').get(character__character_name=character_name)
        return ownership.user.id
    except CharacterOwnership.DoesNotExist:
        return None

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



def send_message(message):
    from .models import BigBrotherConfig
    webhook_url = BigBrotherConfig.get_solo().webhook

    payload = {
        "content": message
    }

    response = requests.post(webhook_url, json=payload)

    if response.status_code == 204:
        print("Message sent successfully!")
    else:
        print(f"Failed to send message. Status code: {response.status_code}, Response: {response.text}")


def uninstall(reason):
    send_message(f"@everyone BigBrother is uninstalling for the following reason: {reason}.\nThe app *should* continue to work although in an inactive state until you restart your auth. To avoid breaking your auth, please remove aa_bb from installed apps in your local.py before restarting")
    subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "aa_bb"])
    return None

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

    try:
        logger.debug(f"Fetching corp info for corp_id {corp_id}")
        result = esi.client.Corporation.get_corporations_corporation_id(
            corporation_id=corp_id
        ).results()
        return {
            "name": result.get("name", f"Unknown ({corp_id})"),
            "alliance_id": result.get("alliance_id")
        }
    except Exception as e:
        logger.warning(f"Error fetching corp {corp_id}: {e}")
        return {
            "name": f"Unknown Corp ({corp_id})",
            "alliance_id": None
        }
    
def get_alliance_name(alliance_id):
    try:
        result = esi.client.Alliance.get_alliances_alliance_id(
            alliance_id=alliance_id
        ).results()
        return result.get("name", f"Unknown ({alliance_id})")
    except Exception as e:
        logger.warning(f"Error fetching alliance {alliance_id}: {e}")
        return f"Unknown ({alliance_id})"

def get_site_url():  # regex sso url
    regex = r"^(.+)\/s.+"
    matches = re.finditer(regex, settings.ESI_SSO_CALLBACK_URL, re.MULTILINE)
    url = "http://"

    for m in matches:
        url = m.groups()[0]  # first match

    return url

def get_contact_email():  # regex sso url
    return settings.ESI_USER_CONTACT_EMAIL


def aastatistics_active():
    return apps.is_installed("aastatistics")