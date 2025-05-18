from django.contrib.auth.models import User
from allianceauth.authentication.models import CharacterOwnership
from corptools.models import CharacterAudit, Skill, SkillTotals, CorporationHistory
from django.utils.html import format_html
from ..app_settings import get_user_characters, format_int, get_character_id
import logging
import json
import os
from typing import Dict
from django.utils.safestring import mark_safe
from django.utils import timezone

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
_SKILLS_JSON_PATH = os.path.join(os.path.dirname(__file__), "skills.json")

skill_ids = [
    3426,   # CPU Management
    21603,  # Cynosural Field Theory
    22761,  # Recon Ships
    28609,  # Heavy Interdiction Cruisers
    28656,  # Black Ops
    12093,  # Covert Ops
    20533,  # Capital Ships
]

def get_skill_map():
    try:
        with open(_SKILLS_JSON_PATH, "r", encoding="utf-8") as f:
            _raw = json.load(f)
    except (OSError, ValueError) as e:
        # Fallback to an empty mapping if the file is missing or invalid.
        _raw = {}
    # Flatten into { skill_id: skill_name }
    skill_name_map: Dict[int, str] = {}
    for category, blocks in _raw.items():
        for block in blocks:
            for key, val in block.items():
                # Skip the “Category ID” entries
                if key == "Category ID":
                    continue
                try:
                    sid = int(key)
                except ValueError:
                    continue
                skill_name_map[sid] = val
    return skill_name_map

def get_user_skill_info(user_id: int, skill_id: int) -> dict:
    """
    Given an AllianceAuth user ID and an EVE skill ID, returns a dict
    mapping each of that user's characters to a sub‐dict containing:
      - trained_skill_level
      - active_skill_level
      - total_sp (total skillpoints on the character)

    Characters without the given skill will show levels = 0.
    """
    # 1) grab all of this user's owned characters
    ownerships = CharacterOwnership.objects.filter(user_id=user_id)
    # Map EVE character ID → ownership record (for name lookup)
    ownership_map = {co.character_id: co for co in ownerships}

    # 2) fetch audits only for those character IDs
    audits = (
        CharacterAudit.objects
        .filter(character_id__in=ownership_map.keys())
        .select_related("skilltotals")
        .prefetch_related("skill_set")
    )

    result = {}
    for audit in audits:
        co = ownership_map[audit.character_id]
        char_name = co.character_name

        # Try to pull the desired skill; if missing, default to zeros
        try:
            skill = audit.skill_set.get(skill_id=skill_id)
            trained = skill.trained_skill_level
            active  = skill.active_skill_level
        except Skill.DoesNotExist:
            trained = 0
            active  = 0

        totals = audit.skilltotals  # SkillTotals instance

        result[char_name] = {
            "trained_skill_level": trained,
            "active_skill_level":  active,
            "total_sp":            totals.total_sp,
        }

    return result


def get_multiple_user_skill_info(user_id: int, skill_ids: list[int]) -> dict[str, dict]:
    """
    Returns a dict mapping each of the user's characters (by name) to:
      - total_sp
      - for each skill_id in skill_ids:
          - trained:    trained_skill_level (or 0 if missing)
          - active:     active_skill_level (or 0 if missing)
    """
    # 1) Load all characters owned by this user
    ownership_map = get_user_characters(user_id)
    logger.info(f"ownership: {len(ownership_map)}")
    logger.info(f"ownership: {str(ownership_map)}")

    if not ownership_map:
        return {}

    # 2) Fetch audits and related totals and skills in bulk
    audits = (
        CharacterAudit.objects
        .filter(character__character_id__in=ownership_map.keys())             # single SQL WHERE IN :contentReference[oaicite:1]{index=1}
        .select_related("skilltotals")                              # JOIN to grab SkillTotals in one query :contentReference[oaicite:2]{index=2}
        .prefetch_related("skill_set")                             # prefetch all Skill rows per character :contentReference[oaicite:3]{index=3}
    )
    logger.info(f"audits: {len(audits)}")

    result: dict[str, dict] = {}

    # 3) Build the output dict
    for audit in audits:
        name = ownership_map[audit.character.character_id]
        totals = audit.skilltotals

        # Gather this character's skills into a lookup by skill_id
        skill_lookup = {
            s.skill_id: s
            for s in audit.skill_set.all()
            if s.skill_id in skill_ids
        }

        # Start with total SP
        entry: dict = {"total_sp": totals.total_sp}

        # Attach each requested skill’s levels
        for sid in skill_ids:
            skill = skill_lookup.get(sid)
            entry[sid] = {
                "trained": skill.trained_skill_level if skill else 0,
                "active":  skill.active_skill_level   if skill else 0,
            }

        result[name] = entry

    return result

def render_user_skills_html(user_id: int) -> str:
    """
    Generates an HTML block containing:
      - <h3>Character Name (total SP)</h3>
      - <table> of skill levels (trained vs. active) for each skill_id in skill_ids
    
    No external links are included.
    """
    skill_name_map = get_skill_map()
    # 1) Fetch all characters’ skill info in one go
    data = get_multiple_user_skill_info(user_id, skill_ids)
    # data is: { "CharName": { "total_sp": int, skill_id: {"trained": int, "active": int}, ... }, ... }
    logger.info(len(data))
    html_parts = []
    for char_name, info in data.items():
        total_sp = info.get("total_sp", 0)
        char_id = get_character_id(char_name)
        char_age = get_char_age(char_id)
        sp_days = total_sp/64800
        sp_age_ratio = round(sp_days / char_age, 2)
        formatted = mark_safe(f'<span style="color:red;">{sp_age_ratio}</span>')
        ratio = sp_age_ratio if sp_age_ratio < 1 else formatted
        # Header with total SP next to name
        html_parts.append(format_html("<h3>{} (<b>{}</b> SP / <b>{}</b> days old / SP to Age ratio: <b>{}</b>)</h3>", char_name, format_int(total_sp), char_age, ratio))

        # Build table header
        html_parts.append(
            '<table class="table table-striped">'
            '<thead><tr>'
            '<th>Skill</th>'
            '<th>Trained Level</th>'
            '<th>Active Level</th>'
            '</tr></thead>'
            '<tbody>'
        )

        # One row per skill_id, in the order of our global list
        for sid in skill_ids:
            levels = info.get(sid, {"trained": 0, "active": 0})
            trained = levels["trained"]
            active = levels["active"]
            style_t = ''
            style_a = ''
            skill_name = skill_name_map.get(sid, str(sid))
            if trained > 0 and sid != 3426 or trained > 3:
                style_t = ' style="color:red;"'
            if active > 0 and sid != 3426 or active > 3:
                style_a = ' style="color:red;"'
            html_parts.append(format_html(
                "<tr>"
                "<td>{skill}</td>"
                "<td {t_attr}>{t_val}</td>"
                "<td {a_attr}>{a_val}</td>"
                "</tr>",
                skill=skill_name,
                t_attr=mark_safe(style_t),
                t_val=trained,
                a_attr=mark_safe(style_a),
                a_val=active,
            ))

        # Close table
        html_parts.append("</tbody></table>")

    # Join everything into one HTML-safe string
    return format_html("".join(html_parts))

def get_char_age(char_id: int) -> int | None:
    """
    Returns the age in days of the character with the given EVE character ID,
    based on the first recorded CorporationHistory.start_date.
    If the character audit or history is missing, returns None.
    """
    try:
        # 1) Find the audit record for this EVE character ID
        audit = CharacterAudit.objects.get(
            character__character_id=char_id
        )
    except CharacterAudit.DoesNotExist:
        return None

    # 2) Get the earliest corp history entry for that audit
    first_hist = (
        CorporationHistory.objects
        .filter(character=audit)
        .order_by('start_date')    # ORDER BY start_date ASC :contentReference[oaicite:0]{index=0}
        .first()
    )
    if not first_hist:
        return None

    # 3) Compute the difference between now() and that start_date
    delta = timezone.now() - first_hist.start_date  # uses aware datetime :contentReference[oaicite:1]{index=1}

    # 4) Return the number of days
    return delta.days