import html

from collections import defaultdict

from django.db import transaction
from typing import List

from ..app_settings import (
    is_npc_corporation,
    get_alliance_history_for_corp,
    resolve_alliance_name,
    resolve_corporation_name,
    get_user_characters,
    is_npc_character,
)
from .corp_blacklist import check_char_corp_bl
from corptools.models import CharacterContact

from ..models import Corporation_names, BigBrotherConfig


def get_user_contacts(user_id: int) -> dict[int, dict]:
    """
    Fetch and filter contacts for a user, excluding NPCs and self-contacts,
    and annotate each with standing, grouping support.
    """
    # 1. Get our chars as a dict { character_id: character_name }
    user_chars = get_user_characters(user_id)
    user_char_ids = set(user_chars.keys())

    # 2. Pull in all CharacterContact rows for those character IDs
    qs = CharacterContact.objects.filter(
        character__character__character_id__in=user_char_ids
    ).select_related('contact_name', 'character__character')

    contacts: dict[int, dict] = {}

    for cc in qs:
        cid = cc.contact_id
        ctype = cc.contact_type

        # skip NPC entries and our own characters
        if ctype == 'npc' or cid in user_char_ids:
            continue

        # skip NPC characters via app filter
        if ctype == 'character' and is_npc_character(cid):
            continue

        if cid not in contacts:
            corp_id = 0
            corp_name = "-"
            alli_id = 0
            alli_name = "-"
            contact_name = "-"

            if ctype == 'character':
                contact_name = cc.contact_name.name

            elif ctype == 'corporation':
                corp_id = cid
                if is_npc_corporation(corp_id):
                    continue
                if corp_id:
                    corp_name = resolve_corporation_name(corp_id)
                    contact_name = corp_name
                    hist = get_alliance_history_for_corp(corp_id)
                    if hist:
                        alli_id = hist[-1]['alliance_id']
                        if alli_id:
                            alli_name = resolve_alliance_name(alli_id)

            elif ctype == 'alliance':
                alli_id = cid
                contact_name = resolve_alliance_name(alli_id)
                alli_name = contact_name

            else:
                contact_name = str(cid)

            contacts[cid] = {
                'contact_type':    ctype,
                'contact_name':    contact_name,
                'characters':      set(),
                'standing':        cc.standing,
                'corporation_name': corp_name,
                'coid': corp_id,
                'alliance_name':    alli_name,
                'aid': alli_id,
            }

        # record which of our chars saw this contact
        host_char_id = cc.character.character.character_id
        contacts[cid]['characters'].add(user_chars[host_char_id])

    # 3. Convert those sets → lists
    for info in contacts.values():
        info['characters'] = list(info['characters'])

    return contacts

def get_cell_style_for_row(cid: int, column: str, row: dict) -> str:
    if column == 'contact_name':
        if check_char_corp_bl(cid):
            return 'color: red;'
        else:
            return ''

    if column == 'standing':
        s = row.get('standing', 0)
        if s >= 6:
            return 'color: darkblue;'
        elif s >= 1:
            return 'color: blue;'
        elif s == 0:
            return 'color: white;'
        elif s >= -5:
            return 'color: orange;'
        else:
            return 'color: #FF0000;'

    if column == 'alliance_name':
        aid = row.get("aid")
        if aid and str(aid) in BigBrotherConfig.get_solo().hostile_alliances:
            return 'color: red;'
        else:
            return ''

    if column == 'corporation_name':
        coid = row.get("coid")
        if coid and str(coid) in BigBrotherConfig.get_solo().hostile_corporations:
            return 'color: red;'
        else:
            return ''

    return ''


def group_contacts_by_standing(contacts: dict[int, dict]) -> dict[int, list[tuple[int, dict]]]:
    buckets = {10: [], 5: [], 0: [], -5: [], -10: []}
    for cid, info in contacts.items():
        s = info.get('standing', 0)
        if s >= 6:
            buckets[10].append((cid, info))
        elif s >= 1:
            buckets[5].append((cid, info))
        elif s == 0:
            buckets[0].append((cid, info))
        elif s >= -5:
            buckets[-5].append((cid, info))
        else:
            buckets[-10].append((cid, info))
    return buckets



def render_contacts(user_id: int) -> str:
    """
    Render the user's contacts into HTML grouped by standing.
    """
    contacts = get_user_contacts(user_id)
    groups = group_contacts_by_standing(contacts)

    if not contacts:
        return '<p>No contacts found.</p>'

    html_parts = ['<div class="contact-groups">']
    for bucket, entries in sorted(groups.items(), reverse=True):
        label = f"Standing {bucket:+d}"
        html_parts.append(f'<h3>{label}</h3>')
        if not entries:
            html_parts.append('<p>No contacts in this category.</p>')
            continue

        # table headers from first entry
        first = entries[0]
        HIDDEN_COLUMNS = {'aid', 'coid'}
        _, first_entry = first
        headers = [h for h in first_entry.keys() if h not in HIDDEN_COLUMNS]
        html_parts.append('<table class="table table-striped">')
        html_parts.append('  <thead>')
        html_parts.append('    <tr>')
        for h in headers:
            html_parts.append(f'      <th>{html.escape(str(h)).replace("_", " ").title()}</th>')
        html_parts.append('    </tr>')
        html_parts.append('  </thead>')
        html_parts.append('  <tbody>')
        for cid, entry in entries:
            html_parts.append('    <tr>')
            for h in headers:
                val = entry.get(h)
                display_val = ', '.join(map(str, val)) if isinstance(val, list) else val
                style = get_cell_style_for_row(cid, h, entry)
                html_parts.append(f'      <td style="{style}">{html.escape(str(display_val))}</td>')
            html_parts.append('    </tr>')
        html_parts.append('  </tbody>')
        html_parts.append('</table>')
    html_parts.append('</div>')

    return '\n'.join(html_parts)




def sus_conta(userID):
    return None

import logging
logger = logging.getLogger(__name__)

def get_user_hostile_notifications(user_id: int) -> dict[int, str]:
    """
    Fetches all contacts for the given user, checks each one against
    the character blacklist, hostile corporations, and hostile alliances,
    and returns a dict of contact_id → notification string for any new hostiles found.
    """
    contacts = get_user_contacts(user_id)
    notifications: dict[int, str] = {}

    # Shortcut to the current hostile lists
    cfg = BigBrotherConfig.get_solo()
    hostile_corps = cfg.hostile_corporations
    hostile_allis = cfg.hostile_alliances
    logger.info(f"{hostile_allis}")

    for cid, info in contacts.items():
        ctype     = info['contact_type']      # 'character' | 'corporation' | 'alliance'
        cname     = info['contact_name']
        chars     = info.get('characters', set())
        coid      = info.get('coid')          # corporation ID (int or None)
        corp_name = info.get('corporation_name')
        aid       = info.get('aid')           # alliance ID (int or None)
        alli_name = info.get('alliance_name')
        s         = info.get('standing', 0)

        alerts: list[str] = []
        logger.info(f"{cname},{aid},{alli_name}")

        # 1) Character-specific blacklist check
        if ctype == 'character' and check_char_corp_bl(cid):
            alerts.append(f"**{cname}** is on blacklist")

        # 2) Hostile corporation check (characters & corporations)
        if ctype in ('character', 'corporation') and coid is not 0:
            if str(coid) in hostile_corps:
                alerts.append(f"corporation **{corp_name}** is on hostile list")

        # 3) Hostile alliance check (characters, corporations & alliances)
        if aid is not 0 and str(aid) in hostile_allis:
            alerts.append(f"alliance **{alli_name}** is on hostile list")

        if alerts:
            char_list = ', '.join(sorted(chars)) if chars else 'no characters'
            # Build a single notification string
            message = (
                f"- A {s} **{ctype}** type contact **{cname}** found on **{char_list}**, flags: "
                + "; ".join(alerts)
            )
            notifications[cid] = message

    return notifications
