from allianceauth.authentication.models import CharacterOwnership
from corptools.models import CharacterAudit, CharacterAsset
from .skills import get_user_skill_info, get_char_age
from ..app_settings import get_user_characters, format_int, get_character_id
from django.utils.html import format_html
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

skill_ids = {
    "cyno":   21603,  # Cynosural Field Theory
    "recon":  22761,  # Recon Ships
    "hic":    28609,  # Heavy Interdiction Cruisers
    "blops":  28656,  # Black Ops
    "covops": 12093,  # Covert Ops
}

def get_user_cyno_info(user_id: int) -> dict:
    """
    Given an AllianceAuth user ID, returns for each of that user's characters:
      - s_<skill>: 1 if trained_skill_level >= required_levels[skill] else 0
      - i_<skill>: 1 if active_skill_level  >= required_levels[skill] else 0 (except for cyno where only s_cyno)

    `required_levels` is an optional dict mapping skill keys ("cyno", "recon", etc.) to the minimum trained/active level required.
    If not provided, defaults to 1 for all skills.
    """
    # default required levels
    required_levels = {
        "cyno":   1,  # Cynosural Field Theory
        "recon":  1,  # Recon Ships
        "hic":    1,  # Heavy Interdiction Cruisers
        "blops":  1,  # Black Ops
        "covops": 1,  # Covert Ops
    }

    # 1) grab all of this user's owned characters
    ownership_map = get_user_characters(user_id)
    logger.info(f"ownership:{str(ownership_map)}")
    # 2) pre-fetch audits for only those characters
    audits = (
        CharacterAudit.objects
        .filter(character__character_id__in=ownership_map.keys())
    )
    logger.info(f"audits:{str(audits)}")

    # 3) fetch each skill once
    skill_data = {
        key: get_user_skill_info(user_id, skill_id)
        for key, skill_id in skill_ids.items()
    }
    logger.info(str(skill_data))

    result = {}

    for audit in audits:
        name = ownership_map[audit.character.character_id]
        logger.info(name)
        cid = get_character_id(name)
        age = get_char_age(cid)
        i_recon = owns_items_in_group(cid, 833)
        i_hic = owns_items_in_group(cid, 894)
        i_blops = owns_items_in_group(cid, 898)
        i_covops = owns_items_in_group(cid, 830)

        # initialize all flags to 0
        char_dic = {
            "s_cyno":  0,
            "s_recon": 0,
            "s_hic":   0,
            "s_blops": 0,
            "s_covops":0,
            "i_recon": i_recon,
            "i_hic":   i_hic,
            "i_blops": i_blops,
            "i_covops":i_covops,
            "age":     age,
            "can_light": False,
        }

        # set flags based on required_levels
        for key, data in skill_data.items():
            lvl_req = required_levels.get(key, 1)
            info = data.get(name, {"trained_skill_level": 0, "active_skill_level": 0})

            # s_<skill>
            if info["trained_skill_level"] >= lvl_req:
                char_dic[f"s_{key}"] = 1
            if info["active_skill_level"] >= lvl_req:
                char_dic[f"s_{key}"] = 2
        if char_dic[f"s_cyno"] > 0 and char_dic[f"s_recon"] > 0 and char_dic[f"i_recon"] == True:
            char_dic[f"can_light"] = True
        if char_dic[f"s_cyno"] > 0 and char_dic[f"s_hic"] > 0 and char_dic[f"i_hic"] == True:
            char_dic[f"can_light"] = True
        if char_dic[f"s_cyno"] > 0 and char_dic[f"s_blops"] > 0 and char_dic[f"i_blops"] == True:
            char_dic[f"can_light"] = True
        if char_dic[f"s_cyno"] > 0 and char_dic[f"s_covops"] > 0 and char_dic[f"i_covops"] == True:
            char_dic[f"can_light"] = True
        result[name] = char_dic

    return result


def owns_items_in_group(cid, gid):
# Query assets for this character filtered by group
    exists = CharacterAsset.objects.filter(
        character__character__character_id=cid,
        type_name__group_id=gid
    ).exists()

    return exists


def render_user_cyno_info_html(user_id: int) -> str:
    """
    Returns an HTML snippet showing, for each of the user's characters:
      - for each skill: whether they can use it (no / yes but alpha / yes) and whether they own ships in that group
      - whether they can light cynos (can_light)
      - character age
    """
    data = get_user_cyno_info(user_id)
    html = ""

    for char_name, info in data.items():
        # header
        html += format_html("<h3>{}</h3>", char_name)

        # table start
        html += """
        <table class="table table-striped">
          <thead>
            <tr>
              <th>Skill</th><th>Can use</th><th>Owns ships</th>
            </tr>
          </thead>
          <tbody>
        """

        # loop through each skill
        for key, label in (
            ("cyno",   "Cynosural Field"),
            ("recon",  "Recon"),
            ("hic",    "HIC"),
            ("blops",  "Black Ops"),
            ("covops", "Covert Ops"),
        ):
            s = info[f"s_{key}"]
            # map trained/active flag to human text
            if s == 0:
                s_txt = "no"
            elif s == 1:
                s_txt = "yes but alpha"
            else:  # s == 2
                s_txt = "yes"
            # only cyno has no “owns” flag
            owns = info.get(f"i_{key}", "")
            html += format_html(
                "<tr><td>{}</td><td>{}</td><td>{}</td></tr>",
                label, s_txt, owns
            )

        # add the “can light?” and age rows
        html += format_html(
            "<tr><td>Can light?</td><td colspan='2'>{}</td></tr>",
            info["can_light"]
        )
        html += format_html(
            "<tr><td>Age</td><td colspan='2'>{}</td></tr>",
            info["age"]
        )

        # table end
        html += "</tbody></table>"

    return format_html(html)

def cyno(user_id):
    return False