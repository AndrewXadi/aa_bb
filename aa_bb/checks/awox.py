import requests
import time
import logging
from django.utils.html import format_html
from allianceauth.authentication.models import CharacterOwnership
from ..app_settings import get_site_url, get_contact_email, get_owner_name

logger = logging.getLogger(__name__)

USER_AGENT = f"{get_site_url()} Maintainer: {get_owner_name()} {get_contact_email()}"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip"
}
ESI_URL = "https://esi.evetech.net/latest/killmails/{}/{}"

def fetch_awox_kills(user_id, delay=0.2):
    characters = CharacterOwnership.objects.filter(user__id=user_id)
    char_ids = [c.character.character_id for c in characters]
    char_id_map = {c.character.character_id: c.character.character_name for c in characters}

    logger.debug("Fetching AWOX kills for user {}: {}".format(user_id, char_id_map))

    kills_by_id = {}

    for char_id in char_ids:
        zkill_url = f"https://zkillboard.com/api/characterID/{char_id}/awox/1/"
        response = requests.get(zkill_url, headers=HEADERS)

        if response.status_code != 200:
            logger.warning("Failed to fetch kills for char {}: {}".format(char_id, response.status_code))
            continue

        killmails = response.json()
        logger.debug("Character {} has {} potential awox kills".format(char_id, len(killmails)))

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

    return list(kills_by_id.values()) if kills_by_id else None


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
