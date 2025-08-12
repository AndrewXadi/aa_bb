import html
import logging

from typing import Dict, Optional, List
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
    get_entity_info,

)
from aa_bb.checks.corp_blacklist import check_char_corp_bl
from corptools.models import CorporateContract, CorporationAudit
from allianceauth.eveonline.models import EveCorporationInfo
from ..models import BigBrotherConfig, ProcessedContract, SusContractNote
from django.utils import timezone

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


def gather_user_contracts(user_id: int):
    corp_info = EveCorporationInfo.objects.get(corporation_id=user_id)
    corp_audit = CorporationAudit.objects.get(corporation=corp_info)

    qs = CorporateContract.objects.filter(corporation=corp_audit)
    return qs

def get_user_contracts(qs) -> Dict[int, Dict]:
    """
    Fetch contracts for a user, extracting issuer and assignee details
    with corp/alliance names at the contract issue date, combined.
    Uses c.for_corporation to identify corporate assignees.
    """
    logger.info(f"Number of contracts: {len(qs)}")
    number = 0
    result: Dict[int, Dict] = {}
    for c in qs:
        cid = c.contract_id
        issue = c.date_issued
        number += 1
        logger.info(f"corp contract number: {number}")

        # -- issuer --
        issuer_id = get_character_id(c.issuer_name)
        issuer_type = get_eve_entity_type(issuer_id)
        timeee = getattr(c, "timestamp", timezone.now())
        iinfo = get_entity_info(issuer_id, timeee)

        # -- assignee --
        if c.assignee_id != 0:
            assignee_id = c.assignee_id
        else:
            assignee_id = c.acceptor_id
        
        assignee_type = get_eve_entity_type(assignee_id)
        ainfo = get_entity_info(assignee_id, timeee)


        result[cid] = {
            'contract_id':              cid,
            'issued_date':              issue,
            'end_date':                 c.date_completed or c.date_expired,
            'contract_type':            c.contract_type,
            'issuer_name':              iinfo["name"],
            'issuer_id':                issuer_id,
            'issuer_corporation':       iinfo["corp_name"],
            'issuer_corporation_id':    iinfo["corp_id"],
            'issuer_alliance':          iinfo["alli_name"],
            'issuer_alliance_id':       iinfo["alli_id"],
            'assignee_name':            ainfo["name"],
            'assignee_id':              assignee_id,
            'assignee_corporation':     ainfo["corp_name"],
            'assignee_corporation_id':  ainfo["corp_id"],
            'assignee_alliance':        ainfo["alli_name"],
            'assignee_alliance_id':     ainfo["alli_id"],
            'status':                   c.status,
        }
    logger.info(f"Number of contracts returned: {len(result)}")
    return result

def get_cell_style_for_contract_row(column: str, row: dict) -> str:
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

def is_contract_row_hostile(row: dict) -> bool:
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
    contracts = get_user_contracts(gather_user_contracts(user_id))
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
    hostile_rows = [row for row in all_rows if is_contract_row_hostile(row)]

    total = len(hostile_rows)
    logger.info(f"found {total} hostile contracts")
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
            style = get_cell_style_for_contract_row(col, row)
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



def get_corp_hostile_contracts(user_id: int) -> Dict[int, str]:
    cfg = BigBrotherConfig.get_solo()
    hostile_corps = cfg.hostile_corporations
    hostile_allis = cfg.hostile_alliances

    # 1) Gather all raw contracts
    all_qs = gather_user_contracts(user_id)
    all_ids = list(all_qs.values_list('contract_id', flat=True))
    del all_qs

    # 2) Which are already processed?
    seen_ids = set(ProcessedContract.objects.filter(contract_id__in=all_ids)
                                      .values_list('contract_id', flat=True))

    notes: Dict[int, str] = {}
    new_ids = [cid for cid in all_ids if cid not in seen_ids]
    del all_ids
    del seen_ids
    processed =0
    if new_ids:
        processed + 1
        logger.info(f"Processing {processed}/{len(new_ids)} contracts for {user_id}, total was {len(all_ids)}")
        # 3) Hydrate only new contracts
        new_qs = all_qs.filter(contract_id__in=new_ids)
        new_rows = get_user_contracts(new_qs)

        for cid, c in new_rows.items():
            # only create ProcessedContract if it doesn't already exist
            pc, created = ProcessedContract.objects.get_or_create(contract_id=cid)
            # if we've processed it before, skip the rest
            if not created:
                continue
            

            if not is_contract_row_hostile(c):
                continue

            flags: List[str] = []
            # issuer
            if c['issuer_name'] != '-' and check_char_corp_bl(c['issuer_id']):
                flags.append(f"Issuer **{c['issuer_name']}** is on blacklist")
            if str(c['issuer_corporation_id']) in hostile_corps:
                flags.append(f"Issuer corp **{c['issuer_corporation']}** is hostile")
            if str(c['issuer_alliance_id']) in hostile_allis:
                flags.append(f"Issuer alliance **{c['issuer_alliance']}** is hostile")
            # assignee
            if c['assignee_name'] != '-' and check_char_corp_bl(c['assignee_id']):
                flags.append(f"Assignee **{c['assignee_name']}** is on blacklist")
            if str(c['assignee_corporation_id']) in hostile_corps:
                flags.append(f"Assignee corp **{c['assignee_corporation']}** is hostile")
            if str(c['assignee_alliance_id']) in hostile_allis:
                flags.append(f"Assignee alliance **{c['assignee_alliance']}** is hostile")
            flags_text = "\n    - ".join(flags)

            note_text = (
                f"- **{c['contract_type']}**: "
                f"\n  - issued **{c['issued_date']}**, "
                f"\n  - ended **{c['end_date']}**, "
                f"\n  - from **{c['issuer_name']}**(**{c['issuer_corporation']}**/"
                  f"**{c['issuer_alliance']}**), "
                f"\n  - to **{c['assignee_name']}**(**{c['assignee_corporation']}**/"
                  f"**{c['assignee_alliance']}**); "
                f"\n  - flags:\n    - {flags_text}"
            )
            SusContractNote.objects.update_or_create(
                contract=pc,
                defaults={'user_id': user_id, 'note': note_text}
            )
            notes[cid] = note_text

    # 4) Pull in old notes
    for scn in SusContractNote.objects.filter(user_id=user_id):
        notes[scn.contract.contract_id] = scn.note

    return notes