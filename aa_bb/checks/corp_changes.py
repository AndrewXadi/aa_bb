import logging
from django.utils.html import format_html
from django.utils.timezone import now
from esi.clients import EsiClientProvider
from allianceauth.authentication.models import CharacterOwnership
from ..models import BigBrotherConfig
from ..app_settings import ensure_datetime, is_npc_corporation, get_alliance_history_for_corp, get_alliance_name, get_corporation_info

logger = logging.getLogger(__name__)
esi = EsiClientProvider()

# External site favicons, fetched each time directly from the source
ZKILL_ICON = "https://zkillboard.com/favicon.ico"
EVEWHO_ICON = "https://evewho.com/favicon.ico"
DOTLAN_ICON = "https://evemaps.dotlan.net/favicon.ico"
EVE411_ICON     = "https://www.eve411.com/favicon.ico"
FORUMS_ICON     = "https://eve-offline.net/favicon.ico"
EVESEARCH_ICON  = "https://eve-search.com/favicon.ico"


def get_frequent_corp_changes(user_id):
    # Load hostile lists
    cfg = BigBrotherConfig.get_solo()
    hostile_corps = {int(cid) for cid in cfg.hostile_corporations.split(',') if cid}
    hostile_alliances = {int(aid) for aid in cfg.hostile_alliances.split(',') if aid}

    characters = CharacterOwnership.objects.filter(user__id=user_id)
    html = ""

    for char in characters:
        char_name = str(char.character)
        char_id   = char.character.character_id
        try:
            response = esi.client.Character.get_characters_character_id_corporationhistory(
                character_id=char.character.character_id
            ).results()
        except Exception:
            continue

        char_links = (
            f'<a href="https://zkillboard.com/character/{char_id}/" target="_blank">'
            f'<img src="{ZKILL_ICON}" width="16" height="16" '
            f'style="margin-left:4px;vertical-align:middle;"/></a> '
            f'<a href="https://evewho.com/character/{char_id}" target="_blank">'
            f'<img src="{EVEWHO_ICON}" width="16" height="16" '
            f'style="margin-left:2px;vertical-align:middle;"/></a> '
            f'<a href="https://www.eve411.com/character/{char_id}" target="_blank">'
            f'<img src="{EVE411_ICON}" width="16" height="16" '
            f'style="margin-left:2px;vertical-align:middle;"/></a> '
            # Eve-Online forums user pages use the character name slug:
            f'<a href="https://forums.eveonline.com/u/{char_name.replace(" ", "_")}/summary" '
            f'target="_blank">'
            f'<img src="{FORUMS_ICON}" width="16" height="16" '
            f'style="margin-left:2px;vertical-align:middle;"/></a> '
            # and eve-search needs URL‐encoded name:
            f'<a href="https://eve-search.com/search/author/{char_name.replace(" ", "%20")}" '
            f'target="_blank">'
            f'<img src="{EVESEARCH_ICON}" width="16" height="16" '
            f'style="margin-left:2px;vertical-align:middle;"/></a> '
        )

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
                f'<img src="{ZKILL_ICON}" width="16" height="16" style="margin-left:4px;vertical-align:middle;"/></a> '
                f'<a href="https://evewho.com/corp/{corp_id}" target="_blank">'
                f'<img src="{EVEWHO_ICON}" width="16" height="16" style="margin-left:2px;vertical-align:middle;"/></a> '
                f'<a href="https://evemaps.dotlan.net/corp/{corp_id}" target="_blank">'
                f'<img src="{DOTLAN_ICON}" width="16" height="16" style="margin-left:2px;vertical-align:middle;"/></a> '
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
                            f'<img src="{ZKILL_ICON}" width="16" height="16" style="margin-left:4px;vertical-align:middle;"/></a> '
                            f'<a href="https://evewho.com/alliance/{aid}" target="_blank">'
                            f'<img src="{EVEWHO_ICON}" width="16" height="16" style="margin-left:2px;vertical-align:middle;"/></a> '
                            f'<a href="https://evemaps.dotlan.net/alliance/{aid}" target="_blank">'
                            f'<img src="{DOTLAN_ICON}" width="16" height="16" style="margin-left:2px;vertical-align:middle;"/></a> '
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

        html += format_html("<h3>{} {}</h3>", char_name, format_html(char_links))
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
