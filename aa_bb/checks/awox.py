import requests
from django.utils.html import format_html
from allianceauth.authentication.models import CharacterOwnership
import logging
import time

logger = logging.getLogger(__name__)

USER_AGENT = "https://yourwebsite.com/ Maintainer: Your Name your@email.com"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip"
}

ESI_URL = "https://esi.evetech.net/latest/killmails/{}/{}"

def get_awox_kills(user_id, delay=0.2):
    characters = CharacterOwnership.objects.filter(user__id=user_id)
    char_ids = [c.character.character_id for c in characters]
    char_id_map = {c.character.character_id: c.character.character_name for c in characters}

    logger.debug("Fetching AWOX kills for user {}: {}".format(user_id, char_id_map))

    kills_by_id = {}

    for char_id in char_ids:
        zkill_url = "https://zkillboard.com/api/characterID/{}/awox/1/".format(char_id)
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

            time.sleep(delay)
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
                    "link": "https://zkillboard.com/kill/{}/".format(kill_id),
                    "chars": attacker_names
                }

            except Exception as e:
                logger.error("Error processing killmail {}: {}".format(kill_id, e))

    if not kills_by_id:
        return "No awox kills found."

    html = '<table class="table table-striped">'
    html += '<thead><tr><th>Character(s)</th><th>Value</th><th>Link</th></tr></thead><tbody>'
    for kill in kills_by_id.values():
        chars = kill.get("chars", [])
        value = kill.get("value", 0)
        link = kill.get("link", "#")

        char_list = ", ".join(sorted(chars))
        value_str = "{:,}".format(value)

        row_html = '<tr><td>{}</td><td>{} ISK</td><td><a href="{}" target="_blank">View</a></td></tr>'
        html += format_html(row_html, char_list, value_str, link)
    html += '</tbody></table>'

    return html
