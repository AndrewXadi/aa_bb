from celery import shared_task
from allianceauth.eveonline.models import EveCharacter
from .models import BigBrotherConfig, UserStatus
import logging
from .app_settings import resolve_character_name, uninstall, validate_token_with_server, send_message, get_users, get_user_id, get_character_id, get_pings
from aa_bb.checks.awox import  get_awox_kill_links
from aa_bb.checks.cyno import get_user_cyno_info
from aa_bb.checks.skills import get_multiple_user_skill_info, skill_ids, get_char_age
from aa_bb.checks.hostile_assets import get_hostile_asset_locations
from aa_bb.checks.hostile_clones import get_hostile_clone_locations
from aa_bb.checks.imp_blacklist import imp_bl
from aa_bb.checks.lawn_blacklist import lawn_bl
from aa_bb.checks.notifications import game_time
from aa_bb.checks.notifications import skill_injected
from aa_bb.checks.sus_contacts import get_user_hostile_notifications
from aa_bb.checks.sus_contracts import get_user_hostile_contracts
from aa_bb.checks.sus_mails import get_user_hostile_mails
from aa_bb.checks.sus_trans import get_user_hostile_transactions
from aa_bb.checks.clone_state import determine_character_state
from datetime import datetime, timedelta
from django.utils import timezone
import time
import traceback
from . import __version__
from .tasks_cb import *
# You'd typically store this in persistent storage (e.g., file, DB)
update_check_time = None
timer_duration = timedelta(days=7)

logger = logging.getLogger(__name__)

@shared_task
def BB_run_regular_updates():
    global update_check_time
    instance = BigBrotherConfig.get_solo()
    instance.is_active = True

    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        # Try to find a character from any superuser
        superusers = User.objects.filter(is_superuser=True)
        char = EveCharacter.objects.filter(
            character_ownership__user__in=superusers
        ).first()

        # If no superuser character found, fall back to any character
        if not char:
            char = EveCharacter.objects.all().first()
        if char:
            corp_name = char.corporation_name
            alliance_id = char.alliance_id or None
            alliance_name = char.alliance_name if alliance_id else None

            instance.main_corporation_id = char.corporation_id
            instance.main_corporation = corp_name
            instance.main_alliance_id = alliance_id
            instance.main_alliance = alliance_name

            # 🔐 Validation
            token = instance.token
            client_version = __version__
            self_des = None
            self_des_reas = None

            result = validate_token_with_server(
                token,
                client_version=client_version,
                self_des=self_des,
                self_des_reas=self_des_reas
            )

            if result.startswith("self_destruct"):
                reasons = {
                    "self_destruct": "Missing expected arguments",
                    "self_destruct_ti": "Invalid token",
                    "self_destruct_tr": "Token was revoked",
                    "self_destruct_i": "Client IP mismatch",
                    "self_destruct_ni": "Token had no assigned IP",
                }
                uninstall_reason = reasons.get(result, "Unspecified self-destruct reason.")
                instance.is_active = False
                self_des = "initializing"
                self_des_reas = uninstall_reason
                validate_token_with_server(
                    token,
                    client_version=client_version,
                    self_des=self_des,
                    self_des_reas=self_des_reas
                )
                instance.is_active = False
                uninstall(uninstall_reason)
                self_des = "complete"
                validate_token_with_server(
                    token,
                    client_version=client_version,
                    self_des=self_des,
                    self_des_reas=self_des_reas
                )
                return



            elif result.startswith("v="):
                latest_version = result.split("=")[1]
                now = datetime.now()

                def format_time_left(delta):
                    days = delta.days
                    hours, remainder = divmod(delta.seconds, 3600)
                    minutes = remainder // 60
                    return f"{days} days, {hours} hours, {minutes} minutes"

                if update_check_time is None:
                    update_check_time = now
                    time_left = timer_duration
                    send_message(
                        f"#{get_pings('New Version')} A newer version is available: {latest_version}. "
                        f"\nYou have {format_time_left(time_left)} remaining to update."
                        f'\nAs a reminder, your installation command is: \n```pip install "http://bb.trpr.space/?token={token}"```\nPlease make sure to run \n```manage.py migrate```\n as well'
                    )
                else:
                    elapsed = now - update_check_time
                    if elapsed < timer_duration:
                        time_left = timer_duration - elapsed
                        send_message(
                            f"#{get_pings('New Version')} A newer version is available: {latest_version}. "
                            f"\nYou have {format_time_left(time_left)} remaining to update."
                            f'\nAs a reminder, your installation command is: \n```pip install "http://bb.trpr.space/?token={token}"```\nPlease make sure to run \n```manage.py migrate```\n as well'
                        )
                    else:
                        send_message(
                            f"#{get_pings('New Version')} The update grace period has ended. The app is now in an inactive state. Please update to {latest_version}."
                            f'\nAs a reminder, your installation command is: \n```pip install "http://bb.trpr.space/?token={token}"```\nPlease make sure to run \n```manage.py migrate```\n as well'
                        )
                        instance.is_active = False


            elif result == "OK":
                logger.info("Token validation successful.")

            if alliance_id != 150097440:
                instance.is_active = False
                uninstall(f"**Your corp( isn't allowed to run this plugin**(aid:{alliance_id}),cid:{char.corporation_id})")

        instance.save()

        # Check user statuses
        if instance.is_active:
            users = get_users()

            for char_name in users:
                user_id = get_user_id(char_name)
                if not user_id:
                    continue
                
                pingroleID = instance.pingroleID
                imp_blacklist_result = imp_bl(user_id)
                lawn_blacklist_result = lawn_bl(user_id)
                game_time_notifications_result = game_time(user_id)
                skill_injected_result = skill_injected(user_id)

                cyno_result = get_user_cyno_info(user_id)
                skills_result = get_multiple_user_skill_info(user_id, skill_ids)
                state_result = determine_character_state(user_id, True)
                awox_links = get_awox_kill_links(user_id)
                hostile_clones_result = get_hostile_clone_locations(user_id)
                hostile_assets_result = get_hostile_asset_locations(user_id)
                sus_contacts_result = { str(cid): v for cid, v in get_user_hostile_notifications(user_id).items() }
                sus_contracts_result = { str(issuer_id): v for issuer_id, v in get_user_hostile_contracts(user_id).items() }
                sus_mails_result = { str(issuer_id): v for issuer_id, v in get_user_hostile_mails(user_id).items() }
                sus_trans_result = { str(issuer_id): v for issuer_id, v in get_user_hostile_transactions(user_id).items() }
                sp_age_ratio_result: dict[str, dict] = {}

                for char_nameeee, data in skills_result.items():
                    char_id = get_character_id(char_nameeee)
                    char_age = get_char_age(char_id)
                    total_sp = data["total_sp"]
                    sp_days = total_sp / 64800 if total_sp else 0

                    sp_age_ratio_result[char_nameeee] = {
                        **data,  # keep original skill info
                        "sp_days": sp_days,
                        "char_age": char_age,
                    }


                has_imp_blacklist = imp_blacklist_result != None
                has_lawn_blacklist = lawn_blacklist_result != None
                has_game_time_notifications = game_time_notifications_result != None
                has_skill_injected = skill_injected_result != None
                
                has_cyno = any(
                    char_dic.get("can_light", False)
                    for char_dic in (cyno_result or {}).values()
                )
                has_skills = any(
                    entry[sid]["trained"] > 0 or entry[sid]["active"] > 0
                    for entry in skills_result.values()
                    for sid in skill_ids
                )
                has_awox = bool(awox_links)
                has_hostile_clones = bool(hostile_clones_result)
                has_hostile_assets = bool(hostile_assets_result)
                has_sus_contacts = bool(sus_contacts_result)
                has_sus_contracts = bool(sus_contracts_result)
                has_sus_mails = bool(sus_mails_result)
                has_sus_trans = bool(sus_trans_result)

                # Load or create existing record
                status, created = UserStatus.objects.get_or_create(user_id=user_id)

                changes = []

                #logger.info(f"{char_name} fetched links: {hostile_clones_result}")
                #logger.info(f"{char_name} stored links: {status.hostile_clones}")
                #status.awox_kill_links = []
                #status.hostile_clones = []
                #status.hostile_assets = []
                #status.sus_contacts = {}
                #status.sus_contracts = {}
                #status.sus_mails = {}
                #status.skills = {}
                #status.cyno = {}
                def as_dict(x):
                    return x if isinstance(x, dict) else {}
                
                if set(state_result) != set(status.clone_status or []):
                    old_states = status.clone_status or {}
                    diff = {}
                    flagggs = []

                    # build dict of changes
                    for char_idddd, new_data in state_result.items():
                        old_data = old_states.get(str(char_idddd)) or old_states.get(char_idddd)  # handle str/int keys
                        if not old_data or old_data.get("state") != new_data.get("state"):
                            diff[char_idddd] = {
                                "old": old_data.get("state") if old_data else None,
                                "new": new_data.get("state"),
                            }

                    # add messages to flags
                    for char_idddd, change in diff.items():
                        char_nameeeee = resolve_character_name(char_idddd)
                        flagggs.append(
                            f"\n- **{char_nameeeee}**: {change['old']} → **{change['new']}**"
                        )

                    pinggg = ""

                    if "omega" in flagggs:
                        pinggg = get_pings('Omega Detected')

                    # final summary message
                    if flagggs:
                        changes.append(f"##{pinggg} Clone state change detected:{''.join(flagggs)}")
                        status.clone_status = state_result
                        status.save()
                
                if set(sp_age_ratio_result) != set(status.sp_age_ratio_result or []):
                        flaggs = []

                        for char_nameee, new_info in sp_age_ratio_result.items():
                            if char_nameee not in sp_age_ratio_result:
                                continue

                            old_info = status.sp_age_ratio_result.get(char_nameee, {})
                            old_ratio = old_info.get("sp_days", 0) / max(old_info.get("char_age", 1), 1)
                            new_ratio = new_info.get("sp_days", 0) / max(new_info.get("char_age", 1), 1)

                            if new_ratio > old_ratio:
                                flaggs.append(
                                    f"- **{char_nameee}'s** SP to age ratio went up from **{old_ratio}** to **{new_ratio}**\n"
                                )

                        if flaggs:
                            sp_list = "".join(flaggs)
                            changes.append(f"## {get_pings('SP Injected')} Skill Injection detected:\n{sp_list}")

                status.sp_age_ratio_result = sp_age_ratio_result
                status.save()

                if status.has_awox_kills != has_awox or set(awox_links) != set(status.awox_kill_links or []):
                    # Compare and find new links
                    old_links = set(status.awox_kill_links or [])
                    new_links = set(awox_links) - old_links
                    link_list = "\n".join(f"- {link}" for link in new_links)
                    logger.info(f"{char_name} new links {link_list}")
                    link_list3 = "\n".join(f"- {link}" for link in awox_links)
                    logger.info(f"{char_name} new links {link_list3}")
                    link_list2 = "\n".join(f"- {link}" for link in old_links)
                    logger.info(f"{char_name} old links {link_list2}")
                    if status.has_awox_kills != has_awox and has_awox:
                        changes.append(f"## AwoX kills: {'🚩' if has_awox else '✖'}")
                        status.has_awox_kills = has_awox
                        logger.info(f"{char_name} changed")
                    if new_links:
                        changes.append(f"##{get_pings('AwoX')} New AwoX kill(s):\n{link_list}")
                        logger.info(f"{char_name} new links")
                    old = set(status.awox_kill_links or [])
                    new = set(awox_links) - old
                    if new:
                        # notify
                        status.awox_kill_links = list(old | new)
                        status.updated = timezone.now()
                        status.save()

                if status.has_cyno != has_cyno or set(cyno_result) != set(status.cyno or []):
                    # 1) Flag change for top-level boolean
                    if status.has_cyno != has_cyno:
                        changes.append(f"Cyno: {'🚩' if has_cyno else '✖'}")
                        status.has_cyno = has_cyno

                    # 2) Grab the old vs. new JSON blobs
                    old_cyno: dict = status.cyno or {}
                    new_cyno: dict = cyno_result

                    # Determine which character names actually changed
                    changed_chars = []
                    for char_namee, new_data in new_cyno.items():
                        old_data = old_cyno.get(char_namee, {})
                        old_filtered = {k: v for k, v in old_data.items() if k != 'age'}
                        new_filtered = {k: v for k, v in new_data.items() if k != 'age'}

                        #logger.info(f"Comparing skills for character '{char_namee}':")
                        #logger.info(f"Old data normalized: {old_filtered}")
                        #logger.info(f"New data normalized: {new_filtered}")
                        #from deepdiff import DeepDiff
                        #diff = DeepDiff(old_filtered, new_filtered, ignore_order=True)
                        #logger.info(f"Diff for '{char_namee}': {diff}")
                        if old_filtered != new_filtered:
                            changed_chars.append(char_namee)

                    # 3) If any changed, build one table per character
                    if changed_chars:
                        # Mapping for display names
                        cyno_display = {
                            "s_cyno": "Cyno Skill",
                            "s_cov_cyno": "CovOps Cyno",
                            "s_recon": "Recon Ships",
                            "s_hic": "Heavy Interdiction",
                            "s_blops": "Black Ops",
                            "s_covops": "Covert Ops",
                            "s_brun": "Blockade Runners",
                            "s_sbomb": "Stealth Bombers",
                            "s_scru": "Strat Cruisers",
                            "s_expfrig": "Expedition Frigs",
                            "i_recon":   "Has Recon",
                            "i_hic":     "Has Hic",
                            "i_blops":   "Has Blops",
                            "i_covops":  "Has covops",
                            "i_brun":    "Has blockade Runner",  
                            "i_sbomb":   "Has bomber",
                            "i_scru":    "Has strat crus",
                            "i_expfrig": "Has exp frig",
                        }

                        # Column order
                        cyno_keys = [
                            "s_cyno", "s_cov_cyno", "s_recon", "s_hic", "s_blops",
                            "s_covops", "s_brun", "s_sbomb", "s_scru", "s_expfrig",
                            "i_recon", "i_hic", "i_blops", "i_covops", "i_brun",  
                            "i_sbomb", "i_scru", "i_expfrig",
                        ]

                        if changed_chars:
                            changes.append(f"##{get_pings('All Cyno Changes')} Changes in cyno capabilities detected:")

                        for charname in changed_chars:
                            old_entry = old_cyno.get(charname, {})
                            new_entry = new_cyno.get(charname, {})
                            anything = any(
                                val in (1, 2, 3, 4, 5)
                                for val in new_entry.values()
                            )
                            if anything == False:
                                continue
                            if new_entry.get("can_light", False) == True:
                                pingrole = get_pings('Can Light Cyno')
                            else:
                                pingrole = get_pings('Cyno Update')

                            changes.append(f"- **{charname}**{pingrole}:")
                            table_lines = [
                                "Value                  | Old | New (1 = trained but alpha, 2 = active)",
                                "-----------------------------------"
                            ]

                            for key in cyno_keys:
                                display = cyno_display.get(key, key)
                                old_val = str(old_entry.get(key, 0))
                                new_val = str(new_entry.get(key, 0))
                                table_lines.append(f"{display.ljust(22)} | {old_val.ljust(3)} | {new_val.ljust(3)}")

                            # Show can_light as a summary at bottom
                            can_light_old = old_entry.get("can_light", False)
                            can_light_new = new_entry.get("can_light", False)
                            table_lines.append("")
                            table_lines.append(f"{'Can Light':<22} | {'🚩' if can_light_old else '✖'} | {'🚩' if can_light_new else '✖'}")

                            table_block = "```\n" + "\n".join(table_lines) + "\n```"
                            changes.append(table_block)

                    # 4) Save new blob
                    status.cyno = new_cyno


                if status.has_skills != has_skills or set(skills_result) != set(status.skills or []):
                    # 1) If the boolean flag flipped, append the 🚩 / ✖ as before
                    if status.has_skills != has_skills:
                        changes.append(f"## Skills: {'🚩' if has_skills else '✖'}")
                        status.has_skills = has_skills

                    # 2) Grab the old vs. new JSON blobs
                    old_skills: dict = status.skills or {}
                    new_skills: dict = skills_result

                    # Determine which character names actually changed
                    changed_chars = []
                    def normalize_keys(d):
                        return {
                            str(k): v for k, v in d.items()
                            if str(k) != "total_sp"
                        }
                    for character_name, new_data in new_skills.items():
                        # Defensive: ensure old_data is a dict; otherwise treat as empty
                        old_data = old_skills.get(character_name)
                        if not isinstance(old_data, dict):
                            old_data = {}

                        # Defensive: ensure new_data is a dict as well
                        if not isinstance(new_data, dict):
                            new_data = {}

                        old_data_norm = normalize_keys(old_data)
                        new_data_norm = normalize_keys(new_data)

                        #logger.info(f"Comparing skills for character '{character_name}':")
                        #logger.info(f"Old data normalized: {old_data_norm}")
                        #logger.info(f"New data normalized: {new_data_norm}")
                        #from deepdiff import DeepDiff
                        #diff = DeepDiff(old_data_norm, new_data_norm, ignore_order=True)
                        #logger.info(f"Diff for '{character_name}': {diff}")

                        if old_data_norm != new_data_norm:
                            changed_chars.append(character_name)

                    # 3) If any changed, build one table per character
                    if changed_chars:
                        # A mapping from skill_id → human-readable name
                        skill_names = {
                            3426:   "CPU Management",
                            21603:  "Cynosural Field Theory",
                            22761:  "Recon Ships",
                            28609:  "Heavy Interdiction Cruisers",
                            28656:  "Black Ops",
                            12093:  "Covert Ops / Stealth Bombers",
                            20533:  "Capital Ships",
                            19719:  "Blockade Runners",
                            30651:  "Caldari Strategic Cruisers",
                            30652:  "Gallente Strategic Cruisers",
                            30653:  "Minmatar Strategic Cruisers",
                            30650:  "Amarr Strategic Cruisers",
                            33856:  "Expedition Frigates",
                        }

                        # Keep the same order you gave, but dedupe 12093 once
                        ordered_skill_ids = [
                            3426, 21603, 22761, 28609, 28656,
                            12093, 20533, 19719,
                            30651, 30652, 30653, 30650, 33856,
                        ]

                        if changed_chars:
                            changes.append(f"##{get_pings('skills')} Changes in skills detected:")

                        for charname in changed_chars:
                            # Defensive retrieval of old vs. new
                            raw_old = old_skills.get(charname)
                            old_entry = raw_old if isinstance(raw_old, dict) else {}

                            raw_new = new_skills.get(charname)
                            new_entry = raw_new if isinstance(raw_new, dict) else {}
                            anything = any(
                                (
                                    new_entry.get(sid, {"trained": 0, "active": 0})["trained"] > 0
                                    or
                                    new_entry.get(sid, {"trained": 0, "active": 0})["active"] > 0
                                )
                                for sid in ordered_skill_ids
                            )
                            if anything == False:
                                continue
                            logger.info(new_entry.values())


                            # 3a) Append the “- **CharacterName**:” header
                            changes.append(f"- **{charname}**:")

                            # 3b) Build the table header and separator
                            table_lines = [
                                "Skill                           | Old (Trained/Active) | New (Trained/Active)",
                                "------------------------------------------------------"
                            ]

                            # 3c) For each skill_id in order, look up old vs. new levels
                            for sid in ordered_skill_ids:
                                name = skill_names.get(sid, f"Skill ID {sid}")

                                # Because JSONField stores keys as strings, do str(sid)
                                old_skill = old_entry.get(str(sid), {"trained": 0, "active": 0})
                                new_skill = new_entry.get(sid, {"trained": 0, "active": 0})

                                # Defensive: if old_skill is not a dict, coerce it
                                if not isinstance(old_skill, dict):
                                    old_skill = {"trained": 0, "active": 0}
                                if not isinstance(new_skill, dict):
                                    new_skill = {"trained": 0, "active": 0}

                                old_tr = old_skill.get("trained", 0)
                                old_ac = old_skill.get("active", 0)
                                new_tr = new_skill.get("trained", 0)
                                new_ac = new_skill.get("active", 0)

                                old_fmt = f"{old_tr}/{old_ac}"
                                new_fmt = f"{new_tr}/{new_ac}"

                                # Pad the skill name to 30 chars for alignment
                                name_padded = name.ljust(30)

                                # Pad the “trained/active” to at least 9 chars so columns line up
                                table_lines.append(f"{name_padded} | {old_fmt.ljust(9)} | {new_fmt.ljust(9)}")

                            # 3d) Wrap the lines in triple backticks
                            table_block = "```\n" + "\n".join(table_lines) + "\n```"
                            changes.append(table_block)

                    # 4) Finally, write back the new blob so that next time “old” is fresh
                    status.skills = new_skills
                # …rest of your saving logic, e.g. status.save(), etc.
                    

                if status.has_hostile_assets != has_hostile_assets or set(hostile_assets_result) != set(status.hostile_assets or []):
                    # Compare and find new links
                    old_links = set(status.hostile_assets or [])
                    new_links = set(hostile_assets_result) - old_links
                    link_list = "\n".join(
                        f"- {system} owned by {hostile_assets_result[system]}" 
                        for system in (set(hostile_assets_result) - set(status.hostile_assets or []))
                    )
                    logger.info(f"{char_name} new assets {link_list}")
                    link_list2 = "\n- ".join(f"🔗 {link}" for link in old_links)
                    logger.info(f"{char_name} old assets {link_list2}")
                    if status.has_hostile_assets != has_hostile_assets:
                        changes.append(f"## Hostile Assets: {'🚩' if has_hostile_assets else '✖'}")
                        logger.info(f"{char_name} changed")
                    if new_links:
                        changes.append(f"##{get_pings('New Hostile Assets')} New Hostile Assets:\n{link_list}")
                        logger.info(f"{char_name} new assets")
                    status.has_hostile_assets = has_hostile_assets
                    status.hostile_assets = hostile_assets_result


                if status.has_hostile_clones != has_hostile_clones or set(hostile_clones_result) != set(status.hostile_clones or []):
                    # Compare and find new links
                    old_links = set(status.hostile_clones or [])
                    new_links = set(hostile_clones_result) - old_links
                    link_list = "\n".join(
                        f"- {system} owned by {hostile_clones_result[system]}" 
                        for system in (set(hostile_clones_result) - set(status.hostile_clones or []))
                    )
                    logger.info(f"{char_name} new clones: {link_list}")
                    link_list2 = "\n".join(f"🔗 {link}" for link in old_links)
                    logger.info(f"{char_name} old clones: {link_list2}")
                    if status.has_hostile_clones != has_hostile_clones:
                        changes.append(f"## Hostile Clones: {'🚩' if has_hostile_clones else '✖'}")
                        logger.info(f"{char_name} changed")
                    if new_links:
                        changes.append(f"##{get_pings('New Hostile Clones')} New Hostile Clone(s):\n{link_list}")
                        logger.info(f"{char_name} new clones")
                    status.has_hostile_clones = has_hostile_clones
                    status.hostile_clones = hostile_clones_result

                if status.has_imp_blacklist != has_imp_blacklist:
                    changes.append(f"Imp Blacklist: {'🚩' if has_imp_blacklist else '✖'}")
                    status.has_imp_blacklist = has_imp_blacklist

                if status.has_lawn_blacklist != has_lawn_blacklist:
                    changes.append(f"Lawn Backlist: {'🚩' if has_lawn_blacklist else '✖'}")
                    status.has_lawn_blacklist = has_lawn_blacklist

                if status.has_game_time_notifications != has_game_time_notifications:
                    changes.append(f"Game Time: {'🚩' if has_game_time_notifications else '✖'}")
                    status.has_game_time_notifications = has_game_time_notifications

                if status.has_skill_injected != has_skill_injected:
                    changes.append(f"Skill Injected: {'🚩' if has_skill_injected else '✖'}")
                    status.has_skill_injected = has_skill_injected

                if status.has_sus_contacts != has_sus_contacts or set(sus_contacts_result) != set(as_dict(status.sus_contacts) or {}):
                    old_contacts = as_dict(status.sus_contacts) or {}
                    #normalized_old = { str(cid): v for cid, v in status.sus_contacts.items() }
                    #normalized_new = { str(cid): v for cid, v in sus_contacts_result.items() }

                    old_ids   = set(as_dict(status.sus_contacts).keys())
                    new_ids   = set(sus_contacts_result.keys())
                    new_links = new_ids - old_ids
                    if new_links:
                        link_list = "\n".join(
                            f"🔗 {sus_contacts_result[cid]}" for cid in new_links
                        )
                        logger.info(f"{char_name} new assets:\n{link_list}")

                    if old_ids:
                        old_link_list = "\n".join(
                            f"🔗 {old_contacts[cid]}" for cid in old_ids if cid in old_contacts
                        )
                        logger.info(f"{char_name} old assets:\n{old_link_list}")

                    if status.has_sus_contacts != has_sus_contacts:
                        changes.append(f"## Sus Contacts: {'🚩' if has_sus_contacts else '✖'}")
                    logger.info(f"{char_name} status changed")

                    if new_links:
                        changes.append(f"## New Sus Contacts:")
                        for cid in new_links:
                            res = sus_contacts_result[cid]
                            ping = get_pings('New Sus Contacts')
                            if res.startswith("- A -"):
                                ping = ""
                            changes.append(f"{res} {ping}")

                    status.has_sus_contacts = has_sus_contacts
                    status.sus_contacts = sus_contacts_result

                if status.has_sus_contracts != has_sus_contracts or set(sus_contracts_result) != set(as_dict(status.sus_contracts) or {}):
                    old_contracts = as_dict(status.sus_contracts) or {}
                    #normalized_old = { str(cid): v for cid, v in status.sus_contacts.items() }
                    #normalized_new = { str(cid): v for cid, v in sus_contacts_result.items() }

                    old_ids   = set(as_dict(status.sus_contracts).keys())
                    new_ids   = set(sus_contracts_result.keys())
                    new_links = new_ids - old_ids
                    if new_links:
                        link_list = "\n".join(
                            f"🔗 {sus_contracts_result[issuer_id]}" for issuer_id in new_links
                        )
                        logger.info(f"{char_name} new assets:\n{link_list}")

                    if old_ids:
                        old_link_list = "\n".join(
                            f"🔗 {old_contracts[issuer_id]}" for issuer_id in old_ids if issuer_id in old_contracts
                        )
                        logger.info(f"{char_name} old assets:\n{old_link_list}")

                    if status.has_sus_contracts != has_sus_contracts:
                        changes.append(f"## Sus Contracts: {'🚩' if has_sus_contracts else '✖'}")
                    logger.info(f"{char_name} status changed")

                    if new_links:
                        changes.append(f"## New Sus Contracts:")
                        for issuer_id in new_links:
                            res = sus_contracts_result[issuer_id]
                            ping = get_pings('New Sus Contracts')
                            if res.startswith("- A -"):
                                ping = ""
                            changes.append(f"{res} {ping}")

                    status.has_sus_contracts = has_sus_contracts
                    status.sus_contracts = sus_contracts_result

                if status.has_sus_mails != has_sus_mails or set(sus_mails_result) != set(as_dict(status.sus_mails) or {}):
                    old_mails = as_dict(status.sus_mails) or {}
                    #normalized_old = { str(cid): v for cid, v in status.sus_contacts.items() }
                    #normalized_new = { str(cid): v for cid, v in sus_contacts_result.items() }

                    old_ids   = set(as_dict(status.sus_mails).keys())
                    new_ids   = set(sus_mails_result.keys())
                    new_links = new_ids - old_ids
                    if new_links:
                        link_list = "\n".join(
                            f"🔗 {sus_mails_result[issuer_id]}" for issuer_id in new_links
                        )
                        logger.info(f"{char_name} new assets:\n{link_list}")

                    if old_ids:
                        old_link_list = "\n".join(
                            f"🔗 {old_mails[issuer_id]}" for issuer_id in old_ids if issuer_id in old_mails
                        )
                        logger.info(f"{char_name} old assets:\n{old_link_list}")

                    if status.has_sus_mails != has_sus_mails:
                        changes.append(f"## Sus Mails: {'🚩' if has_sus_mails else '✖'}")
                    logger.info(f"{char_name} status changed")

                    if new_links:
                        changes.append(f"## New Sus Mails:")
                        for issuer_id in new_links:
                            res = sus_mails_result[issuer_id]
                            ping = get_pings('New Sus Mails')
                            if res.startswith("- A -"):
                                ping = ""
                            changes.append(f"{res} {ping}")

                    status.has_sus_mails = has_sus_mails
                    status.sus_mails = sus_mails_result

                if status.has_sus_trans != has_sus_trans or set(sus_trans_result) != set(as_dict(status.sus_trans) or {}):
                    old_trans = as_dict(status.sus_trans) or {}
                    #normalized_old = { str(cid): v for cid, v in status.sus_contacts.items() }
                    #normalized_new = { str(cid): v for cid, v in sus_contacts_result.items() }

                    old_ids   = set(as_dict(status.sus_trans).keys())
                    new_ids   = set(sus_trans_result.keys())
                    new_links = new_ids - old_ids
                    if new_links:
                        link_list = "\n".join(
                            f"{sus_trans_result[issuer_id]}" for issuer_id in new_links
                        )
                        logger.info(f"{char_name} new trans:\n{link_list}")

                    if old_ids:
                        old_link_list = "\n".join(
                            f"{old_trans[issuer_id]}" for issuer_id in old_ids if issuer_id in old_trans
                        )
                        logger.info(f"{char_name} old trans:\n{old_link_list}")

                    if status.has_sus_trans != has_sus_trans:
                        changes.append(f"## Sus Transactions: {'🚩' if has_sus_trans else '✖'}")
                    logger.info(f"{char_name} status changed")
                    changes.append(f"## New Sus Transactions{get_pings('New Sus Transactions')}:\n{link_list}")
                    #if new_links:
                    #    changes.append(f"## New Sus Transactions @here:")
                    #    for issuer_id in new_links:
                    #        res = sus_trans_result[issuer_id]
                    #        ping = f""
                    #        if res.startswith("- A -"):
                    #            ping = ""
                    #        changes.append(f"{res} {ping}")

                    status.has_sus_trans = has_sus_trans
                    status.sus_trans = sus_trans_result

                if changes:
                    for i in range(0, len(changes)):
                        chunk = changes[i]
                        if i == 0:
                            msg = f"# 🛑 Status change detected for **{char_name}**:\n" + "\n" + chunk
                        else:
                            msg = chunk
                        logger.info(f"Measage: {msg}")
                        send_message(msg)
                        time.sleep(0.03)
                status.updated = timezone.now()
                status.save()

    except Exception as e:
        logger.error("Task failed", exc_info=True)
        instance.is_active = False
        instance.save()
        send_message(
            f"#{get_pings('Error')} Big Brother encountered an unexpected error and disabled itself, "
            "please forward your aa worker.log and the error below to Andrew Xadi"
        )

        tb_str = traceback.format_exc()
        max_chunk = 1000
        start = 0
        length = len(tb_str)

        while start < length:
            end = min(start + max_chunk, length)
            if end < length:
                nl = tb_str.rfind('\n', start, end)
                if nl != -1 and nl > start:
                    end = nl + 1
            chunk = tb_str[start:end]
            send_message(f"```{chunk}```")
            start = end
    
    from django_celery_beat.models import PeriodicTask
    task_name = 'BB run regular updates'
    task = PeriodicTask.objects.filter(name=task_name).first()
    if not task.enabled:
        send_message("Big Brother task has finished, you can now enable the task")

