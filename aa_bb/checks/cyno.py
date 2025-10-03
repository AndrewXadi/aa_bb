from allianceauth.authentication.models import CharacterOwnership
from corptools.models import CharacterAudit, CharacterAsset
from .skills import get_user_skill_info, get_char_age
from ..app_settings import get_user_characters, format_int, get_character_id
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from .corp_changes import get_current_stint_days_in_corp
import logging
from aa_bb.models import BigBrotherConfig as bbc

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

skill_ids = {
    "cyno":     21603,  # Cynosural Field Theory
    "recon":    22761,  # Recon Ships
    "hic":      28609,  # Heavy Interdiction Cruisers
    "blops":    28656,  # Black Ops
    "covops":   12093,  # Covert Ops
    "brun":     19719,  # Blockade Runners
    "sbomb":    12093,  # Stealth Bombers
    "calscru":  30651,  # Stategic Cruisers
    "galscru":  30652,  # Stategic Cruisers
    "minscru":  30653,  # Stategic Cruisers
    "amascru":  30650,  # Stategic Cruisers
    "expfrig":  33856,  # Expedition Frigates
    "acarrier": 24311,
    "ccarrier": 24312,
    "gcarrier": 24313,
    "mcarrier": 24314,
    "adread":   20525,
    "cdread":   20530,
    "gdread":   20531,
    "mdread":   20532,
    "tdread":   52997,
    "atitan":   3347,
    "ctitan":   3346,
    "gtitan":   3344,
    "mtitan":   3345,
    "ajf":      20524,
    "cjf":      20526,
    "gjf":      20527,
    "mjf":      20528,
    "jf":       29029,
    "rorq":     28374,
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
        "cyno":      1,  # Cynosural Field Theory
        "recon":     1,  # Recon Ships
        "hic":       1,  # Heavy Interdiction Cruisers
        "blops":     1,  # Black Ops
        "covops":    1,  # Covert Ops
        "brun":      1,  # Blockade Runners
        "sbomb":     1,  # Stealth Bombers
        "calscru":   1,  # Stategic Cruisers
        "galscru":   1,  # Stategic Cruisers
        "minscru":   1,  # Stategic Cruisers
        "amascru":   1,  # Stategic Cruisers
        "expfrig":   1,  # Expedition Frigates
        "acarrier":  1,
        "ccarrier":  1,
        "gcarrier":  1,
        "mcarrier":  1,
        "adread":    1,
        "cdread":    1,
        "gdread":    1,
        "mdread":    1,
        "tdread":    1,
        "atitan":    1,
        "ctitan":    1,
        "gtitan":    1,
        "mtitan":    1,
        "ajf":       4,
        "cjf":       4,
        "gjf":       4,
        "mjf":       4,
        "jf":        1,
        "rorq":      1,
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
        i_brun = owns_items_in_group(cid, 1202)
        i_sbomb = owns_items_in_group(cid, 834)
        i_scru = owns_items_in_group(cid, 963)
        i_expfrig = owns_items_in_group(cid, 1283)
        i_carrier = owns_items_in_group(cid, 547)
        i_dread = owns_items_in_group(cid, 485)
        i_fax = owns_items_in_group(cid, 1538)
        i_super = owns_items_in_group(cid, 659)
        i_titan = owns_items_in_group(cid, 30)
        i_jf = owns_items_in_group(cid, 902)
        i_rorq = owns_items_in_group(cid, 883)

        # initialize all flags to 0
        char_dic = {
            "s_cyno":    0,
            "s_cov_cyno":0,
            "s_recon":   0,
            "s_hic":     0,
            "s_blops":   0,
            "s_covops":  0,
            "s_brun":    0,
            "s_sbomb":   0,
            "s_scru":    0,
            "s_expfrig": 0,
            "s_carrier": 0,
            "s_dread":   0,
            "s_fax":     0,
            "s_super":   0,
            "s_titan":   0,
            "s_jf":      0,
            "s_rorq":    0,
            "i_recon":   i_recon,
            "i_hic":     i_hic,
            "i_blops":   i_blops,
            "i_covops":  i_covops,
            "i_brun":    i_brun,  
            "i_sbomb":   i_sbomb,
            "i_scru":    i_scru,
            "i_expfrig": i_expfrig,
            "i_carrier": i_carrier,
            "i_dread": i_dread,
            "i_fax": i_fax,
            "i_super": i_super,
            "i_titan": i_titan,
            "i_jf": i_jf,
            "i_rorq": i_rorq,
            "age":       age,
            "can_light": False,
        }
        jfff = 0

        # set flags based on required_levels
        for key, data in skill_data.items():
            lvl_req = required_levels.get(key, 1)
            info = data.get(name, {"trained_skill_level": 0, "active_skill_level": 0})

            # s_<skill>
            if key == "jf":
                if info["trained_skill_level"] >= lvl_req:
                    jfff = 1
                if info["active_skill_level"] >= lvl_req:
                    jfff = 2
            if key == "acarrier" or key == "ccarrier" or key == "gcarrier" or key == "mcarrier":
                if info["trained_skill_level"] >= lvl_req:
                    char_dic[f"s_carrier"] = 1
                    char_dic[f"s_super"] = 1
                    char_dic[f"s_fax"] = 1
                if info["active_skill_level"] >= lvl_req:
                    char_dic[f"s_carrier"] = 2
                    char_dic[f"s_super"] = 2
                    char_dic[f"s_fax"] = 2
            if key == "adread" or key == "cdread" or key == "gdread" or key == "mdread" or key == "tdread":
                if info["trained_skill_level"] >= lvl_req:
                    char_dic[f"s_dread"] = 1
                if info["active_skill_level"] >= lvl_req:
                    char_dic[f"s_dread"] = 2
            if key == "atitan" or key == "ctitan" or key == "gtitan" or key == "mtitan":
                if info["trained_skill_level"] >= lvl_req:
                    char_dic[f"s_titan"] = 1
                if info["active_skill_level"] >= lvl_req:
                    char_dic[f"s_titan"] = 2
            if key == "ajf" or key == "cjf" or key == "gjf" or key == "mjf":
                if info["trained_skill_level"] >= lvl_req and jfff == 1:
                    char_dic[f"s_jf"] = 1
                if info["active_skill_level"] >= lvl_req and jfff == 2:
                    char_dic[f"s_jf"] = 2
            if key == "rorq":
                if info["trained_skill_level"] >= lvl_req:
                    char_dic[f"s_rorq"] = 1
                if info["active_skill_level"] >= lvl_req:
                    char_dic[f"s_rorq"] = 2
            if key == "calscru" or key == "amascru" or key == "galscru" or key == "minscru":
                if info["trained_skill_level"] >= lvl_req:
                    char_dic[f"s_scru"] = 1
                if info["active_skill_level"] >= lvl_req:
                    char_dic[f"s_scru"] = 2
            elif key == "cyno":
                if info["trained_skill_level"] >= lvl_req:
                    char_dic[f"s_{key}"] = 1
                if info["active_skill_level"] >= lvl_req:
                    char_dic[f"s_{key}"] = 2
                if info["trained_skill_level"] == 5:
                    char_dic[f"s_cov_{key}"] = 1
                if info["active_skill_level"] == 5:
                    char_dic[f"s_cov_{key}"] = 2
            else:
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
        if char_dic[f"s_cov_cyno"] > 0 and char_dic[f"s_covops"] > 0 and char_dic[f"i_covops"] == True:
            char_dic[f"can_light"] = True
        if char_dic[f"s_cov_cyno"] > 0 and char_dic[f"s_brun"] > 0 and char_dic[f"i_brun"] == True:
            char_dic[f"can_light"] = True
        if char_dic[f"s_cov_cyno"] > 0 and char_dic[f"s_sbomb"] > 0 and char_dic[f"i_sbomb"] == True:
            char_dic[f"can_light"] = True
        if char_dic[f"s_cov_cyno"] > 0 and char_dic[f"s_scru"] > 0 and char_dic[f"i_scru"] == True:
            char_dic[f"can_light"] = True
        if char_dic[f"s_cov_cyno"] > 0 and char_dic[f"s_expfrig"] > 0 and char_dic[f"i_expfrig"] == True:
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
              <th>Name</th><th>Can use</th><th>Owns ships</th>
            </tr>
          </thead>
          <tbody>
        """

        # loop through each skill
        for key, label in (
            ("cyno",     "Cynosural Field"),
            ("cov_cyno", "Cynosural Field 5"),
            ("recon",    "Recon"),
            ("hic",      "HIC"),
            ("blops",    "Black Ops"),
            ("covops",   "Covert Ops"),
            ("brun",     "Blockade Runners"),
            ("sbomb",    "Stealth Bombers"),
            ("scru",     "Stategic Cruisers"),
            ("expfrig",  "Exploration Frigates"),
            ("carrier",  "Carriers"),
            ("dread",    "Dreads"),
            ("fax",      "FAXes"),
            ("super",    "Supers"),
            ("titan",    "Titans"),
            ("jf",       "Jump Freighters"),
            ("rorq",     "Rorquals"),
        ):
            s = info[f"s_{key}"]
            # map trained/active flag to human text
            if s == 0:
                s_txt = "False"
            elif s == 1:
                s_txt = mark_safe('<span style="color:orange;">True (but alpha)</span>')
            else:  # s == 2
                s_txt = mark_safe('<span style="color:red;">True</span>')
            # only cyno has no “owns” flag
            if info.get(f"i_{key}", "") == True:
                owns = mark_safe(f'<span style="color:red;">{info.get(f"i_{key}", "")}</span>')
            else:
                owns = f'{info.get(f"i_{key}", "")}'
            html += format_html(
                "<tr><td>{}</td><td>{}</td><td>{}</td></tr>",
                label, s_txt, owns
            )

        # add the “can light?” and age rows
        if info["can_light"] == True:
            can_light = mark_safe(f'<span style="color:red;">{info["can_light"]}</span>')
        else:
            can_light = f'{info["can_light"]}'
        if info["age"] < 90:
            age = mark_safe(f'<span style="color:red;">{info["age"]}</span>')
        else:
            age = f'{info["age"]}'
        html += format_html(
            "<tr><td>Can light?</td><td colspan='2'>{}</td></tr>",
            can_light
        )
        html += format_html(
            "<tr><td>Age</td><td colspan='2'>{}</td></tr>",
            age
        )
        cid = get_character_id(char_name)
        corp_label = f"Time in {bbc.get_solo().main_corporation}"
        days_in_corp = get_current_stint_days_in_corp(cid,bbc.get_solo().main_corporation_id)
        days_html = f"{days_in_corp} days"

        html += format_html(
            "<tr><td>{}</td><td colspan='2'>{}</td></tr>",
            corp_label, days_html
        )

        # table end
        html += "</tbody></table>"

    return format_html(html)

def cyno(user_id):
    return False