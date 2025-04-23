import logging
from django.utils.html import format_html
from django.utils.timezone import now
from esi.clients import EsiClientProvider
from allianceauth.authentication.models import CharacterOwnership
from dateutil.parser import parse as parse_datetime
from ..models import BigBrotherConfig

logger = logging.getLogger(__name__)
esi = EsiClientProvider()

# Cache for alliance histories
def_cache = {}

# External site favicons, fetched each time directly from the source
ZKILL_ICON = "https://zkillboard.com/favicon.ico"
EVEWHO_ICON = "https://evewho.com/favicon.ico"
DOTLAN_ICON = "https://evemaps.dotlan.net/favicon.ico"


def ensure_datetime(value):
    if isinstance(value, str):
        return parse_datetime(value)
    return value


def is_npc_corporation(corp_id):
    return 1_000_000 <= corp_id < 2_000_000


def get_corporation_info(corp_id):
    try:
        result = esi.client.Corporation.get_corporations_corporation_id(
            corporation_id=corp_id
        ).results()
        return {"name": result.get("name", f"Unknown ({corp_id})")}
    except Exception:
        return {"name": f"Unknown Corp ({corp_id})"}


def get_alliance_history_for_corp(corp_id):
    if corp_id in def_cache:
        return def_cache[corp_id]
    try:
        response = esi.client.Corporation.get_corporations_corporation_id_alliancehistory(
            corporation_id=corp_id
        ).results()
        history = [{"alliance_id": h.get("alliance_id"), "start_date": ensure_datetime(h.get("start_date"))} for h in response]
        history.sort(key=lambda x: x["start_date"])
    except Exception:
        history = []
    def_cache[corp_id] = history
    return history


def get_alliance_name(alliance_id):
    if not alliance_id:
        return "-"
    try:
        result = esi.client.Alliance.get_alliances_alliance_id(
            alliance_id=alliance_id
        ).results()
        return result.get("name", f"Unknown ({alliance_id})")
    except Exception:
        return f"Unknown ({alliance_id})"


def get_frequent_corp_changes(user_id):
    # Load hostile lists
    cfg = BigBrotherConfig.get_solo()
    hostile_corps = {int(cid) for cid in cfg.hostile_corporations.split(',') if cid}
    hostile_alliances = {int(aid) for aid in cfg.hostile_alliances.split(',') if aid}

    characters = CharacterOwnership.objects.filter(user__id=user_id)
    html = ""

    for char in characters:
        char_name = str(char.character)
        try:
            response = esi.client.Character.get_characters_character_id_corporationhistory(
                character_id=char.character.character_id
            ).results()
        except Exception:
            continue

        history = list(reversed(response))
        rows = []

        for idx, membership in enumerate(history):
            corp_id = membership['corporation_id']
            if is_npc_corporation(corp_id):
                continue

            # Membership window
            start = ensure_datetime(membership['start_date'])
            end = ensure_datetime(history[idx+1]['start_date']) if idx+1 < len(history) else now()
            total_days = (end - start).days

            corp_name = get_corporation_info(corp_id)['name']
            membership_range = f"{start.date()} - {end.date()}"

            # Corp cell with external site favicons (fetched live)
            corp_color = 'red' if (hostile_corps and corp_id in hostile_corps) else 'inherit'
            corp_cell = (
                f'<span style="color:{corp_color};">{corp_name}</span>'
                f'<a href="https://zkillboard.com/corporation/{corp_id}/" target="_blank">'
                f'<img src="{ZKILL_ICON}" width="16" height="16" style="margin-left:4px;vertical-align:middle;"/></a>'
                f'<a href="https://evewho.com/corp/{corp_id}" target="_blank">'
                f'<img src="{EVEWHO_ICON}" width="16" height="16" style="margin-left:2px;vertical-align:middle;"/></a>'
                f'<a href="https://evemaps.dotlan.net/corp/{corp_id}" target="_blank">'
                f'<img src="{DOTLAN_ICON}" width="16" height="16" style="margin-left:2px;vertical-align:middle;"/></a>'
            )

            # Alliance segments
            alliances_html = []
            periods_html = []
            alliance_history = get_alliance_history_for_corp(corp_id)
            for j, ent in enumerate(alliance_history):
                a_start = ent['start_date']
                a_end = alliance_history[j+1]['start_date'] if j+1 < len(alliance_history) else None
                seg_start = max(start, a_start)
                seg_end = min(end, a_end) if a_end else end
                if seg_start < seg_end:
                    aid = ent['alliance_id']
                    aname = get_alliance_name(aid)
                    period = f"{seg_start.date()} - {seg_end.date()}"

                    if aid:
                        alliance_color = 'red' if (hostile_alliances and aid in hostile_alliances) else 'inherit'
                        name_cell = f'<span style="color:{alliance_color};">{aname}</span>'
                        icons = (
                            f'<a href="https://zkillboard.com/alliance/{aid}/" target="_blank">'
                            f'<img src="{ZKILL_ICON}" width="16" height="16" style="margin-left:4px;vertical-align:middle;"/></a>'
                            f'<a href="https://evewho.com/alliance/{aid}" target="_blank">'
                            f'<img src="{EVEWHO_ICON}" width="16" height="16" style="margin-left:2px;vertical-align:middle;"/></a>'
                            f'<a href="https://evemaps.dotlan.net/alliance/{aid}" target="_blank">'
                            f'<img src="{DOTLAN_ICON}" width="16" height="16" style="margin-left:2px;vertical-align:middle;"/></a>'
                        )
                    else:
                        name_cell = '-'
                        icons = ''
                    alliances_html.append(name_cell + icons)
                    periods_html.append(period)

            if not alliances_html:
                alliances_html = ['-']
                periods_html = [membership_range]

            # Duration cell coloring only
            dur_color = 'red' if total_days < 10 else ('orange' if total_days < 30 else 'inherit')

            rows.append({
                'corp_cell': corp_cell,
                'membership_range': membership_range,
                'alliances_html': '<br>'.join(alliances_html),
                'periods_html': '<br>'.join(periods_html),
                'total_days': total_days,
                'dur_color': dur_color,
            })

        html += f"<h4>{char_name}</h4>"
        html += '<table class="table table-striped">'
        html += '<thead><tr><th>Corporation</th><th>Membership</th><th>Alliance(s)</th><th>Alliance Dates</th><th>Time in Corp</th></tr></thead><tbody>'
        for r in rows:
            row_html = (
                '<tr>'
                f'<td>{r["corp_cell"]}</td>'
                f'<td>{r["membership_range"]}</td>'
                f'<td>{r["alliances_html"]}</td>'
                f'<td>{r["periods_html"]}</td>'
                f'<td style="color:{r["dur_color"]};">{r["total_days"]} days</td>'
                '</tr>'
            )
            html += format_html(row_html)
        html += '</tbody></table>'

    return format_html(html)
