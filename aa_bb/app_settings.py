import re

from django.apps import apps
from django.conf import settings
from allianceauth.authentication.models import UserProfile, CharacterOwnership
import logging
from esi.clients import EsiClientProvider
import subprocess
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict


logger = logging.getLogger(__name__)
esi = EsiClientProvider()


ESI_BASE = "https://esi.evetech.net/latest"
DATASOURCE = "tranquility"
HEADERS = {"Accept": "application/json"}

# Module‑level cache variables
_sov_map_cache = None              # type: Optional[list]
_sov_map_cache_time = None         # type: Optional[datetime]
_CACHE_TTL = timedelta(hours=24)   # how long to keep the cache


def _get_sov_map() -> list:
    """
    Return the full sovereignty map, fetching from ESI only if the
    cached copy is missing or older than _CACHE_TTL.
    """
    global _sov_map_cache, _sov_map_cache_time

    #_sov_map_cache_time = None

    now = datetime.utcnow()
    if _sov_map_cache is None or _sov_map_cache_time is None \
       or (now - _sov_map_cache_time) > _CACHE_TTL:
        # Cache miss or stale → fetch anew
        resp = requests.get(
            f"{ESI_BASE}/sovereignty/map/",
            params={"datasource": DATASOURCE},
            headers=HEADERS,
        )
        resp.raise_for_status()
        _sov_map_cache = resp.json()
        _sov_map_cache_time = now

    return _sov_map_cache  # guaranteed to be a list


# Module-level cache for resolved owner names
_owner_name_cache = {}

def resolve_alliance_name(owner_id: int) -> str:
    """
    Resolve an alliance/faction ID to its name using ESI.
    Uses a module-level cache since names never change.
    """
    if owner_id in _owner_name_cache:
        return _owner_name_cache[owner_id]

    try:
        resp = requests.post(
            f"{ESI_BASE}/universe/names/",
            params={"datasource": DATASOURCE},
            json=[owner_id],
            headers=HEADERS,
        )
        resp.raise_for_status()
        name_data = resp.json()
        name_entry = next((n for n in name_data if n["id"] == owner_id), None)
        owner_name = name_entry["name"] if name_entry else "(Unknown)"
    except Exception as e:
        logger.exception(f"Failed to resolve name for owner ID {owner_id}: {e}")
        owner_name = "(Unknown)"

    _owner_name_cache[owner_id] = owner_name
    return owner_name


def get_system_owner(system_name: str) -> Optional[Dict[str, str]]:
    """
    Look up the sovereignty owner of the given EVE system (by name).
    Uses a 24‑hour cached copy of the full map for performance.

    Returns a dict:
      {
        "owner_id": "...",
        "owner_name": "...",
        "owner_type": "alliance", "faction", or "unknown"
      }
    or None if not found.
    """
    logger.debug(f"Looking up system owner for: {system_name}")

    # 1) Resolve name → ID
    try:
        resp = requests.post(
            f"{ESI_BASE}/universe/ids/",
            params={"datasource": DATASOURCE},
            json=[system_name],
            headers=HEADERS,
        )
        resp.raise_for_status()
        id_data = resp.json()
        logger.debug(f"Resolved name → ID response: {id_data}")
    except Exception as e:
        logger.exception(f"Failed to resolve system name '{system_name}': {e}")
        return None

    sys_entry = next(
        (i for i in id_data.get("systems", [])
         if i.get("name", "").lower() == system_name.lower()),
        None
    )

    if not sys_entry:
        logger.warning(f"No system ID found for system name: {system_name}")
        return None

    system_id = sys_entry["id"]
    logger.debug(f"System '{system_name}' resolved to ID {system_id}")

    # 2) Get the sovereignty map and find the system
    try:
        sov_map = _get_sov_map()
        entry = next((s for s in sov_map if s["system_id"] == system_id), None)
        logger.debug(f"Sovereignty entry for system {system_id}: {entry}")
    except Exception as e:
        logger.exception(f"Failed to fetch or parse sovereignty map: {e}")
        return None

    if not entry:
        logger.info(f"System ID {system_id} not found in sovereignty map.")
        return None

    # 3) Determine owner
    owner_id = None
    owner_type = None

    if "alliance_id" in entry:
        owner_id = entry["alliance_id"]
        owner_type = "alliance"
    elif "faction_id" in entry:
        owner_id = entry["faction_id"]
        owner_type = "faction"
    else:
        logger.info(f"System {system_id} has no alliance or faction ownership.")
        return {
            "owner_id": "",
            "owner_name": "Unclaimed",
            "owner_type": "unknown",
        }

    logger.debug(f"System {system_id} is owned by {owner_type} with ID {owner_id}")

    # 4) Resolve owner ID → name (cached)
    owner_name = resolve_alliance_name(owner_id)
    logger.info(f"System '{system_name}' is owned by {owner_type} '{owner_name}' (ID: {owner_id})")

    return {
        "owner_id": str(owner_id),
        "owner_name": owner_name,
        "owner_type": owner_type,
    }




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
        headers = {"User-Agent": "c"}

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
    subprocess.run(["pip", "uninstall", "-y", "aa_bb"])
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