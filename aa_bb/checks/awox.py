import requests
import time
import logging
from django.utils.html import format_html
from allianceauth.authentication.models import CharacterOwnership
from ..modelss import AwoxKillsCache
from django.utils import timezone
from ..app_settings import get_site_url, get_contact_email, get_owner_name, send_message
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

USER_AGENT = f"{get_site_url()} Maintainer: {get_owner_name()} {get_contact_email()}"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip",
    "Accept": "application/json",
}
ESI_URL = "https://esi.evetech.net/latest/killmails/{}/{}"

# Limit zKill "down" notifications to once every 2 hours
_last_zkill_down_notice_monotonic = 0.0


def _notify_zkill_down_once(preview: str, status: int | None, content_type: str | None):
    global _last_zkill_down_notice_monotonic
    now = time.monotonic()
    # 2 hours = 7200 seconds
    if now - _last_zkill_down_notice_monotonic < 3500:
        return
    _last_zkill_down_notice_monotonic = now
    msg = (
        "zKillboard appears unavailable and awox checks will not work (non-JSON response).\n"
        f"status={status} content_type='{content_type}'\n"
        f"body preview: ```{preview}```"
    )
    try:
        send_message(msg)
    except Exception as e:
        logger.warning(f"Failed to send zKill down notification: {e}")

def fetch_awox_kills(user_id, delay=0.2):
    # Indefinite DB cache: return cached kills if present
    try:
        cache = AwoxKillsCache.objects.get(pk=user_id)
        try:
            cache.last_accessed = timezone.now()
            cache.save(update_fields=["last_accessed"])
        except Exception:
            cache.save()
        return cache.data or []
    except AwoxKillsCache.DoesNotExist:
        pass
    characters = CharacterOwnership.objects.filter(user__id=user_id)
    char_ids = [c.character.character_id for c in characters]
    char_id_map = {c.character.character_id: c.character.character_name for c in characters}

    #logger.debug("Fetching AWOX kills for user {}: {}".format(user_id, char_id_map))

    kills_by_id = {}

    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers.update({"Connection": "close"})
    retries = Retry(total=3, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))

    for char_id in char_ids:
        zkill_url = f"https://zkillboard.com/api/characterID/{char_id}/awox/1/"
        try:
            response = session.get(zkill_url, timeout=(3, 10))
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.warning(f"Error fetching awox for {char_id}: {e}")
            continue

        # zKill may return HTML/Cloudflare challenge or a custom error page
        content_type = response.headers.get("Content-Type", "")
        text_preview = (response.text or "").strip()[:200]
        text_lower = (response.text or "").lower()
        if (
            not content_type.startswith("application/json")
            or "so a big oops happened" in text_lower
            or "cdn-cgi/challenge-platform" in text_lower
        ):
            logger.warning(
                "Non-JSON response from zKillboard for %s: status=%s content_type=%s body='%s'",
                char_id,
                response.status_code,
                content_type,
                text_preview,
            )
            _notify_zkill_down_once(text_preview, response.status_code, content_type)
            continue

        try:
            killmails = response.json()
        except ValueError as e:
            logger.warning(
                "Failed to decode zKillboard JSON for %s: %s. Body preview='%s'",
                char_id,
                e,
                text_preview,
            )
            _notify_zkill_down_once(text_preview, response.status_code, content_type)
            continue
        #logger.debug("Character {} has {} potential awox kills".format(char_id, len(killmails)))

        for kill in killmails:
            kill_id = kill.get("killmail_id")
            hash_ = kill.get("zkb", {}).get("hash")
            value = kill.get("zkb", {}).get("totalValue", 0)

            if not kill_id or not hash_:
                continue
            if kill_id in kills_by_id:
                continue

            #time.sleep(delay)
            try:
                esi_resp = requests.get(ESI_URL.format(kill_id, hash_), headers=HEADERS)
                if esi_resp.status_code != 200:
                    logger.warning("Failed to fetch ESI killmail {}: {}".format(kill_id, esi_resp.status_code))
                    continue

                full_kill = esi_resp.json()
                attackers = full_kill.get("attackers", [])
                victim_id = full_kill.get("victim", {}).get("character_id")

                attacker_names = set()
                for attacker in attackers:
                    a_id = attacker.get("character_id")
                    if a_id in char_ids and a_id != victim_id:
                        attacker_names.add(char_id_map.get(a_id))

                if not attacker_names:
                    continue

                kills_by_id[kill_id] = {
                    "value": int(value),
                    "link": f"https://zkillboard.com/kill/{kill_id}/",
                    "chars": attacker_names
                }

            except Exception as e:
                logger.error("Error processing killmail {}: {}".format(kill_id, e))

    data_list = list(kills_by_id.values()) if kills_by_id else []
    try:
        AwoxKillsCache.objects.update_or_create(
            user_id=user_id,
            defaults={"data": data_list, "last_accessed": timezone.now()},
        )
    except Exception:
        pass
    return data_list if data_list else None


def render_awox_kills_html(userID):
    kills = fetch_awox_kills(userID)
    if not kills:
        return None

    html = '<table class="table table-striped">'
    html += '<thead><tr><th>Character(s)</th><th>Value</th><th>Link</th></tr></thead><tbody>'

    for kill in kills:
        chars = ", ".join(sorted(kill.get("chars", [])))
        value = "{:,}".format(kill.get("value", 0))
        link = kill.get("link", "#")

        row_html = '<tr><td>{}</td><td>{} ISK</td><td><a href="{}" target="_blank">View</a></td></tr>'
        html += format_html(row_html, chars, value, link)

    html += '</tbody></table>'
    return html

def get_awox_kill_links(user_id):
    kills = fetch_awox_kills(user_id)
    if not kills:
        return []

    return [kill["link"] for kill in kills if "link" in kill]
