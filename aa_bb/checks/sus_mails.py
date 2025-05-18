import html
import logging

from typing import Dict, Optional, List
from datetime import datetime, timedelta
from functools import lru_cache
from django.utils import timezone

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
from .corp_blacklist import check_char_corp_bl
from corptools.models import MailMessage, MailRecipient
from ..models import BigBrotherConfig, ProcessedMail, SusMailNote

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def _find_employment_at(employment: List[dict], date: datetime) -> Optional[dict]:
    for rec in employment:
        start = rec.get('start_date')
        end = rec.get('end_date')
        if start and start <= date and (end is None or date < end):
            return rec
    return None


def _find_alliance_at(history: List[dict], date: datetime) -> Optional[int]:
    for i, rec in enumerate(history):
        start = rec.get('start_date')
        next_start = history[i+1]['start_date'] if i+1 < len(history) else None
        if start and start <= date and (next_start is None or date < next_start):
            return rec.get('alliance_id')
    return None


def gather_user_mails(user_id: int):
    """
    Return all MailMessage objects where the user is a recipient.
    """
    user_chars = get_user_characters(user_id)
    user_ids = set(user_chars.keys())
    qs = MailMessage.objects.filter(
        recipients__recipient_id__in=user_ids
    ).prefetch_related('recipients', 'recipients__recipient_name')
    logger.debug(f"Found {qs.count()} mails for user {user_id}")
    return qs


def get_user_mails(qs) -> Dict[int, Dict]:
    """
    Extract mails for a user, including sender details at send time.
    Returns dict keyed by message id.
    """
    result: Dict[int, Dict] = {}
    for m in qs:
        mid = m.id_key
        sent = m.timestamp


        # -- sender details --
        sender_id = m.from_id
        timeee = getattr(m, "timestamp", timezone.now())
        sinfo = get_entity_info(sender_id, timeee)

        # -- recipients list --
        recipient_names = []
        recipient_ids = []
        recipient_corps = []
        recipient_corp_ids = []
        recipient_alliances = []
        recipient_alliance_ids = []
        for mr in m.recipients.all():
            rid   = mr.recipient_id
            logger.info(f"getting info for {rid}")
            rinfo = get_entity_info(rid, timeee)
            recipient_ids.append(rid)
            recipient_names.append(rinfo["name"])
            recipient_corps.append(rinfo["corp_name"])
            recipient_corp_ids.append(rinfo["corp_id"])
            recipient_alliances.append(rinfo["alli_name"])
            recipient_alliance_ids.append(rinfo["alli_id"])

        result[mid] = {
            'message_id':               mid,
            'sent_date':                sent,
            'subject':                  m.subject or '',
            'sender_name':              sinfo["name"],
            'sender_id':                sender_id,
            'sender_corporation':       sinfo["corp_name"],
            'sender_corporation_id':    sinfo["corp_id"],
            'sender_alliance':          sinfo["alli_name"],
            'sender_alliance_id':       sinfo["alli_id"],
            'recipient_names':          recipient_names,
            'recipient_ids':            recipient_ids,
            'recipient_corps':          recipient_corps,
            'recipient_corp_ids':       recipient_corp_ids,
            'recipient_alliances':      recipient_alliances,
            'recipient_alliance_ids':   recipient_alliance_ids,
            'status':                   m.is_read and 'Read' or 'Unread',
        }
        logger.debug(f"Processed mail {mid}")
    logger.info(f"Extracted {len(result)} mails")
    return result


def get_cell_style_for_mail_cell(column: str, row: dict, index: Optional[int] = None) -> str:
    solo = BigBrotherConfig.get_solo()
    # sender cell
    if column.startswith('sender_'):
        if column == 'sender_name' and check_char_corp_bl(row.get('sender_id')):
            return 'color: red;'
        if column == 'sender_corporation' and str(row.get('sender_corporation_id')) in solo.hostile_corporations:
            return 'color: red;'
        if column == 'sender_alliance' and str(row.get('sender_alliance_id')) in solo.hostile_alliances:
            return 'color: red;'
    # recipient cell
    if column.startswith('recipient_') and index is not None:
        # blacklist check
        rid = row['recipient_ids'][index]
        if check_char_corp_bl(rid):
            return 'color: red;'
        # corp/alliance hostility
        cid = row['recipient_corps'][index] if column == 'recipient_corps' else None
        aid = row['recipient_alliance_ids'][index] if column == 'recipient_alliance_ids' else None
        if cid and str(cid) in solo.hostile_corporations:
            return 'color: red;'
        if aid and str(aid) in solo.hostile_alliances:
            return 'color: red;'
    return ''


def is_mail_row_hostile(row: dict) -> bool:
    solo = BigBrotherConfig.get_solo()
    # sender hostility
    if row.get('sender_name'):
        for key in ["GM ","CCP "]:
            if key in str(row["sender_name"]):
                return True
    if check_char_corp_bl(row.get('sender_id')):
        return True
    if str(row.get('sender_corporation_id')) in solo.hostile_corporations:
        return True
    if str(row.get('sender_alliance_id')) in solo.hostile_alliances:
        return True
    # any recipient hostility
    for idx, rid in enumerate(row['recipient_ids']):
        if check_char_corp_bl(rid):
            return True
        if str(row['recipient_corps'][idx]) in solo.hostile_corporations:
            return True
        if str(row['recipient_alliance_ids'][idx]) in solo.hostile_alliances:
            return True
    return False



def render_mails(user_id: int) -> str:
    mails = get_user_mails(gather_user_mails(user_id))
    if not mails:
        return '<p>No mails found.</p>'

    rows = sorted(mails.values(), key=lambda x: x['sent_date'], reverse=True)
    hostile_rows = [r for r in rows if is_mail_row_hostile(r)]
    total = len(hostile_rows)
    if total == 0:
        return '<p>No hostile mails found.</p>'

    limit = 50
    display = hostile_rows[:limit]
    skipped = max(total - limit, 0)

    # Only show these columns:
    VISIBLE = [
        'sent_date', 'subject',
        'sender_name', 'sender_corporation', 'sender_alliance',
        'recipient_names', 'recipient_corps', 'recipient_alliances', 'status',
    ]

    # Build HTML table
    html_parts = ['<table class="table table-striped">', '<thead><tr>']
    for col in VISIBLE:
        html_parts.append(f'<th>{html.escape(col.replace("_", " ").title())}</th>')
    html_parts.append('</tr></thead><tbody>')

    for row in display:
        html_parts.append('<tr>')
        for col in VISIBLE:
            val = row.get(col, '')
            # recipients come as lists
            if isinstance(val, list):
                parts = []
                for idx, item in enumerate(val):
                    style = ''  # default
                    # map list-column back to its id-array sibling:
                    if col == 'recipient_names':
                        rid = row['recipient_ids'][idx]
                        if check_char_corp_bl(rid):
                            style = 'color:red;'
                        elif str(row['recipient_corp_ids'][idx]) in BigBrotherConfig.get_solo().hostile_corporations:
                            style = 'color:red;'
                        elif str(row['recipient_alliance_ids'][idx]) in BigBrotherConfig.get_solo().hostile_alliances:
                            style = 'color:red;'
                    elif col == 'recipient_corps':
                        cid = row['recipient_corp_ids'][idx]
                        if cid and str(cid) in BigBrotherConfig.get_solo().hostile_corporations:
                            style = 'color:red;'
                    elif col == 'recipient_alliances':
                        aid = row['recipient_alliance_ids'][idx]
                        if aid and str(aid) in BigBrotherConfig.get_solo().hostile_alliances:
                            style = 'color:red;'

                    if style:
                        prefix = f"<span style='{style}'>"
                    else:
                        prefix = "<span>"

                    parts.append(f"{prefix}{html.escape(str(item))}</span>")
                cell = '<td>' + ', '.join(parts) + '</td>'
            else:
                # single-value columns
                style = ''
                if col.startswith('sender_'):
                    if col == 'sender_name' and check_char_corp_bl(row['sender_id']):
                        style = 'color:red;'
                    elif col == 'sender_corporation' and str(row['sender_corporation_id']) in BigBrotherConfig.get_solo().hostile_corporations:
                        style = 'color:red;'
                    elif col == 'sender_alliance' and str(row['sender_alliance_id']) in BigBrotherConfig.get_solo().hostile_alliances:
                        style = 'color:red;'
                # subject/content keyword highlighting can be done client-side
                if style:
                    style_attr = f" style='{style}'"
                else:
                    style_attr = ""
                cell = f"<td{style_attr}>{html.escape(str(val))}</td>"

            html_parts.append(cell)
        html_parts.append('</tr>')

    html_parts.append('</tbody></table>')
    if skipped:
        html_parts.append(f'<p>Showing {limit} of {total} hostile mails; skipped {skipped}.</p>')

    return '\n'.join(html_parts)



def get_user_hostile_mails(user_id: int) -> Dict[int, str]:
    cfg = BigBrotherConfig.get_solo()

    # 1) Gather all raw MailMessage IDs cheaply
    all_qs = gather_user_mails(user_id)
    all_ids = list(all_qs.values_list('id_key', flat=True))

    # 2) Find which IDs are already processed
    seen_ids = set(ProcessedMail.objects.filter(mail_id__in=all_ids)
                                  .values_list('mail_id', flat=True))

    # 3) Determine the new ones
    new_ids = [mid for mid in all_ids if mid not in seen_ids]
    notes: Dict[int, str] = {}

    if new_ids:
        # 4) Hydrate only the new mails
        new_qs = all_qs.filter(id_key__in=new_ids)
        new_rows = get_user_mails(new_qs)

        for mid, m in new_rows.items():
            # mark processed, skip if already exists
            pm, created = ProcessedMail.objects.get_or_create(mail_id=mid)
            if not created:
                continue

            # only create a note if it's hostile
            if not is_mail_row_hostile(m):
                continue

            flags: List[str] = []
            # sender
            if check_char_corp_bl(m['sender_id']):
                flags.append(f"Sender **{m['sender_name']}** is on blacklist")
            if str(m['sender_corporation_id']) in cfg.hostile_corporations:
                flags.append(f"Sender corp **{m['sender_corporation']}** is hostile")
            if str(m['sender_alliance_id']) in cfg.hostile_alliances:
                flags.append(f"Sender alliance **{m['sender_alliance']}** is hostile")
            # recipients
            for idx, rid in enumerate(m.get('recipient_ids', [])):
                name = m['recipient_names'][idx]
                if check_char_corp_bl(rid):
                    flags.append(f"Recipient **{name}** is on blacklist")
                cid = m['recipient_corp_ids'][idx]
                if cid and str(cid) in cfg.hostile_corporations:
                    flags.append(f"Recipient corp **{m['recipient_corps'][idx]}** is hostile")
                aid = m['recipient_alliance_ids'][idx]
                if aid and str(aid) in cfg.hostile_alliances:
                    flags.append(f"Recipient alliance **{m['recipient_alliances'][idx]}** is hostile")
            flags_text = "\n    - ".join(flags)

            note_text = (
                f"- **'{m['subject']}'**: "
                f"\n  - sent {m['sent_date']}; "
                f"\n  - from **{m['sender_name']}**(**{m['sender_corporation']}**/"
                  f"**{m['sender_alliance']}**), "
                f"\n  - flags:\n    - {flags_text}"
            )
            SusMailNote.objects.update_or_create(
                mail=pm,
                defaults={"user_id": user_id, "note": note_text}
            )
            notes[mid] = note_text

    # 5) Fetch *all* notes for this user (new + old)
    for note in SusMailNote.objects.filter(user_id=user_id):
        notes[note.mail.mail_id] = note.note

    return notes