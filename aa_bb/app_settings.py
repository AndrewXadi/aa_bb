from allianceauth.authentication.models import UserProfile, CharacterOwnership
import logging
from esi.clients import EsiClientProvider
import re
import subprocess
import sys
import requests
from datetime import datetime, timedelta
from django.utils import timezone
from typing import Optional, Dict, Tuple, Any, List
from django.db import transaction, IntegrityError, OperationalError
from .models import Alliance_names, Corporation_names, Character_names, BigBrotherConfig, id_types, EntityInfoCache, SovereigntyMapCache, AllianceHistoryCache, CorporationInfoCache
from .modelss import CharacterEmploymentCache
from dateutil.parser import parse as parse_datetime
import time
from bravado.exception import HTTPError
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from django.contrib.auth import get_user_model
from allianceauth.framework.api.user import get_main_character_name_from_user
from .app_settings_2 import *

logger = logging.getLogger(__name__)
esi = EsiClientProvider()

ESI_BASE = "https://esi.evetech.net/latest"
DATASOURCE = "tranquility"
HEADERS = {"Accept": "application/json"}



# Owner-name cache (7d TTL)
_owner_name_cache: Dict[int, Tuple[str, datetime]] = {}

def get_pings(message_type: str) -> str:
    """
    Given a MessageType instance, return a string of pings separated by spaces.
    """
    #logger.info(f"message type recieved - {message_type}")
    cfg = BigBrotherConfig.get_solo()
    pings = []

    if cfg.pingrole1_messages.all().filter(name=message_type).exists():
        pings.append(f"<@&{cfg.pingroleID}>")

    if cfg.pingrole2_messages.all().filter(name=message_type).exists():
        pings.append(f"<@&{cfg.pingroleID2}>")

    if cfg.here_messages.all().filter(name=message_type).exists():
        pings.append("@here")

    if cfg.everyone_messages.all().filter(name=message_type).exists():
        pings.append("@everyone")

    ping = " " + " ".join(pings) if pings else ""
    #logger.info(f"pingrole1 - {cfg.pingrole1_messages.all()}")
    #logger.info(f"pingrole2 - {cfg.pingrole2_messages.all()}")
    #logger.info(f"here - {cfg.here_messages.all()}")
    #logger.info(f"everyone - {cfg.everyone_messages.all()}")
    #logger.info(f"ping sent - {ping}")

    return ping

def _find_employment_at(employment: List[dict], date: datetime) -> Optional[dict]:
    for rec in employment:
        start = rec.get('start_date')
        end = rec.get('end_date')
        if start and start <= date and (end is None or date < end):
            return rec
    return None

def get_main_character_name(user_id):
    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
        return get_main_character_name_from_user(user)
    except User.DoesNotExist:
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
        record = id_types.objects.get(pk=eve_id)
        # mark last access time without touching freshness timestamp
        try:
            record.last_accessed = timezone.now()
            record.save(update_fields=["last_accessed"])
        except Exception:
            record.save()
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
            obj = id_types(id=eve_id, name=entity_type)
            obj.save()
    except IntegrityError:
        # another thread/process inserted it first; safe to ignore
        logging.debug(f"ID {eve_id} was cached by another process.")

    return entity_type

def is_npc_character(character_id: int) -> bool:
    return 3_000_000 <= character_id < 4_000_000

def get_character_id(name: str) -> int | None:
    """
    Resolve a character name to ID using ESI /universe/ids/ endpoint,
    with caching implemented through the Django model. Uses `esi.client` and
    self-heals duplicate name rows by reconciling via ESI.
    """
    # Step 1: Fast-path from DB when exactly one record exists
    try:
        record = Character_names.objects.get(name=name)
    except Character_names.MultipleObjectsReturned:
        record = None  # fall through to ESI reconciliation below
    except Character_names.DoesNotExist:
        record = None
    else:
        record.updated = timezone.now()
        record.save()
        return record.id

    # Step 2: Resolve via ESI and reconcile duplicates
    try:
        future = esi.client.Universe.post_universe_ids(
            names=[str(name)],
            datasource=DATASOURCE,
        )
        data = future.result()
    except HTTPError as e:
        logger.error(f"ESI error resolving character name '{name}': {e}")
        # Fallback to most recent local record if present
        fallback = (
            Character_names.objects
            .filter(name=name)
            .order_by("-updated")
            .first()
        )
        if fallback:
            fallback.updated = timezone.now()
            fallback.save()
            return fallback.id
        return None

    characters = (data or {}).get("characters", [])
    if not characters:
        return None

    char_id = int(characters[0]["id"])

    # Ensure canonical mapping exists
    with transaction.atomic():
        obj, created = Character_names.objects.get_or_create(
            id=char_id,
            defaults={"name": name}
        )
        if not created and obj.name != name:
            obj.name = name
            obj.updated = timezone.now()
            obj.save()

    # Proactively fix any duplicate rows left over with the same name but different IDs
    try:
        stale_qs = Character_names.objects.filter(name=name).exclude(id=char_id)
        if stale_qs.exists():
            try:
                # Resolve correct names for stale IDs using ESI
                stale_ids = [int(s.id) for s in stale_qs]
                name_future = esi.client.Universe.post_universe_names(
                    ids=stale_ids,
                    datasource=DATASOURCE,
                )
                name_rows = {int(r.get("id")): r.get("name") for r in name_future.result() or []}
            except HTTPError:
                name_rows = {}

            for stale in stale_qs:
                correct_name = name_rows.get(int(stale.id)) or stale.name
                if correct_name != stale.name:
                    stale.name = correct_name
                    stale.updated = timezone.now()
                    stale.save()
    except Exception as e:
        logger.debug(f"Duplicate cleanup failed for name='{name}': {e}")

    return char_id

_EXPIRY = timedelta(days=30)

def get_entity_info(entity_id: int, as_of: timezone.datetime) -> Dict:
    """
    Returns a dict:
      {
        'name': str,
        'type': 'character'|'corporation'|'alliance'|None,
        'corp_id': Optional[int],
        'corp_name': str,
        'alli_id': Optional[int],
        'alli_name': str,
      }
    Caches the result in the DB for 2 hours.
    """
    if entity_id == None:
        entity_id = 342545170
        errent = True
    else:
        errent = False
    now = timezone.now()

    # 1) Attempt to fetch fresh-enough cache entry
    try:
        cache = EntityInfoCache.objects.get(entity_id=entity_id, as_of=as_of)
        cache.updated = timezone.now()
        cache.save()
        if now - cache.updated < _EXPIRY:
            #logger.debug(f"cache hit: entity={entity_id} @ {as_of}")
            return cache.data
        else:
            #logger.debug(f"cache stale: entity={entity_id} @ {as_of}, expired {cache.updated}")
            cache.delete()
    except EntityInfoCache.DoesNotExist:
        pass
    #logger.debug(f"cache empty: entity={entity_id} @ {as_of}")

    # 2) Compute fresh info
    etype = get_eve_entity_type(entity_id)
    name = corp_name = alli_name = "-"
    corp_id = alli_id = None

    if etype == "character":
        name = resolve_character_name(entity_id)
        emp = get_character_employment(entity_id)
        rec = _find_employment_at(emp, as_of)
        if rec:
            corp_id   = rec["corporation_id"]
            corp_name = rec["corporation_name"]
            alli_id   = _find_alliance_at(rec.get("alliance_history", []), as_of)
            if alli_id:
                alli_name = resolve_alliance_name(alli_id)

    elif etype == "corporation":
        corp_id   = entity_id
        corp_name = resolve_corporation_name(entity_id)
        hist      = get_alliance_history_for_corp(entity_id)
        alli_id   = _find_alliance_at(hist, as_of)
        if alli_id:
            alli_name = resolve_alliance_name(alli_id)

    elif etype == "alliance":
        alli_id   = entity_id
        alli_name = resolve_alliance_name(entity_id)

    info = {
        "name":      name,
        "type":      etype,
        "corp_id":   corp_id,
        "corp_name": corp_name,
        "alli_id":   alli_id,
        "alli_name": alli_name,
    }

    # 3) Store in cache table (create or update)
    #    wrap in transaction to avoid race conditions
    MAX_RETRIES = 3
    for attempt in range(MAX_RETRIES):
        try:
            with transaction.atomic():
                EntityInfoCache.objects.update_or_create(
                    entity_id=entity_id,
                    as_of=as_of,
                    defaults={"data": info}
                )
            break  # Success, exit loop
        except OperationalError as e:
            if 'Deadlock' in str(e) and attempt < MAX_RETRIES - 1:
                time.sleep(0.1 * (attempt + 1))  # small backoff
                continue
            raise

    if errent:
        errmsg = "Error: entity id provided is None "
        info = {
            "name":      errmsg,
            "type":      etype,
            "corp_id":   corp_id,
            "corp_name": errmsg,
            "alli_id":   alli_id,
            "alli_name": errmsg,
        }

    return info

TTL_SHORT = timedelta(hours=4)

def _ser_dt(v):
    return v.isoformat() if isinstance(v, datetime) else v

def _deser_dt(v):
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            try:
                return parse_datetime(v)
            except Exception:
                return v
    return v

def _ser_employment(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({
            'corporation_id': r.get('corporation_id'),
            'corporation_name': r.get('corporation_name'),
            'start_date': _ser_dt(r.get('start_date')),
            'end_date': _ser_dt(r.get('end_date')),
            'alliance_history': [
                {'alliance_id': ah.get('alliance_id'), 'start_date': _ser_dt(ah.get('start_date'))}
                for ah in (r.get('alliance_history') or [])
            ],
        })
    return out

def _deser_employment(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows or []:
        out.append({
            'corporation_id': r.get('corporation_id'),
            'corporation_name': r.get('corporation_name'),
            'start_date': _deser_dt(r.get('start_date')),
            'end_date': _deser_dt(r.get('end_date')),
            'alliance_history': [
                {'alliance_id': ah.get('alliance_id'), 'start_date': _deser_dt(ah.get('start_date'))}
                for ah in (r.get('alliance_history') or [])
            ],
        })
    return out

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

    # 2. Cache: try DB (4h TTL)
    try:
        ce = CharacterEmploymentCache.objects.get(pk=char_id)
        if timezone.now() - ce.updated < TTL_SHORT:
            try:
                ce.last_accessed = timezone.now()
                ce.save(update_fields=['last_accessed'])
            except Exception:
                ce.save()
            return _deser_employment(ce.data)
    except CharacterEmploymentCache.DoesNotExist:
        pass

    # 3. Fetch the corp history from ESI
    try:
        response = esi.client.Character.get_characters_character_id_corporationhistory(
            character_id=char_id
        ).results()
    except Exception as e:
        logger.exception(f"ESI failure for character_id {char_id}: {e}")
        return []

    # 4. Order from earliest to latest
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

    # Save to cache
    try:
        CharacterEmploymentCache.objects.update_or_create(
            char_id=char_id,
            defaults={'data': _ser_employment(rows), 'last_accessed': timezone.now()},
        )
    except Exception:
        pass
    return rows

def get_user_characters(user_id: int) -> dict[int, str]:
    qs = CharacterOwnership.objects.filter(user__id=user_id).select_related('character')
    return {
        co.character.character_id: co.character.character_name
        for co in qs
    }

def format_int(value: int) -> str:
    """
    Format an integer SP value using dots as thousands separators.
    E.g. 65861521 → "65.861.521"
    """
    # Python’s built-in uses commas; swap them out for dots
    return f"{value:,}".replace(",", ".")

def is_npc_corporation(corp_id):
    return 1_000_000 <= corp_id < 2_000_000

CORP_TTL = timedelta(hours=4)

def get_corporation_info(corp_id):
    """
    Fetch corporation info from DB cache or ESI (24h TTL).
    """
    # 1) Try DB cache first
    try:
        entry = CorporationInfoCache.objects.get(pk=corp_id)
        if timezone.now() - entry.updated < CORP_TTL:
            return {"name": entry.name, "member_count": entry.member_count}
        # else: expired → delete to refresh
        entry.delete()
    except CorporationInfoCache.DoesNotExist:
        pass

    # 2) Fetch fresh from ESI
    try:
        result = esi.client.Corporation.get_corporations_corporation_id(
            corporation_id=corp_id
        ).results()
        info = {
            "name": result.get("name", f"Unknown ({corp_id})"),
            "member_count": result.get("member_count", 0),
        }
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        print(f"Failed to fetch corp info [{corp_id}]: {e}")
        info = {"name": f"Unknown Corp ({corp_id})", "member_count": 0}

    # 3) Store/update DB cache
    CorporationInfoCache.objects.update_or_create(
        corp_id=corp_id,
        defaults=info
    )

    return info


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

logger = logging.getLogger(__name__)

ALLIANCE_TTL = timedelta(hours=24)

def _parse_datetime(value):
    """Parse ISO8601 string to datetime, return None if invalid."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None

def _serialize_datetime(value):
    """Recursively convert datetime objects to ISO8601 strings."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_serialize_datetime(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize_datetime(v) for k, v in value.items()}
    return value

def get_alliance_history_for_corp(corp_id):
    # 1) Try DB cache first
    try:
        entry = AllianceHistoryCache.objects.get(pk=corp_id)
        if entry.is_fresh:
            return [
                {
                    "alliance_id": h.get("alliance_id"),
                    "start_date": _parse_datetime(h.get("start_date")),
                }
                for h in entry.history
            ]
        else:
            entry.delete()
    except AllianceHistoryCache.DoesNotExist:
        pass

    # 2) Fetch fresh directly
    history = []
    try:
        response = _fetch_alliance_history(corp_id)
        history = [
            {
                "alliance_id": h.get("alliance_id"),
                "start_date": _parse_datetime(h.get("start_date")),
            }
            for h in response
        ]
        history.sort(key=lambda x: x["start_date"] or datetime.min)
    except Exception as e:
        logger.info(f"Error fetching alliance history for corp {corp_id}: {e}")
        return []

    # 3) Store in DB (serialize datetimes as strings)
    serialized_history = _serialize_datetime(history)
    AllianceHistoryCache.objects.update_or_create(
        corp_id=corp_id,
        defaults={"history": serialized_history}
    )

    return history

def _get_sov_map() -> list:
    try:
        entry = SovereigntyMapCache.objects.get(pk=1)
        if entry.is_fresh:
            return entry.data
    except SovereigntyMapCache.DoesNotExist:
        pass

    resp = requests.get(
        f"{ESI_BASE}/sovereignty/map/",
        params={"datasource": DATASOURCE},
        headers=HEADERS,
    )
    resp.raise_for_status()
    data = resp.json()

    SovereigntyMapCache.objects.update_or_create(
        pk=1,
        defaults={"data": data}
    )

    return data

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
        record.updated = timezone.now()
        record.save()
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
        record.updated = timezone.now()
        record.save()
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
        record.updated = timezone.now()
        record.save()
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
    member_states = BigBrotherConfig.get_solo().bb_member_states.all()
    users = list(
        UserProfile.objects.filter(state__in=member_states)
        .exclude(main_character=None)
        .values_list("main_character__character_name", flat=True)
        .order_by("main_character__character_name")
    )
    return users

def get_user_profiles():
    member_states = BigBrotherConfig.get_solo().bb_member_states.all()
    users = (
        UserProfile.objects.filter(state__in=member_states)
        .exclude(main_character=None)
        .select_related("main_character", "user")  # optimization
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
