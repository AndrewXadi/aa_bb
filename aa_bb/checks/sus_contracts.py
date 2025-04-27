import html
import logging

from typing import Dict, Optional
from datetime import datetime

from ..app_settings import (
    is_npc_corporation,
    is_npc_character,
    get_character_employment,
    get_alliance_history_for_corp,
    resolve_alliance_name,
    resolve_corporation_name,
    resolve_character_name,
    get_user_characters,
    get_character_id,
    get_eve_entity_type,

)
from .corp_blacklist import check_char_corp_bl
from corptools.models import Contract
from ..models import BigBrotherConfig

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def _find_employment_at(employment: list, date: datetime) -> Optional[dict]:
    for i, rec in enumerate(employment):
        start = rec.get('start_date')
        end = rec.get('end_date')
        if start and start <= date and (end is None or date < end):
            return rec
    return None


def _find_alliance_at(history: list, date: datetime) -> Optional[int]:
    for i, rec in enumerate(history):
        start = rec.get('start_date')
        next_start = history[i+1]['start_date'] if i+1 < len(history) else None
        if start and start <= date and (next_start is None or date < next_start):
            return rec.get('alliance_id')
    return None


def get_user_contracts(user_id: int) -> Dict[int, Dict]:
    """
    Fetch contracts for a user, extracting issuer and assignee details
    with corp/alliance names at the contract issue date, combined.
    Uses c.for_corporation to identify corporate assignees.
    """
    user_chars = get_user_characters(user_id)
    user_ids = set(user_chars.keys())

    qs = Contract.objects.filter(
        character__character__character_id__in=user_ids
    ).select_related('character__character')
    logger.info(f"Number of contracts: {len(qs)}")
    number = 0
    result: Dict[int, Dict] = {}
    for c in qs:
        cid = c.contract_id
        issue = c.date_issued
        number += 1
        logger.info(f"contract number: {number}")

        # -- issuer --
        issuer_name = '-'
        issuer_id = get_character_id(c.issuer_name)
        issuer_type = get_eve_entity_type(issuer_id)
        issuer_corporation = '-'
        issuer_corporation_id = None
        issuer_alliance = '-'
        issuer_alliance_id = None
        if issuer_id and issuer_type:
            if issuer_type == 'character':
                issuer_name = resolve_character_name(issuer_id)
                emp = get_character_employment(issuer_id)
                rec = _find_employment_at(emp, issue)
                if rec:
                    issuer_corporation_id = rec.get('corporation_id')
                    issuer_corporation = rec.get('corporation_name')
                    issuer_alliance_id = _find_alliance_at(rec.get('alliance_history', []), issue)
                    issuer_alliance = resolve_alliance_name(issuer_alliance_id) if issuer_alliance_id else '-'
            elif issuer_type == 'corporation':
                issuer_corporation = resolve_corporation_name(issuer_id)
                issuer_corporation_id = issuer_id
                hist = get_alliance_history_for_corp(issuer_id)
                issuer_alliance_id = _find_alliance_at(hist, issue)
                issuer_alliance = resolve_alliance_name(issuer_alliance_id) if issuer_alliance_id else '-'
            elif issuer_type == 'alliance':
                issuer_alliance = resolve_alliance_name(issuer_id)
                issuer_alliance_id = issuer_id

        # -- assignee --
        assignee_name = '-'
        assignee_id = c.assignee_id
        assignee_type = get_eve_entity_type(c.assignee_id)
        assignee_corporation = '-'
        assignee_corporation_id = None
        assignee_alliance = '-'
        assignee_alliance_id = None
        if assignee_id and assignee_type:
            if assignee_type == 'character':
                assignee_name = resolve_character_name(assignee_id)
                emp = get_character_employment(assignee_id)
                rec = _find_employment_at(emp, issue)
                if rec:
                    assignee_corporation_id = rec.get('corporation_id')
                    assignee_corporation = rec.get('corporation_name')
                    assignee_alliance_id = _find_alliance_at(rec.get('alliance_history', []), issue)
                    assignee_alliance = resolve_alliance_name(assignee_alliance_id) if assignee_alliance_id else '-'
            elif assignee_type == 'corporation':
                assignee_corporation = resolve_corporation_name(assignee_id)
                assignee_corporation_id = issuer_id
                hist = get_alliance_history_for_corp(assignee_id)
                assignee_alliance_id = _find_alliance_at(hist, issue)
                assignee_alliance = resolve_alliance_name(assignee_alliance_id) if assignee_alliance_id else '-'
            elif assignee_type == 'alliance':
                assignee_alliance = resolve_alliance_name(assignee_id)
                assignee_alliance_id = assignee_id

        result[cid] = {
            'contract_id':              cid,
            'issued_date':              issue,
            'end_date':                 c.date_completed or c.date_expired,
            'contract_type':            c.contract_type,
            'issuer_name':              issuer_name,
            'issuer_id':                issuer_id,
            'issuer_corporation':       issuer_corporation,
            'issuer_corporation_id':    issuer_corporation_id,
            'issuer_alliance':          issuer_alliance,
            'issuer_alliance_id':       issuer_alliance_id,
            'assignee_name':            assignee_name,
            'assignee_id':              assignee_id,
            'assignee_corporation':     assignee_corporation,
            'assignee_corporation_id':  assignee_corporation_id,
            'assignee_alliance':        assignee_alliance,
            'assignee_alliance_id':     assignee_alliance_id,
            'status':                   c.status,
        }
    logger.info(f"Number of contracts returned: {len(result)}")
    return result

def get_cell_style_for_row(column: str, row: dict) -> str:
    if column == 'issuer_name':
        cid = row.get("issuer_id")
        if check_char_corp_bl(cid):
            return 'color: red;'
        else:
            return ''
        
    if column == 'assignee_name':
        cid = row.get("assignee_id")
        if check_char_corp_bl(cid):
            return 'color: red;'
        else:
            return ''

    if column == 'issuer_corporation':
        aid = row.get("issuer_corporation_id")
        if aid and str(aid) in BigBrotherConfig.get_solo().hostile_corporations:
            return 'color: red;'
        else:
            return ''

    if column == 'issuer_alliance':
        coid = row.get("issuer_alliance_id")
        if coid and str(coid) in BigBrotherConfig.get_solo().hostile_alliances:
            return 'color: red;'
        else:
            return ''

    if column == 'assignee_corporation':
        aid = row.get("assignee_corporation_id")
        if aid and str(aid) in BigBrotherConfig.get_solo().hostile_corporations:
            return 'color: red;'
        else:
            return ''

    if column == 'assignee_alliance':
        coid = row.get("assignee_alliance_id")
        if coid and str(coid) in BigBrotherConfig.get_solo().hostile_alliances:
            return 'color: red;'
        else:
            return ''

    return ''

def is_row_hostile(row: dict) -> bool:
    """Returns True if the row matches hostile corp/char/alliance criteria."""
    if check_char_corp_bl(row.get("issuer_id")):
        return True
    if check_char_corp_bl(row.get("assignee_id")):
        return True

    solo = BigBrotherConfig.get_solo()

    if row.get("issuer_corporation_id") and str(row["issuer_corporation_id"]) in solo.hostile_corporations:
        return True
    if row.get("issuer_alliance_id") and str(row["issuer_alliance_id"]) in solo.hostile_alliances:
        return True
    if row.get("assignee_corporation_id") and str(row["assignee_corporation_id"]) in solo.hostile_corporations:
        return True
    if row.get("assignee_alliance_id") and str(row["assignee_alliance_id"]) in solo.hostile_alliances:
        return True

    return False



def render_contracts(user_id: int) -> str:
    """
    Renders an HTML table of user contracts with per-cell styling.
    Hostile/blacklisted entries will be colored red via get_cell_style_for_row.
    Limits output to the first 50 hostile rows and indicates how many were skipped.
    """
    contracts = get_user_contracts(user_id)
    logger.info(f"Number of contracts: {len(contracts)}")
    if not contracts:
        return '<p>No contracts found.</p>'

    # Sort rows by issue date descending
    all_rows = sorted(
        contracts.values(),
        key=lambda x: x['issued_date'],
        reverse=True
    )

    # Filter to only hostile rows
    # Ensure is_row_hostile is defined/imported
    hostile_rows = [row for row in all_rows if is_row_hostile(row)]

    total = len(hostile_rows)
    limit = 50
    if total == 0:
        return '<p>No hostile contracts found.</p>'

    display_rows = hostile_rows[:limit]
    skipped = total - limit if total > limit else 0

    # Determine headers using the first displayed row
    first_row = display_rows[0]
    HIDDEN_COLUMNS = {
        'assignee_alliance_id', 'assignee_corporation_id',
        'issuer_alliance_id', 'issuer_corporation_id',
        'assignee_id', 'issuer_id', 'contract_id'
    }
    headers = [h for h in first_row.keys() if h not in HIDDEN_COLUMNS]

    html_parts = []
    html_parts.append('<table class="table table-striped">')
    html_parts.append('  <thead>')
    html_parts.append('    <tr>')
    for h in headers:
        label = html.escape(str(h)).replace("_", " ").title()
        html_parts.append(f'      <th>{label}</th>')
    html_parts.append('    </tr>')
    html_parts.append('  </thead>')
    html_parts.append('  <tbody>')

    for row in display_rows:
        html_parts.append('    <tr>')
        for col in headers:
            raw = row.get(col)
            text = html.escape(str(raw))
            style = get_cell_style_for_row(col, row)
            if style:
                html_parts.append(f'      <td style="{style}">{text}</td>')
            else:
                html_parts.append(f'      <td>{text}</td>')
        html_parts.append('    </tr>')

    html_parts.append('  </tbody>')
    html_parts.append('</table>')

    # Indicate skipped rows
    if skipped > 0:
        html_parts.append(
            f'<p>Showing {limit} of {total} hostile contracts; '
            f'skipped {skipped} older contracts.</p>'
        )

    logger.info(f"rendered {len(display_rows)} hostile contracts, skipped {skipped}")
    return "\n".join(html_parts)



def get_user_hostile_contracts(user_id: int) -> Dict[int, str]:
    cfg = BigBrotherConfig.get_solo()
    hostile_corps = cfg.hostile_corporations
    hostile_allis = cfg.hostile_alliances
    notes: Dict[int, str] = {}
    for issuer_id, c in get_user_contracts(user_id).items():
        flags = []
        # issuer
        if c['issuer_name'] != '-' and check_char_corp_bl(issuer_id):
            flags.append(f"Issuer **{c['issuer_name']}** is on blacklist")
        if str(c['issuer_corporation_id']) in hostile_corps:
            flags.append(f"Issuer corp **{c['issuer_corporation']}** is hostile")
        if str(c['issuer_alliance_id']) in hostile_allis:
            flags.append(f"Issuer alli **{c['issuer_alliance']}** is hostile")
        # assignee
        if c['assignee_name'] != '-' and check_char_corp_bl(c['assignee_id']):
            flags.append(f"Assignee **{c['assignee_name']}** is on blacklist")
        if str(c['assignee_corporation_id']) in hostile_corps:
            flags.append(f"Assignee corp **{c['assignee_corporation']}** is hostile")
        if str(c['assignee_alliance_id']) in hostile_allis:
            flags.append(f"Assignee alli **{c['assignee_alliance']}** is hostile")
        if flags:
            notes[issuer_id] = f"- Contract {c['contract_id']} ({c['contract_type']}) issued {c['issued_date']}, ended {c['end_date']}; flags: {'; '.join(flags)}"
    logger.info(f"Number of contracts: {len(notes)}")
    return notes