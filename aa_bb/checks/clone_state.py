from .skills import get_user_skill_info
from aa_bb.modelss import CharacterAccountState
from aa_bb.app_settings import resolve_character_name, get_user_characters
from django.db import transaction
from django.utils.html import format_html, mark_safe
import json
import os
import logging

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def determine_character_state(user_id, save: bool = False):
    alpha_skills_file = os.path.join(BASE_DIR, "alpha_skills.json")
    all_skills_file = os.path.join(BASE_DIR, "skills.json")

    # Load alpha skill caps
    with open(alpha_skills_file, "r") as f:
        alpha_skills = json.load(f)
    alpha_caps = {skill["id"]: skill["cap"] for skill in alpha_skills}

    # Load all skills
    with open(all_skills_file, "r") as f:
        all_skills_data = json.load(f)
    all_skill_ids = set()
    for category, entries in all_skills_data.items():
        if len(entries) < 2:
            continue
        skill_map = entries[1]
        for skill_id_str in skill_map:
            all_skill_ids.add(int(skill_id_str))

    # Load DB records first
    char_db_records = {
        rec.char_id: rec for rec in CharacterAccountState.objects.all()
    }
    #logger.info(f"char_db_records: {str(char_db_records)}")
    all_char_ids = get_user_characters(user_id)
    #logger.info(f"all_char_ids: {str(all_char_ids)}")

    result = {}
    skill_cache = {}  # skill_id -> {char_id: skill_data}

    # Helper to get skill info and cache it
    def get_skill_info_cached(skill_id):
        if skill_id not in skill_cache:
            skill_cache[skill_id] = get_user_skill_info(user_id, skill_id)
        return skill_cache[skill_id]

    for char_id in all_char_ids:
        char_name = resolve_character_name(char_id)
        #logger.info(f"char_id: {str(char_id)}")
        state = None
        skill_used = None

        db_record = char_db_records.get(char_id)

        # 1. Check DB skill first
        if db_record and db_record.skill_used:
            skill_id = db_record.skill_used
            skill_data_all_chars = get_skill_info_cached(skill_id)
            skill_data = skill_data_all_chars.get(char_id, {})
            trained = skill_data.get("trained_skill_level", 0)
            active = skill_data.get("active_skill_level", 0)

            if active > alpha_caps.get(skill_id, 5):
                state = "omega"
            elif trained > active:
                state = "alpha"

            if state:
                skill_used = skill_id

        # 2. Check alpha skills if state still unknown
        if state is None:
            for skill in alpha_skills:
                skill_id = skill["id"]
                cap = skill["cap"]
                skill_data_all_chars = get_skill_info_cached(skill_id)
                skill_data = skill_data_all_chars.get(char_name, {})
                trained = skill_data.get("trained_skill_level", 0)
                active = skill_data.get("active_skill_level", 0)

                if active > cap:
                    state = "omega"
                    skill_used = skill_id
                    break
                elif trained > active:
                    state = "alpha"
                    skill_used = skill_id
                    break

        # 3. Check remaining skills only if still unknown
        if state is None:
            remaining_skill_ids = all_skill_ids - set(alpha_caps.keys())
            for skill_id in remaining_skill_ids:
                skill_data_all_chars = get_skill_info_cached(skill_id)
                skill_data = skill_data_all_chars.get(char_name, {})
                trained = skill_data.get("trained_skill_level", 0)
                active = skill_data.get("active_skill_level", 0)

                if trained > active:
                    state = "alpha"
                    skill_used = skill_id
                    break


        if state is None:
            state = "unknown"
            skill_used = None

        last_state = db_record.state if db_record else None
        result[char_id] = {
            "state": state,
            "skill_used": skill_used,
            "last_state": last_state,
        }

        # Save or update DB record
        if save:
            with transaction.atomic():
                CharacterAccountState.objects.update_or_create(
                    char_id=char_id,
                    defaults={"state": state, "skill_used": skill_used}
                )
    del skill_cache
    return result

def render_character_states_html(user_id: int) -> str:
    """
    Returns an HTML snippet showing, for each of the user's characters:
      - the current state (alpha/omega/unknown)
    as a single table with columns Character | State
    """
    data = determine_character_state(user_id)
    #logger.info(f"data: {str(data)}")

    html = """
    <table class="table table-striped">
      <thead>
        <tr>
          <th>Character</th>
          <th>State</th>
        </tr>
      </thead>
      <tbody>
    """

    for char_id, info in data.items():
        char_name = resolve_character_name(char_id)

        # state formatting
        state_val = info.get("state", "unknown")
        if state_val == "omega":
            state_val_html = mark_safe('<span style="color:red;">Omega</span>')
        elif state_val == "alpha":
            state_val_html = mark_safe('<span style="color:green;">Alpha</span>')
        else:
            state_val_html = "Unknown"

        html += format_html("<tr><td>{}</td><td>{}</td></tr>", char_name, state_val_html)

    html += "</tbody></table>"
    return format_html(html)
