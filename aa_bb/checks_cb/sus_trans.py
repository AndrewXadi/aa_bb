import html
import logging

from typing import Dict, Optional, List
from datetime import datetime

from django.utils import timezone

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

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
from corptools.models import CorporationAudit, CorporationWalletJournalEntry
from allianceauth.eveonline.models import EveCorporationInfo
from ..models import BigBrotherConfig, ProcessedTransaction, SusTransactionNote

SUS_TYPES = ("player_trading","corporation_account_withdrawal","player_donation")

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


def gather_user_transactions(user_id: int):
    corp_info = EveCorporationInfo.objects.get(corporation_id=user_id)
    corp_audit = CorporationAudit.objects.get(corporation=corp_info)

    qs = CorporationWalletJournalEntry.objects.filter(division__corporation=corp_audit)
    logger.info(f"qs:{qs.count()}")
    return qs


def get_user_transactions(qs) -> Dict[int, Dict]:
    """
    Transform raw WalletJournalEntry queryset into structured dict
    with first_party (first_party) and second_party (second_party) info,
    resolving corp/alliance at transaction time.
    """
    result: Dict[int, Dict] = {}
    for entry in qs:
        tx_id = entry.entry_id
        tx_date = entry.date

        # first_party = first_party_id
        first_party_id = entry.first_party_id
        first_party_type = get_eve_entity_type(first_party_id)
        iinfo = get_entity_info(first_party_id, tx_date)

        # second_party = second_party_id
        second_party_id = entry.second_party_id
        second_party_type = get_eve_entity_type(second_party_id)
        ainfo = get_entity_info(second_party_id, tx_date)

        context = ""
        context_id = entry.context_id
        context_type = entry.context_id_type
        if context_type == "structure_id":
            context = f"Structure ID: {context_id}"
        elif context_type == "character_id":
            context = f"Character: {get_entity_info(context_id, tx_date)['name']}"
        elif context_type == "eve_system":
            context = "EVE System"
        elif context_type == None:
            context = "None"
        elif context_type == "market_transaction_id":
            context = f"Market Transaction ID: {context_id}"
        else:
            context = f"{context_type}: {context_id}"

        amount =  "{:,}".format(entry.amount)
        balance =  "{:,}".format(entry.balance)

        result[tx_id] = {
            'entry_id': tx_id,
            'date': tx_date,
            'amount': amount,
            'balance': balance,
            'description': entry.description,
            'reason': entry.reason,
            'first_party_id': first_party_id,
            'first_party_name': iinfo['name'],
            'first_party_corporation_id': iinfo['corp_id'],
            'first_party_corporation': iinfo['corp_name'],
            'first_party_alliance_id': iinfo['alli_id'],
            'first_party_alliance': iinfo['alli_name'],
            'second_party_id': second_party_id,
            'second_party_name': ainfo['name'],
            'second_party_corporation_id': ainfo['corp_id'],
            'second_party_corporation': ainfo['corp_name'],
            'second_party_alliance_id': ainfo['alli_id'],
            'second_party_alliance': ainfo['alli_name'],
            'context': context,
            'type': entry.ref_type,
        }
    logger.debug(f"Transformed {len(result)} transactions")
    return result


def is_transaction_hostile(tx: dict) -> bool:
    """
    Mark transaction as hostile if first_party or second_party or corps/alliances are blacklisted
    """
    cfg = BigBrotherConfig.get_solo()
    if check_char_corp_bl(tx.get('first_party_id')) or check_char_corp_bl(tx.get('second_party_id')):
        return True
    wlcorp = set((cfg.whitelist_corporations or "").split(','))
    wlali = set((cfg.whitelist_alliances or "").split(','))
    fpcorp = str(tx.get('first_party_corporation_id') or '')
    spcorp = str(tx.get('second_party_corporation_id') or '')
    fpali = str(tx.get('first_party_alliance_id') or '')
    spali = str(tx.get('second_party_alliance_id') or '')
    # Check if both parties are whitelisted (corp OR alliance)
    fp_whitelisted = fpcorp in wlcorp or fpali in wlali
    sp_whitelisted = spcorp in wlcorp or spali in wlali
    # logger.info(f"first party:{tx.get('first_party_id')}, cid:{fpcorp}, aid:{fpali}, fpwl:{fp_whitelisted}, 2nd: {tx.get('second_party_id')}, cid:{spcorp}, aid:{spali}, spwl:{sp_whitelisted}, wlali:{wlali}")

    if fp_whitelisted and sp_whitelisted:
        return False
    for key in SUS_TYPES:
        if key in tx.get('type'):
            return True
    for key in ('first_party_corporation_id', 'second_party_corporation_id'):
        if tx.get(key) and str(tx[key]) in cfg.hostile_corporations:
            return True
    for key in ('first_party_alliance_id', 'second_party_alliance_id'):
        if tx.get(key) and str(tx[key]) in cfg.hostile_alliances:
            return True
    return False


def render_transactions(user_id: int) -> str:
    """
    Render HTML table of recent hostile wallet transactions for user
    """
    qs = gather_user_transactions(user_id)
    txs = get_user_transactions(qs)

    # sort by date desc
    all_list = sorted(txs.values(), key=lambda x: x['date'], reverse=True)
    hostile = [t for t in all_list if is_transaction_hostile(t)]
    if not hostile:
        return '<p>No hostile transactions found.</p>'

    limit = 50
    display = hostile[:limit]
    skipped = max(0, len(hostile) - limit)

    # define headers to show
    first = display[0]
    HIDDEN = {'first_party_id','second_party_id','first_party_corporation_id','second_party_corporation_id',
              'first_party_alliance_id','second_party_alliance_id','entry_id'}
    headers = [k for k in first.keys() if k not in HIDDEN]

    parts = ['<table class="table table-striped">','<thead>','<tr>']
    for h in headers:
        parts.append(f'<th>{html.escape(h.replace("_"," ").title())}</th>')
    parts.extend(['</tr>','</thead>','<tbody>'])

    for t in display:
        parts.append('<tr>')
        for col in headers:
            val = html.escape(str(t.get(col)))
            style = ''
            # reuse contract style logic by mapping to transaction
            if col == 'type':
                for key in SUS_TYPES:
                    if key in t['type']:
                        style = 'color: red;'
            if col in ('first_party_name', 'second_party_name') and check_char_corp_bl(t.get(col + '_id', -1)):
                style = 'color: red;'
            if col.endswith('corporation') and t.get(col + '_id') and str(t[col + '_id']) in BigBrotherConfig.get_solo().hostile_corporations:
                style = 'color: red;'
            if col.endswith('alliance') and t.get(col + '_id') and str(t[col + '_id']) in BigBrotherConfig.get_solo().hostile_alliances:
                style = 'color: red;'
            def make_td(val, style=""):
                style_attr = f' style="{style}"' if style else ""
                return f"<td{style_attr}>{val}</td>"
            parts.append(make_td(val, style))
        parts.append('</tr>')

    parts.extend(['</tbody>','</table>'])
    if skipped:
        parts.append(f'<p>Showing {limit} of {len(hostile)} hostile transactions; skipped {skipped} older ones.</p>')
    return '\n'.join(parts)


def get_corp_hostile_transactions(user_id: int) -> Dict[int, str]:
    """
    Identify and note hostile transactions, storing notes and returning summary
    """
    qs_all = gather_user_transactions(user_id)
    all_ids = list(qs_all.values_list('entry_id', flat=True))
    seen = set(ProcessedTransaction.objects.filter(entry_id__in=all_ids)
                                              .values_list('entry_id', flat=True))
    notes: Dict[int, str] = {}
    new = [eid for eid in all_ids if eid not in seen]
    del all_ids
    del seen
    processed = 0
    if new:
        processed + 1
        new_qs = qs_all.filter(entry_id__in=new)
        del qs_all
        rows = get_user_transactions(new_qs)
        for eid, tx in rows.items():
            pt, created = ProcessedTransaction.objects.get_or_create(entry_id=eid)
            if not created:
                continue
            if not is_transaction_hostile(tx):
                continue
            flags = []
            if tx['type']:
                for key in SUS_TYPES:
                    if key in tx['type']:
                        flags.append(f"Transaction type is **{tx['type']}**")
            if tx['first_party_id'] and check_char_corp_bl(tx['first_party_id']):
                flags.append(f"first_party **{tx['first_party_name']}** is on blacklist")
            if str(tx['first_party_corporation_id']) in BigBrotherConfig.get_solo().hostile_corporations:
                flags.append(f"first_party corp **{tx['first_party_corporation']}** is hostile")
            if str(tx['first_party_alliance_id']) in BigBrotherConfig.get_solo().hostile_alliances:
                flags.append(f"first_party alliance **{tx['first_party_alliance']}** is hostile")
            if tx['second_party_id'] and check_char_corp_bl(tx['second_party_id']):
                flags.append(f"second_party **{tx['second_party_name']}** is on blacklist")
            if str(tx['second_party_corporation_id']) in BigBrotherConfig.get_solo().hostile_corporations:
                flags.append(f"second_party corp **{tx['second_party_corporation']}** is hostile")
            if str(tx['second_party_alliance_id']) in BigBrotherConfig.get_solo().hostile_alliances:
                flags.append(f"second_party alliance **{tx['second_party_alliance']}** is hostile")
            flags_text = "\n    - ".join(flags)

            note = (
                f"- **{tx['date']}**: "
                f"\n  - amount **{tx['amount']}**, "
                f"\n  - type **{tx['type']}**, "
                f"\n  - reason **{tx['reason']}**, "
                f"\n  - from **{tx['first_party_name']}**(**{tx['first_party_corporation']}**/"
                  f"**{tx['first_party_alliance']}**), "
                f"\n  - to **{tx['second_party_name']}**(**{tx['second_party_corporation']}**/"
                  f"**{tx['second_party_alliance']}**); "
                f"\n  - flags:\n    - {flags_text}"
            )
            SusTransactionNote.objects.update_or_create(
                transaction=pt,
                defaults={'user_id': user_id, 'note': note}
            )
            notes[eid] = note

    for note_obj in SusTransactionNote.objects.filter(user_id=user_id):
        notes[note_obj.transaction.entry_id] = note_obj.note

    return notes
