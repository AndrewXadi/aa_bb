import logging
from django.utils.html import format_html
from django.utils.timezone import now
from datetime import timedelta
from esi.clients import EsiClientProvider
from allianceauth.authentication.models import CharacterOwnership
from dateutil.parser import parse as parse_datetime

logger = logging.getLogger(__name__)
esi = EsiClientProvider()

def is_npc_corporation(corp_id):
    return 1_000_000 <= corp_id < 2_000_000

def ensure_datetime(value):
    if isinstance(value, str):
        return parse_datetime(value)
    return value

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

def get_frequent_corp_changes(user_id):
    characters = CharacterOwnership.objects.filter(user__id=user_id)
    html = ""

    for char in characters:
        char_name = str(char.character)
        logger.info(f"Checking character: {char.character.character_id} ({char_name})")

        try:
            response = esi.client.Character.get_characters_character_id_corporationhistory(
                character_id=char.character.character_id
            ).results()
            logger.info(f"  Got {len(response)} corp history entries")
        except Exception as e:
            logger.error(f"  Skipping character due to error: {e}")
            continue

        history = list(reversed(response))
        rows = []

        for i in range(len(history)):
            corp = history[i]
            corp_id = corp['corporation_id']
            if is_npc_corporation(corp_id):
                logger.debug(f"    Skipping NPC corp {corp_id}")
                continue

            start_date = corp.get('start_date')
            end_date = history[i + 1]['start_date'] if i + 1 < len(history) else now()
            delta = (ensure_datetime(end_date) - ensure_datetime(start_date)).days

            corp_info = get_corp_info(corp_id)
            corp_name = corp_info["name"]
            alliance_id = corp_info["alliance_id"]
            alliance_name = get_alliance_name(alliance_id) if alliance_id else None

            color = ''
            if delta < 10:
                color = 'red'
            elif delta < 30:
                color = 'orange'

            logger.debug(f"    Corp: {corp_id} ({corp_name}), Alliance: {alliance_name}, Delta: {delta} days")

            corp_link = f"https://zkillboard.com/corporation/{corp_id}/"
            alliance_link = f"https://zkillboard.com/alliance/{alliance_id}/" if alliance_id else None
            alliance_full_link = f'<a href="{alliance_link}" target="_blank">{alliance_name}</a>' if alliance_link else "None"

            rows.append({
                "corp_name": corp_name,
                "corp_link": corp_link,
                "alliance_name": alliance_name,
                "alliance_link": alliance_link,
                "alliance_full_link": alliance_full_link,
                "delta": delta,
                "color": color
            })

        # Build HTML table for this character
        html += f"<h4>{char_name}</h4>"
        html += '<table class="table table-striped">'
        html += '<thead><tr><th>Corporation</th><th>Alliance</th><th>Time Spent</th></tr></thead><tbody>'

        for row in rows:
            row_html = (
                '<tr>'
                f'<td style="color:{row["color"] or "inherit"};">'
                f'<a href="{row["corp_link"]}" target="_blank">{row["corp_name"]}</a></td>'
                f'<td style="color:{row["color"] or "inherit"};">'
                f'<td style="color:{row["color"] or "inherit"};">{row["alliance_full_link"]}</td>'
                f'<td style="color:{row["color"] or "inherit"};">{row["delta"]} days</td>'
                '</tr>'
            )
            html += format_html(row_html)
        html += '</tbody></table>'

    return format_html(html)
