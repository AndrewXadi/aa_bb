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
from django.utils import timezone
from typing import Optional, Dict, Tuple, Any, List
from django.db import transaction, IntegrityError
from .models import Alliance_names, Corporation_names, Character_names, BigBrotherConfig, id_types
from dateutil.parser import parse as parse_datetime
import time
from bravado.exception import HTTPError
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout


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

def _find_employment_at(employment: List[dict], date: datetime) -> Optional[dict]:
    for rec in employment:
        start = rec.get('start_date')
        end = rec.get('end_date')
        if start and start <= date and (end is None or date < end):
            return rec
    return None


def _find_alliance_at(history: List[dict], date: datetime) -> Optional[int]:
    for i, rec in enumerate(history):
        start = rec.get('start_date')
        next_start = history[i+1]['start_date'] if i+1 < len(history) else None
        if start and start <= date and (next_start is None or date < next_start):
            return rec.get('alliance_id')
    return None

def get_eve_entity_type_int(eve_id: int, datasource: str | None = None) -> str | None:
    """
    Resolve an EVE Online ID to its entity type.

    Returns:
        'character', 'corporation', 'alliance', etc., or None on error/not found.
    """
    if eve_id is None:
        logging.warning("No EVE ID provided to get_eve_entity_type_int")
        return None
    try:
        future = esi.client.Universe.post_universe_names(
            ids=[eve_id],                # must be `ids`
            datasource=datasource or "tranquility"
        )
        results = future.result()        # <-- use .result(), not .results()
    except HTTPError as e:
        logging.warning(f"ESI error resolving {eve_id}: {e}")
        return None

    if not results:
        return None
    return results[0].get("category")

def get_eve_entity_type(
    eve_id: int,
    datasource: str | None = None
) -> Optional[str]:
    """
    Resolve an EVE Online ID to its entity type, caching results in the `id_types` table.

    Workflow:
      1. Try to get a cached record via id_types.objects.get(pk=eve_id).
      2. If found, return record.name.
      3. On DoesNotExist, call get_eve_entity_type() to fetch from ESI.
      4. If ESI returns a non-null type, save a new id_types record.
      5. Return the resolved type (or None if unresolved).
    """
    # 1. Cache lookup
    try:
        record = id_types.objects.get(pk=eve_id)  # raises id_types.DoesNotExist if not found :contentReference[oaicite:0]{index=0}
        return record.name
    except id_types.DoesNotExist:
        pass

    # 2. Cache miss — resolve via ESI
    entity_type = get_eve_entity_type_int(eve_id, datasource=datasource)
    if entity_type is None:
        return None

    # 3. Store in cache
    try:
        with transaction.atomic():
            id_types.objects.create(
                id=eve_id,
                name=entity_type
            )  # convenience create method: new instance + save :contentReference[oaicite:1]{index=1}
    except IntegrityError:
        # another thread/process inserted it first; safe to ignore
        logging.debug(f"ID {eve_id} was cached by another process.")

    return entity_type

def is_npc_character(character_id: int) -> bool:
    return 3_000_000 <= character_id < 4_000_000

def get_character_id(name: str) -> int | None:
    """
    Resolve a character name to ID using ESI /universe/ids/ endpoint,
    with caching implemented through the Django model.
    """
    # Step 1: Check if the character's ID exists in the database
    try:
        record = Character_names.objects.get(name=name)
        return record.id  # Return the stored ID from the database if found
    except Character_names.DoesNotExist:
        pass  # Continue to ESI if not found in the database

    # Step 2: Fetch from ESI if the name is not in the database
    url = "https://esi.evetech.net/latest/universe/ids/?datasource=tranquility"
    headers = {"Accept": "application/json"}
    payload = [str(name)]  # List of names to be passed to the API

    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()  # Check if the response is successful
        data = response.json()
        
        characters = data.get("characters", [])
        if characters:
            char_id = characters[0]["id"]
            
            # Save the new ID to the database for future use
            with transaction.atomic():
                Character_names.objects.update_or_create(
                    name=name,
                    defaults={"id": char_id}
                )

            return char_id
        
        return None  # If no characters found in the response

    except requests.RequestException as e:
        logger.error(f"Character lookup failed for '{name}': {e}")
        return None

# A simple time-based LRU
_CACHE: Dict[int, Dict] = {}
_EXPIRY = timedelta(hours=24)

def get_entity_info(entity_id: int, as_of: timezone) -> Dict:
    """
    Returns a dict:
      {
        'name': str,
        'type': 'character'|'corporation'|'alliance'|None,
        'corp_id': Optional[int],
        'corp_name': str,
        'alli_id': Optional[int],
        'alli_name': str,
        'timestamp': datetime  # for eviction
      }
    Caches the result for 24h.
    """
    now = timezone.now()
    entry = _CACHE.get(entity_id)
    if entry and now - entry['timestamp'] < _EXPIRY:
        return entry

    # Otherwise compute fresh:
    etype = get_eve_entity_type(entity_id)
    name, corp_id, corp_name, alli_id, alli_name = '-', None, '-', None, '-'

    if etype == 'character':
        name = resolve_character_name(entity_id)
        emp  = get_character_employment(entity_id)
        rec  = _find_employment_at(emp, as_of)
        if rec:
            corp_id   = rec['corporation_id']
            corp_name = rec['corporation_name']
            alli_id   = _find_alliance_at(rec['alliance_history'], as_of)
            if alli_id:
                alli_name = resolve_alliance_name(alli_id)
    elif etype == 'corporation':
        corp_id   = entity_id
        corp_name = resolve_corporation_name(entity_id)
        hist      = get_alliance_history_for_corp(entity_id)
        alli_id   = _find_alliance_at(hist, as_of)
        if alli_id:
            alli_name = resolve_alliance_name(alli_id)
    elif etype == 'alliance':
        alli_id   = entity_id
        alli_name = resolve_alliance_name(entity_id)

    info = {
        'name': name,
        'type': etype,
        'corp_id': corp_id,
        'corp_name': corp_name,
        'alli_id': alli_id,
        'alli_name': alli_name,
        'timestamp': now,
    }
    _CACHE[entity_id] = info
    return info

def get_character_employment(character_or_id) -> list[dict]:
    """
    Fetch and format the permanent employment history for a character.
    Accepts either:
      - an int: the EVE character_id
      - an object with .character_id attribute
    Returns a list of dicts:
      {
        'corporation_id': int,
        'corporation_name': str,
        'start_date': datetime,
        'end_date': datetime|None,
        'alliance_history': [ {'alliance_id': int, 'start_date': datetime}, ... ]
      }
    On ESI failure, logs and returns [].
    """
    # 1. Normalize to integer character_id
    if isinstance(character_or_id, int):
        char_id = character_or_id
    else:
        try:
            char_id = int(character_or_id.character_id)
        except (AttributeError, TypeError, ValueError):
            raise ValueError(
                "get_character_employment() requires an int or an object with .character_id"
            )

    # 2. Fetch the corp history from ESI
    try:
        response = esi.client.Character.get_characters_character_id_corporationhistory(
            character_id=char_id
        ).results()
    except Exception as e:
        logger.exception(f"ESI failure for character_id {char_id}: {e}")
        return []

    # 3. Order from earliest to latest
    history = list(reversed(response))
    rows = []

    for idx, membership in enumerate(history):
        corp_id = membership.get('corporation_id')
        if not corp_id or is_npc_corporation(corp_id):
            continue

        start = ensure_datetime(membership.get('start_date'))
        # Next start_date becomes this membership's end_date
        end = None
        if idx + 1 < len(history):
            end = ensure_datetime(history[idx + 1].get('start_date'))

        # Enrich with corp and alliance info
        corp_info     = get_corporation_info(corp_id)
        alliance_hist = get_alliance_history_for_corp(corp_id)

        rows.append({
            'corporation_id':   corp_id,
            'corporation_name': corp_info.get('name'),
            'start_date':       start,
            'end_date':         end,
            'alliance_history': alliance_hist,
        })

        # Persist the corporation name for future lookups
        with transaction.atomic():
            Corporation_names.objects.update_or_create(
                pk=corp_id,
                defaults={'name': corp_info.get('name', f"Unknown ({corp_id})")}
            )

    return rows

def get_user_characters(user_id: int) -> dict[int, str]:
    qs = CharacterOwnership.objects.filter(user__id=user_id).select_related('character')
    return {
        co.character.character_id: co.character.character_name
        for co in qs
    }

def is_npc_corporation(corp_id):
    return 1_000_000 <= corp_id < 2_000_000

corp_cache = {}
CORP_TTL = timedelta(hours=24)

def get_corporation_info(corp_id):
    """
    Fetch corporation info from the ESI API, with a manual 24 h TTL cache.
    """
    # 1) Cache lookup & expiration check (LBYL)
    entry = corp_cache.get(corp_id)
    if entry:
        if datetime.utcnow() - entry["stored"] < CORP_TTL:
            return entry["value"]
        # expired → remove old entry
        del corp_cache[corp_id]

    # 2) Cache miss or expired → fetch fresh data
    try:
        result = esi.client.Corporation.get_corporations_corporation_id(
            corporation_id=corp_id
        ).results()
        # carry through name & member_count
        info = {
            "name":         result.get("name", f"Unknown ({corp_id})"),
            "member_count": result.get("member_count", 0),
        }
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        # log & return a safe default
        print(f"Failed to fetch corp info [{corp_id}]: {e}")
        info = {"name": f"Unknown Corp ({corp_id})"}

    # 3) Store in cache
    corp_cache[corp_id] = {
        "value": info,
        "stored": datetime.utcnow()
    }

    return info

def_cache = {}

def ensure_datetime(value):
    if isinstance(value, str):
        return parse_datetime(value)
    return value

def _fetch_alliance_history(corp_id):
    try:
        return esi.client.Corporation.get_corporations_corporation_id_alliancehistory(
            corporation_id=corp_id
        ).results()
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        logger.warning(f"Failed to fetch alliance history for corp {corp_id}: {e}")
        return []

def get_alliance_history_for_corp(corp_id):
    if corp_id in def_cache:
        return def_cache[corp_id]

    history = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_fetch_alliance_history, corp_id)
        try:
            response = future.result(timeout=5)   # give it 5s max
            history = [
                {
                    "alliance_id": h.get("alliance_id"),
                    "start_date": ensure_datetime(h.get("start_date")),
                }
                for h in response
            ]
            history.sort(key=lambda x: x["start_date"])
        except FuturesTimeout:
            logger.info(f"Timeout fetching alliance history for corp {corp_id}")
            return []
        except Exception as e:
            logger.info(f"Error fetching alliance history for corp {corp_id}: {e}")

    def_cache[corp_id] = history
    return history

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

ESI_BASE    = "https://esi.evetech.net/latest"
DATASOURCE  = "tranquility"
HEADERS     = {"Accept": "application/json"}

def resolve_alliance_name(owner_id: int) -> str:
    """
    Resolve alliance/faction ID to name via ESI, storing permanently in aa_bb_alliances.
    On lookup failure, falls back to stale DB record or returns 'Unresolvable <Error>'.
    """
    # 1. Try permanent table first
    try:
        record = Alliance_names.objects.get(pk=owner_id)
        return record.name
    except Alliance_names.DoesNotExist:
        pass  # need to fetch and store

    # 2. Fetch from ESI
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

        # 3. Save or update the DB record
        with transaction.atomic():
            Alliance_names.objects.update_or_create(
                pk=owner_id,
                defaults={"name": owner_name}
            )

        return owner_name

    except Exception as e:
        # 4. On error, log and fallback to stale if any
        logger.exception(f"Failed to resolve name for owner ID {owner_id}: {e}")
        try:
            stale = Alliance_names.objects.get(pk=owner_id)
            return stale.name
        except Alliance_names.DoesNotExist:
            pass

        e_short  = e.__class__.__name__
        e_detail = getattr(e, 'code', None) or getattr(e, 'status', None) or str(e)
        return f"Unresolvable eve map{e_short}{e_detail}"

def resolve_corporation_name(corp_id: int) -> str:
    """
    Resolve corporation ID to name via ESI, storing permanently in aa_bb_corporations.
    On lookup failure, falls back to stale DB record or returns 'Unresolvable <Error>'.
    """
    # 1. Try permanent table first
    try:
        record = Corporation_names.objects.get(pk=corp_id)
        return record.name
    except Corporation_names.DoesNotExist:
        pass  # need to fetch and store

    # 2. Fetch from ESI
    try:
        resp = requests.post(
            f"{ESI_BASE}/universe/names/",
            params={"datasource": DATASOURCE},
            json=[corp_id],
            headers=HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()
        entry = next((n for n in data if n.get("id") == corp_id), None)
        corp_name = entry.get("name") if entry else "Unresolvable"

        # 3. Save or update the DB record
        with transaction.atomic():
            Corporation_names.objects.update_or_create(
                pk=corp_id,
                defaults={"name": corp_name}
            )

        return corp_name

    except Exception as e:
        # 4. On error, log and fallback to stale if any
        logger.exception(f"Failed to resolve name for corporation ID {corp_id}: {e}")
        try:
            stale = Corporation_names.objects.get(pk=corp_id)
            return stale.name
        except Corporation_names.DoesNotExist:
            pass

        e_short  = e.__class__.__name__
        e_detail = getattr(e, 'code', None) or getattr(e, 'status', None) or str(e)
        return f"Unresolvable eve map{e_short}{e_detail}"
    
def resolve_character_name(char_id: int) -> str:
    """
    Resolve character ID to name via ESI, storing permanently in Character_names.
    On lookup failure, falls back to stale DB record or returns 'Unresolvable <Error>'.
    """
    # 1. Try permanent table first
    try:
        record = Character_names.objects.get(pk=char_id)
        return record.name
    except Character_names.DoesNotExist:
        pass  # need to fetch and store

    # 2. Fetch from ESI
    try:
        resp = requests.post(
            f"{ESI_BASE}/universe/names/",
            params={"datasource": DATASOURCE},
            json=[char_id],
            headers=HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()
        entry = next((n for n in data if n.get("id") == char_id), None)
        char_name = entry.get("name") if entry else "Unresolvable"

        # 3. Save or update the DB record
        with transaction.atomic():
            Character_names.objects.update_or_create(
                pk=char_id,
                defaults={"name": char_name}
            )

        return char_name

    except Exception as e:
        # 4. On error, log and fallback to stale if any
        logger.exception(f"Failed to resolve name for character ID {char_id}: {e}")
        try:
            stale = Character_names.objects.get(pk=char_id)
            return stale.name
        except Character_names.DoesNotExist:
            pass

        e_short = e.__class__.__name__
        e_detail = getattr(e, 'code', None) or getattr(e, 'status', None) or str(e)
        return f"Unresolvable eve map{e_short}{e_detail}"


def get_system_owner(system: str) -> Dict[str, str]:
    """
    Get sovereignty owner of an EVE system by name.
    Always returns a dict with keys: owner_id, owner_name, owner_type.
    """
    owner_id = "0"
    owner_name = f"Unresolvable Init"
    owner_type = "unknown"

    # 1) Pull name and ID from the passed-in dict
    system_id = system.get("id")
    system_nam = system.get("name")
    system_name = str()
    if system_nam:
        system_name = str(system_nam)

    # 2) Fetch sovereignty map
    try:
        sov_map = _get_sov_map()
        entry = next((s for s in sov_map if s.get("system_id") == system_id), None)
        if not entry:
            return {"owner_id": owner_id, "owner_name": f"Unresolvable structure due to lack of docking rights", "owner_type": owner_type}
    except Exception as e:
        logger.exception(f"Failed to fetch sovereignty for system ID {system_id}: {e}")
        e_short = e.__class__.__name__
        e_detail = getattr(e, 'code', None) or getattr(e, 'status', None) or str(e)
        return {"owner_id": owner_id, "owner_name": f"Unresolvable sov, {e_short}{e_detail}", "owner_type": owner_type}

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


_webhook_history = deque()  # stores timestamp floats of last webhook sends
_channel_history = deque()  # stores timestamp floats of last channel sends

def send_message(message: str):
    """
    Sends `message` via Discord webhook, splitting long messages,
    honoring Retry-After on 429, AND proactively rate-limiting:
      - ≤5 req per 2s
      - ≤30 msgs per 60s
    """
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
            except HTTPError:
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
    if not alliance_id:
        return "None"
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


def aablacklist_active():
    return apps.is_installed("blacklist")