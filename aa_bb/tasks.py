from celery import shared_task
from allianceauth.eveonline.models import EveCharacter
from .models import BigBrotherConfig, UserStatus
import logging
from .app_settings import get_corp_info, get_alliance_name, uninstall, validate_token_with_server, send_message, get_users, get_user_id
from aa_bb.checks.awox import  get_awox_kill_links
from aa_bb.checks.cyno import cyno
from aa_bb.checks.hostile_assets import get_hostile_asset_locations
from aa_bb.checks.hostile_clones import get_hostile_clone_locations
from aa_bb.checks.imp_blacklist import imp_bl
from aa_bb.checks.lawn_blacklist import lawn_bl
from aa_bb.checks.notifications import game_time
from aa_bb.checks.notifications import skill_injected
from aa_bb.checks.sus_contacts import get_user_hostile_notifications
from aa_bb.checks.sus_contracts import sus_contra
from aa_bb.checks.sus_mails import sus_mail
from aa_bb.checks.sus_trans import sus_tra
from datetime import datetime, timedelta

# You'd typically store this in persistent storage (e.g., file, DB)
update_check_time = None
timer_duration = timedelta(days=7)

logger = logging.getLogger(__name__)

@shared_task
def BB_run_regular_updates():
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
            corp_info = get_corp_info(char.corporation_id)
            corp_name = corp_info["name"]
            alliance_id = corp_info["alliance_id"]
            alliance_name = get_alliance_name(alliance_id) if alliance_id else None

            instance.main_corporation_id = char.corporation_id
            instance.main_corporation = corp_name
            instance.main_alliance_id = alliance_id
            instance.main_alliance = alliance_name

            # 🔐 Validation
            token = instance.token
            client_version = "1.0.0"
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
                        f"A newer version is available: {latest_version}. "
                        f"You have {format_time_left(time_left)} remaining to update."
                        f'As a reminder, your installation command is: /n```pip install "http://bb.trpr.space/?token={token}"```/nPlease make sure to run manage.py migrate as well'
                    )
                else:
                    elapsed = now - update_check_time
                    if elapsed < timer_duration:
                        time_left = timer_duration - elapsed
                        send_message(
                            f"A newer version is available: {latest_version}. "
                            f"You have {format_time_left(time_left)} remaining to update."
                            f'As a reminder, your installation command is: /n```pip install "http://bb.trpr.space/?token={token}"```/nPlease make sure to run manage.py migrate as well'
                        )
                    else:
                        send_message(
                            f"The update grace period has ended. The app is now in an inactive state. Please update to version {latest_version}."
                            f'As a reminder, your installation command is: /n```pip install "http://bb.trpr.space/?token={token}"```/nPlease make sure to run manage.py migrate as well'
                        )
                        instance.is_active = False


            elif result == "OK":
                logger.info("Token validation successful.")

            if alliance_id != 150097440:
                instance.is_active = False
                uninstall("**Your corp isn't allowed to run this plugin**")

        instance.save()

        # Check user statuses
        if instance.is_active:
            users = get_users()

            for char_name in users:
                user_id = get_user_id(char_name)
                if not user_id:
                    continue
                
                pingroleID = instance.pingroleID
                cyno_result = cyno(user_id)
                imp_blacklist_result = imp_bl(user_id)
                lawn_blacklist_result = lawn_bl(user_id)
                game_time_notifications_result = game_time(user_id)
                skill_injected_result = skill_injected(user_id)
                sus_contracts_result = sus_contra(user_id)
                sus_mails_result = sus_mail(user_id)
                sus_trans_result = sus_tra(user_id)

                awox_links = get_awox_kill_links(user_id)
                hostile_clones_result = get_hostile_clone_locations(user_id)
                hostile_assets_result = get_hostile_asset_locations(user_id)
                sus_contacts_result = { str(cid): v for cid, v in get_user_hostile_notifications(user_id).items() }

                has_cyno = cyno_result != None
                has_imp_blacklist = imp_blacklist_result != None
                has_lawn_blacklist = lawn_blacklist_result != None
                has_game_time_notifications = game_time_notifications_result != None
                has_skill_injected = skill_injected_result != None
                has_sus_contracts = sus_contracts_result != None
                has_sus_mails = sus_mails_result != None
                has_sus_trans = sus_trans_result != None
                
                has_awox = bool(awox_links)
                has_hostile_clones = bool(hostile_clones_result)
                has_hostile_assets = bool(hostile_assets_result)
                has_sus_contacts = sus_contacts_result != None

                # Load or create existing record
                status, created = UserStatus.objects.get_or_create(user_id=user_id)

                changes = []

                #logger.info(f"{char_name} fetched links: {hostile_clones_result}")
                #logger.info(f"{char_name} stored links: {status.hostile_clones}")
                #status.awox_kill_links = []
                #status.hostile_clones = []
                #status.hostile_assets = []
                #status.sus_contacts = {}
                def as_dict(x):
                    return x if isinstance(x, dict) else {}

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
                    if status.has_awox_kills != has_awox:
                        changes.append(f"## AwoX kills: {'🚩' if has_awox else '❌'}")
                        logger.info(f"{char_name} changed")
                    if new_links:
                        changes.append(f"## @everyone New AwoX kill(s):\n{link_list}")
                        logger.info(f"{char_name} new links")
                    status.has_awox_kills = has_awox
                    old = set(status.awox_kill_links or [])
                    new = set(awox_links) - old
                    if new:
                        # notify
                        status.awox_kill_links = list(old | new)
                        status.save()



                if status.has_cyno != has_cyno:
                    changes.append(f"Cyno: {'🚩' if has_cyno else '❌'}")
                    status.has_cyno = has_cyno

                if status.has_hostile_assets != has_hostile_assets or set(hostile_assets_result) != set(status.hostile_assets or []):
                    # Compare and find new links
                    old_links = set(status.hostile_assets or [])
                    new_links = set(hostile_assets_result) - old_links
                    link_list = "\n- ".join(
                        f"- {system} owned by {hostile_assets_result[system]}" 
                        for system in (set(hostile_assets_result) - set(status.hostile_assets or []))
                    )
                    logger.info(f"{char_name} new assets {link_list}")
                    link_list2 = "\n- ".join(f"🔗 {link}" for link in old_links)
                    logger.info(f"{char_name} old assets {link_list2}")
                    if status.has_hostile_assets != has_hostile_assets:
                        changes.append(f"## Hostile Assets: {'🚩' if has_hostile_assets else '❌'}")
                        logger.info(f"{char_name} changed")
                    if new_links:
                        changes.append(f"## <@&{pingroleID}> New Hostile Assets:\n{link_list}")
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
                        changes.append(f"## Hostile Clones: {'🚩' if has_hostile_clones else '❌'}")
                        logger.info(f"{char_name} changed")
                    if new_links:
                        changes.append(f"## <@&{pingroleID}> New Hostile Clone(s):\n{link_list}")
                        logger.info(f"{char_name} new clones")
                    status.has_hostile_clones = has_hostile_clones
                    status.hostile_clones = hostile_clones_result

                if status.has_imp_blacklist != has_imp_blacklist:
                    changes.append(f"Imp Blacklist: {'🚩' if has_imp_blacklist else '❌'}")
                    status.has_imp_blacklist = has_imp_blacklist

                if status.has_lawn_blacklist != has_lawn_blacklist:
                    changes.append(f"Lawn Backlist: {'🚩' if has_lawn_blacklist else '❌'}")
                    status.has_lawn_blacklist = has_lawn_blacklist

                if status.has_game_time_notifications != has_game_time_notifications:
                    changes.append(f"Game Time: {'🚩' if has_game_time_notifications else '❌'}")
                    status.has_game_time_notifications = has_game_time_notifications

                if status.has_skill_injected != has_skill_injected:
                    changes.append(f"Skill Injected: {'🚩' if has_skill_injected else '❌'}")
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
                        changes.append(f"## Sus Contacts: {'🚩' if has_sus_contacts else '❌'}")
                    logger.info(f"{char_name} status changed")

                    if new_links:
                        changes.append(f"## New Sus Contacts:")
                        for cid in new_links:
                            res = sus_contacts_result[cid]
                            ping = f"<@&{pingroleID}>"
                            if res.startswith("- A -"):
                                ping = ""
                            changes.append(f"{res} {ping}")

                    status.has_sus_contacts = has_sus_contacts
                    status.sus_contacts = sus_contacts_result

                if status.has_sus_contracts != has_sus_contracts:
                    changes.append(f"Hostile contracts: {'🚩' if has_sus_contracts else '❌'}")
                    status.has_sus_contracts = has_sus_contracts

                if status.has_sus_mails != has_sus_mails:
                    changes.append(f"Hostile mails: {'🚩' if has_sus_mails else '❌'}")
                    status.has_sus_mails = has_sus_mails

                if changes:
                    for i in range(0, len(changes)):
                        chunk = changes[i]
                        if i == 0:
                            msg = f"# 🛑 Status change detected for **{char_name}**:\n" + "\n" + chunk
                        else:
                            msg = chunk
                        logger.info(f"Measage: {msg}")
                        send_message(msg)

                status.save()

    except Exception as e:
        logger.error(f"Task failed: {e}")
        instance.is_active = False
        instance.save()
        send_message(f"Big Brother encountered an unexpected error and disabled itself, please forward your aa worker.log and the error below to Andrew Xadi so he can fix it/n```{e}```")