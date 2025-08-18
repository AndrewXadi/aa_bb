from celery import shared_task
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
from allianceauth.authentication.models import UserProfile
from django_celery_beat.models import PeriodicTask, CrontabSchedule
from .models import BigBrotherConfig, CorpStatus, Messages,OptMessages1,OptMessages2,OptMessages3,OptMessages4,OptMessages5,LeaveRequest
import logging
from .app_settings import send_message, get_pings, resolve_corporation_name, get_users, get_user_id, get_character_id
from aa_bb.checks_cb.hostile_assets import get_corp_hostile_asset_locations
from aa_bb.checks_cb.sus_contracts import get_corp_hostile_contracts
from aa_bb.checks_cb.sus_trans import get_corp_hostile_transactions
from aa_bb.checks.roles_and_tokens import get_user_roles_and_tokens
from corptools.api.helpers import get_alts_queryset
from datetime import timedelta, date
from django.utils import timezone
import time
import traceback
import random
from . import __version__

# You'd typically store this in persistent storage (e.g., file, DB)
update_check_time = None
timer_duration = timedelta(days=7)

logger = logging.getLogger(__name__)

@shared_task
def CB_run_regular_updates():
    global update_check_time
    instance = BigBrotherConfig.get_solo()


    try:
        if instance.is_active:
            # Corp Brother
            qs = EveCorporationInfo.objects.all()
            corps = []
            if qs is not None:
                corps = (
                    qs.values_list("corporation_id", flat=True)
                      .order_by("corporation_name")
                ).filter(
                    corporationaudit__isnull=False,
                )
            

            for corp_id in corps:
                ignored_str = BigBrotherConfig.get_solo().ignored_corporations or ""
                ignored_ids = {int(s) for s in ignored_str.split(",") if s.strip().isdigit()}
                if corp_id in ignored_ids:
                    continue
                hostile_assets_result = get_corp_hostile_asset_locations(corp_id)
                sus_contracts_result = { str(issuer_id): v for issuer_id, v in get_corp_hostile_contracts(corp_id).items() }
                sus_trans_result = { str(issuer_id): v for issuer_id, v in get_corp_hostile_transactions(corp_id).items() }

                has_hostile_assets = bool(hostile_assets_result)
                has_sus_contracts = bool(sus_contracts_result)
                has_sus_trans = bool(sus_trans_result)

                # Load or create existing record
                corpstatus, created = CorpStatus.objects.get_or_create(corp_id=corp_id)

                corp_changes = []

                #corpstatus.hostile_assets = []
                #corpstatus.sus_contracts = {}
                #corpstatus.sus_trans = {}
                def as_dict(x):
                    return x if isinstance(x, dict) else {}
                
                if not corpstatus.corp_name:
                    corpstatus.corp_name = resolve_corporation_name(corp_id)

                corp_name = corpstatus.corp_name
                
                if corpstatus.has_hostile_assets != has_hostile_assets or set(hostile_assets_result) != set(corpstatus.hostile_assets or []):
                    # Compare and find new links
                    old_links = set(corpstatus.hostile_assets or [])
                    new_links = set(hostile_assets_result) - old_links
                    link_list = "\n".join(
                        f"- {system} owned by {hostile_assets_result[system]}" 
                        for system in (set(hostile_assets_result) - set(corpstatus.hostile_assets or []))
                    )
                    logger.info(f"{corp_name} new assets {link_list}")
                    link_list2 = "\n- ".join(f"🔗 {link}" for link in old_links)
                    logger.info(f"{corp_name} old assets {link_list2}")
                    if corpstatus.has_hostile_assets != has_hostile_assets:
                        corp_changes.append(f"## Hostile Assets: {'🚩' if has_hostile_assets else '✖'}")
                        logger.info(f"{corp_name} changed")
                    if new_links:
                        corp_changes.append(f"##{get_pings('New Hostile Assets')} New Hostile Assets:\n{link_list}")
                        logger.info(f"{corp_name} new assets")
                    corpstatus.has_hostile_assets = has_hostile_assets
                    corpstatus.hostile_assets = hostile_assets_result

                if corpstatus.has_sus_contracts != has_sus_contracts or set(sus_contracts_result) != set(as_dict(corpstatus.sus_contracts) or {}):
                    old_contracts = as_dict(corpstatus.sus_contracts) or {}
                    #normalized_old = { str(cid): v for cid, v in status.sus_contacts.items() }
                    #normalized_new = { str(cid): v for cid, v in sus_contacts_result.items() }

                    old_ids   = set(as_dict(corpstatus.sus_contracts).keys())
                    new_ids   = set(sus_contracts_result.keys())
                    logger.info(f"old {len(old_ids)}, new {len(new_ids)}")
                    new_links = new_ids - old_ids
                    if new_links:
                        link_list = "\n".join(
                            f"🔗 {sus_contracts_result[issuer_id]}" for issuer_id in new_links
                        )
                        logger.info(f"{corp_name} new assets:\n{link_list}")

                    if old_ids:
                        old_link_list = "\n".join(
                            f"🔗 {old_contracts[issuer_id]}" for issuer_id in old_ids if issuer_id in old_contracts
                        )
                        logger.info(f"{corp_name} old assets:\n{old_link_list}")

                    if corpstatus.has_sus_contracts != has_sus_contracts:
                        corp_changes.append(f"## Sus Contracts: {'🚩' if has_sus_contracts else '✖'}")
                    logger.info(f"{corp_name} status changed")

                    if new_links:
                        corp_changes.append(f"## New Sus Contracts:")
                        for issuer_id in new_links:
                            res = sus_contracts_result[issuer_id]
                            ping = get_pings('New Sus Contracts')
                            if res.startswith("- A -"):
                                ping = ""
                            corp_changes.append(f"{res} {ping}")

                    corpstatus.has_sus_contracts = has_sus_contracts
                    corpstatus.sus_contracts = sus_contracts_result

                if corpstatus.has_sus_trans != has_sus_trans or set(sus_trans_result) != set(as_dict(corpstatus.sus_trans) or {}):
                    old_trans = as_dict(corpstatus.sus_trans) or {}
                    #normalized_old = { str(cid): v for cid, v in status.sus_contacts.items() }
                    #normalized_new = { str(cid): v for cid, v in sus_contacts_result.items() }

                    old_ids   = set(as_dict(corpstatus.sus_trans).keys())
                    new_ids   = set(sus_trans_result.keys())
                    new_links = new_ids - old_ids
                    if new_links:
                        link_list = "\n".join(
                            f"{sus_trans_result[issuer_id]}" for issuer_id in new_links
                        )
                        logger.info(f"{corp_name} new trans:\n{link_list}")

                    if old_ids:
                        old_link_list = "\n".join(
                            f"{old_trans[issuer_id]}" for issuer_id in old_ids if issuer_id in old_trans
                        )
                        logger.info(f"{corp_name} old trans:\n{old_link_list}")

                    if corpstatus.has_sus_trans != has_sus_trans:
                        corp_changes.append(f"## Sus Transactions: {'🚩' if has_sus_trans else '✖'}")
                    logger.info(f"{corp_name} status changed")
                    corp_changes.append(f"## New Sus Transactions{get_pings('New Sus Transactions')}:\n{link_list}")
                    #if new_links:
                    #    changes.append(f"## New Sus Transactions @here:")
                    #    for issuer_id in new_links:
                    #        res = sus_trans_result[issuer_id]
                    #        ping = f""
                    #        if res.startswith("- A -"):
                    #            ping = ""
                    #        changes.append(f"{res} {ping}")

                    corpstatus.has_sus_trans = has_sus_trans
                    corpstatus.sus_trans = sus_trans_result

                if corp_changes:
                    for i in range(0, len(corp_changes)):
                        chunk = corp_changes[i]
                        if i == 0:
                            msg = f"# 🛑 Status change detected for **{corp_name}**:\n" + "\n" + chunk
                        else:
                            msg = chunk
                        logger.info(f"Measage: {msg}")
                        send_message(msg)
                        time.sleep(0.03)
                corpstatus.updated = timezone.now()
                corpstatus.save()

    except Exception as e:
        logger.error("Task failed", exc_info=True)
        instance.is_active = False
        instance.save()
        send_message(
            f"#{get_pings('Error')} Corp Brother encountered an unexpected error and disabled itself, "
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
    task_name = 'CB run regular updates'
    task = PeriodicTask.objects.filter(name=task_name).first()
    if not task.enabled:
        send_message("Corp Brother task has finished, you can now enable the task")


@shared_task
def check_member_compliance():
    instance = BigBrotherConfig.get_solo()
    if not instance.is_active:
        return
    users = get_users()
    messages = ""

    for char_name in users:
        user_id = get_user_id(char_name)
        data = get_user_roles_and_tokens(user_id)
        flags = ""

        for character, info in data.items():
            has_roles = any(info.get(role, False) for role in ("director", "accountant", "station_manager", "personnel_manager"))
            has_char_token = info.get("character_token", False)
            has_corp_token = info.get("corporation_token", False)

            # Non-compliant if character has roles but no corporation token or missing character token
            if not has_char_token or (has_roles and not has_corp_token):
                details = []
                if not has_char_token:
                    details.append("      - missing character token\n")
                if has_roles and not has_corp_token:
                    details.append("      - has corp roles but missing corp token\n")
                flags += f"  - {character}:\n{''.join(details)}"

        if flags:
            messages += f"-  {char_name}:\n{flags}"

    from allianceauth.eveonline.models import EveCorporationInfo, EveCharacter
    from .app_settings import get_corporation_info, get_alliance_name
    missing_characters = []
    corp_ids = instance.member_corporations
    if corp_ids:
        for corp_id in corp_ids.split(","):
            corp_chars = []
            corp_id = corp_id.strip()
            if not corp_id:
                continue

            # Get characters linked in your DB
            linked_chars = list(
                EveCharacter.objects.filter(corporation_id=corp_id)
                .values_list("character_name", flat=True)
            )

            corp_name = get_corporation_info(corp_id)["name"]
            # Get characters from EveWho API
            all_corp_members = get_corp_character_names(corp_id)
            # Find missing characters
            for char_name in all_corp_members:
                if char_name not in linked_chars:
                    corp_chars.append(f"  - {char_name}")
            if corp_chars:
                chars_str = "\n".join(corp_chars)
                missing_characters.append(f"- {corp_name}\n{chars_str}")
    ali_ids = instance.member_alliances
    logger.info(f"ali_ids: {str(ali_ids)}")
    if ali_ids:
        for ali_id in ali_ids.split(","):
            logger.info(f"ali_id: {str(ali_id)}")
            ali_chars = []
            ali_id = ali_id.strip()
            logger.info(f"ali_id: {str(ali_id)}")
            if not ali_id:
                continue

            # Get characters linked in your DB
            linked_chars = list(
                EveCharacter.objects.filter(alliance_id=ali_id)
                .values_list("character_name", flat=True)
            )
            logger.info(f"linked_chars: {str(linked_chars)}")

            ali_name = get_alliance_name(ali_id)
            logger.info(f"ali_name: {str(ali_name)}")
            # Get characters from EveWho API
            all_ali_members = get_ali_character_names(ali_id)
            logger.info(f"all_ali_members: {str(all_ali_members)}")
            # Find missing characters
            for char_name in all_ali_members:
                if char_name not in linked_chars:
                    ali_chars.append(f"  - {char_name}")
            if ali_chars:
                chars_str = "\n".join(ali_chars)
                missing_characters.append(f"- {ali_name}\n{chars_str}")
    compliance_msg = ""
    if missing_characters:
        logger.info(f"missing_characters: {str(missing_characters)}")
        joined_msg = '\n'.join(missing_characters)
        compliance_msg += f"\n## Missing tokens for member characters:\n{joined_msg}"

    if messages:
        compliance_msg += f"\n## Non Compliant users found:\n" + messages

    if compliance_msg: 
        compliance_msg = f"#{get_pings('Compliance')} Compliance Issues found:" + compliance_msg
        send_message(compliance_msg)

import requests

def get_corp_character_names(corp_id: int) -> str:
    url = f"https://evewho.com/api/corplist/{corp_id}"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()

    return [char["name"] for char in data.get("characters", [])]

def get_ali_character_names(ali_id: int) -> str:
    url = f"https://evewho.com/api/allilist/{ali_id}"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()

    return [char["name"] for char in data.get("characters", [])]


@shared_task
def BB_send_daily_messages():
    config = BigBrotherConfig.get_solo()
    webhook = config.dailywebhook
    enabled = config.are_daily_messages_active

    if not enabled:
        return

    # Get only messages not sent in this cycle
    unsent_messages = Messages.objects.filter(sent_in_cycle=False)

    if not unsent_messages.exists():
        # Reset all messages if cycle is complete
        Messages.objects.update(sent_in_cycle=False)
        unsent_messages = Messages.objects.filter(sent_in_cycle=False)

    if not unsent_messages.exists():
        return  # Still nothing to send

    message = random.choice(list(unsent_messages))
    send_message(message.text, webhook)

    # Mark as sent
    message.sent_in_cycle = True
    message.save()

@shared_task
def BB_send_opt_message1():
    config = BigBrotherConfig.get_solo()
    webhook = config.optwebhook1
    enabled = config.are_opt_messages1_active

    if not enabled:
        return

    # Get only messages not sent in this cycle
    unsent_messages = OptMessages1.objects.filter(sent_in_cycle=False)

    if not unsent_messages.exists():
        # Reset all messages if cycle is complete
        OptMessages1.objects.update(sent_in_cycle=False)
        unsent_messages = OptMessages1.objects.filter(sent_in_cycle=False)

    if not unsent_messages.exists():
        return  # Still nothing to send

    message = random.choice(list(unsent_messages))
    send_message(message.text, webhook)

    # Mark as sent
    message.sent_in_cycle = True
    message.save()

@shared_task
def BB_send_opt_message2():
    config = BigBrotherConfig.get_solo()
    webhook = config.optwebhook2
    enabled = config.are_opt_messages2_active

    if not enabled:
        return

    # Get only messages not sent in this cycle
    unsent_messages = OptMessages2.objects.filter(sent_in_cycle=False)

    if not unsent_messages.exists():
        # Reset all messages if cycle is complete
        OptMessages2.objects.update(sent_in_cycle=False)
        unsent_messages = OptMessages2.objects.filter(sent_in_cycle=False)

    if not unsent_messages.exists():
        return  # Still nothing to send

    message = random.choice(list(unsent_messages))
    send_message(message.text, webhook)

    # Mark as sent
    message.sent_in_cycle = True
    message.save()

@shared_task
def BB_send_opt_message3():
    config = BigBrotherConfig.get_solo()
    webhook = config.optwebhook3
    enabled = config.are_opt_messages3_active

    if not enabled:
        return

    # Get only messages not sent in this cycle
    unsent_messages = OptMessages3.objects.filter(sent_in_cycle=False)

    if not unsent_messages.exists():
        # Reset all messages if cycle is complete
        OptMessages3.objects.update(sent_in_cycle=False)
        unsent_messages = OptMessages3.objects.filter(sent_in_cycle=False)

    if not unsent_messages.exists():
        return  # Still nothing to send

    message = random.choice(list(unsent_messages))
    send_message(message.text, webhook)

    # Mark as sent
    message.sent_in_cycle = True
    message.save()

@shared_task
def BB_send_opt_message4():
    config = BigBrotherConfig.get_solo()
    webhook = config.optwebhook4
    enabled = config.are_opt_messages4_active

    if not enabled:
        return

    # Get only messages not sent in this cycle
    unsent_messages = OptMessages4.objects.filter(sent_in_cycle=False)

    if not unsent_messages.exists():
        # Reset all messages if cycle is complete
        OptMessages4.objects.update(sent_in_cycle=False)
        unsent_messages = OptMessages4.objects.filter(sent_in_cycle=False)

    if not unsent_messages.exists():
        return  # Still nothing to send

    message = random.choice(list(unsent_messages))
    send_message(message.text, webhook)

    # Mark as sent
    message.sent_in_cycle = True
    message.save()

@shared_task
def BB_send_opt_message5():
    config = BigBrotherConfig.get_solo()
    webhook = config.optwebhook5
    enabled = config.are_opt_messages5_active

    if not enabled:
        return

    # Get only messages not sent in this cycle
    unsent_messages = OptMessages5.objects.filter(sent_in_cycle=False)

    if not unsent_messages.exists():
        # Reset all messages if cycle is complete
        OptMessages5.objects.update(sent_in_cycle=False)
        unsent_messages = OptMessages5.objects.filter(sent_in_cycle=False)

    if not unsent_messages.exists():
        return  # Still nothing to send

    message = random.choice(list(unsent_messages))
    send_message(message.text, webhook)

    # Mark as sent
    message.sent_in_cycle = True
    message.save()


@shared_task
def BB_register_message_tasks():
    logger.info("🔄 Running BB_register_message_tasks...")

    config = BigBrotherConfig.get_solo()

    # Default fallback schedule (12:00 UTC daily)
    default_schedule, _ = CrontabSchedule.objects.get_or_create(
        minute='0',
        hour='12',
        day_of_week='*',
        day_of_month='*',
        month_of_year='*',
        timezone='UTC',
    )

    # Tasks info: name, task path, config schedule attr, active flag attr
    tasks = [
        {
            "name": "BB send daily message",
            "task_path": "aa_bb.tasks.BB_send_daily_messages",
            "schedule_attr": "dailyschedule",
            "active_attr": "are_daily_messages_active",
        },
        {
            "name": "BB send optional message 1",
            "task_path": "aa_bb.tasks.BB_send_opt_message1",
            "schedule_attr": "optschedule1",
            "active_attr": "are_opt_messages1_active",
        },
        {
            "name": "BB send optional message 2",
            "task_path": "aa_bb.tasks.BB_send_opt_message2",
            "schedule_attr": "optschedule2",
            "active_attr": "are_opt_messages2_active",
        },
        {
            "name": "BB send optional message 3",
            "task_path": "aa_bb.tasks.BB_send_opt_message3",
            "schedule_attr": "optschedule3",
            "active_attr": "are_opt_messages3_active",
        },
        {
            "name": "BB send optional message 4",
            "task_path": "aa_bb.tasks.BB_send_opt_message4",
            "schedule_attr": "optschedule4",
            "active_attr": "are_opt_messages4_active",
        },
        {
            "name": "BB send optional message 5",
            "task_path": "aa_bb.tasks.BB_send_opt_message5",
            "schedule_attr": "optschedule5",
            "active_attr": "are_opt_messages5_active",
        },
    ]

    for task_info in tasks:
        name = task_info["name"]
        task_path = task_info["task_path"]
        schedule = getattr(config, task_info["schedule_attr"], None) or default_schedule
        is_active = getattr(config, task_info["active_attr"], False)

        existing_task = PeriodicTask.objects.filter(name=name).first()

        if is_active:
            if existing_task is None:
                PeriodicTask.objects.create(
                    name=name,
                    task=task_path,
                    crontab=schedule,
                    enabled=True,
                )
                logger.info(f"✅ Created '{name}' periodic task with enabled=True")
            else:
                updated = False
                if existing_task.crontab != schedule:
                    existing_task.crontab = schedule
                    updated = True
                if existing_task.task != task_path:
                    existing_task.task = task_path
                    updated = True
                if not existing_task.enabled:
                    existing_task.enabled = True
                    updated = True
                if updated:
                    existing_task.save()
                    logger.info(f"✅ Updated '{name}' periodic task")
                else:
                    logger.info(f"ℹ️ '{name}' periodic task already exists and is up to date")
        else:
            if existing_task:
                existing_task.delete()
                logger.info(f"🗑️ Deleted '{name}' periodic task because messages are disabled")



@shared_task
def BB_run_regular_loa_updates():
    cfg = BigBrotherConfig.get_solo()
    if not cfg.is_loa_active:
        logger.info("LoA feature disabled; skipping updates.")
        return
    member_states = BigBrotherConfig.get_solo().bb_member_states.all()
    qs_profiles = (
        UserProfile.objects
        .filter(state__in=member_states)
        .exclude(main_character=None)
        .select_related("user", "main_character")
    )
    if not qs_profiles.exists():
        logger.info("No member mains found.")
        return
    
    flags = []

    for profile in qs_profiles:
        user = profile.user
        # Determine main_character_id
        try:
            main_id = profile.main_character.character_id
        except Exception:
            main_id = get_character_id(profile)

        # Load main character
        ec = EveCharacter.objects.filter(character_id=main_id).first()
        if not ec:
            continue

        # Find the most recent logoff among all alts
        latest_logoff = None
        for char in get_alts_queryset(ec):
            audit = getattr(char, "characteraudit", None)
            ts = getattr(audit, "last_known_logoff", None) if audit else None
            if ts and (latest_logoff is None or ts > latest_logoff):
                latest_logoff = ts

        if not latest_logoff:
            continue

         # 1) Check and update any existing approved requests for this user
        lr_qs = LeaveRequest.objects.filter(
            user=user,
            status="approved",
        )
        today = date.today()
        in_progress = False
        for lr in lr_qs:
            if lr.start_date <= today <= lr.end_date:
                # it’s now in progress
                if lr.status != "in_progress":
                    lr.status = "in_progress"
                    lr.save(update_fields=["status"])
                    logger.info(
                        "   → Marked LOA %s as in_progress for %s",
                        lr, user.username,
                    )
            elif today > lr.end_date:
                # the approved window has passed
                if lr.status != "finished":
                    lr.status = "finished"
                    lr.save(update_fields=["status"])
                    logger.info(
                        "   → Marked LOA %s as finished for %s",
                        lr, user.username,
                    )
                    send_message(f"##{get_pings('LoA Changed Status')} **{ec}**'s LoA\n- from **{lr.start_date}**\n- to **{lr.end_date}**\n- for **{lr.reason}**\n## has finished")
            if lr.status == "in_progress":
                in_progress = True

        # Compute days since that logoff
        days_since = (timezone.now() - latest_logoff).days
        if days_since > cfg.loa_max_logoff_days:
            if in_progress == False:
                flags.append(f"- **{ec}** was last seen online on {latest_logoff} (**{days_since}** days ago where maximum w/o a LoA request is **{cfg.loa_max_logoff_days}**)")
    if flags:
        flags_text = "\n".join(flags)
        send_message(f"##{get_pings('LoA Inactivity')} Inactive Members Found:\n{flags_text}")


@shared_task
def BB_daily_DB_cleanup():
    from .models import Alliance_names, Character_names, Corporation_names, UserStatus, EntityInfoCache, CorporationInfoCache, AllianceHistoryCache, SovereigntyMapCache
    two_months_ago = timezone.now() - timedelta(days=60)
    flags = []
    #Delete old model entries
    models_to_cleanup = [
        (Alliance_names, "alliance"),
        (Character_names, "character"),
        (Corporation_names, "corporation"),
        (UserStatus, "User Status"),
        (EntityInfoCache, "Entity Info Cache"),
        (CorporationInfoCache, "Corporation Info Cache"),
        (AllianceHistoryCache, "Alliance History Cache"),
        (SovereigntyMapCache, "Sovereignty Map Cache"),
    ]

    for model, name in models_to_cleanup:
        old_entries = model.objects.filter(updated__lt=two_months_ago)
        count, _ = old_entries.delete()
        flags.append(f"- Deleted {count} old {name} records.")


    from .models import (
    ProcessedContract, SusContractNote,
    ProcessedMail, SusMailNote,
    ProcessedTransaction, SusTransactionNote,
    )
    from corptools.models import Contract, MailMessage, CharacterWalletJournalEntry as WalletJournalEntry
    from django.db import transaction
    # -- CONTRACTS --
    # Get all contract_ids that exist in Contract
    existing_contract_ids = set(
        Contract.objects.values_list('contract_id', flat=True)
    )
    
    # Find ProcessedContract entries not in Contract
    orphaned_processed_contracts = ProcessedContract.objects.exclude(contract_id__in=existing_contract_ids)
    orphaned_contract_ids = list(orphaned_processed_contracts.values_list('contract_id', flat=True))
    
    # Delete orphans in SusContractNote (OneToOneField links to ProcessedContract)
    sus_contracts_to_delete = SusContractNote.objects.filter(contract_id__in=orphaned_contract_ids)
    
    with transaction.atomic():
        count_sus = sus_contracts_to_delete.delete()[0]
        count_proc = orphaned_processed_contracts.delete()[0]
    
    flags.append(f"- Deleted {count_proc} old ProcessedContract and {count_sus} SusContractNote records.")
    
    # -- MAILS --
    existing_mail_ids = set(
        MailMessage.objects.values_list('id_key', flat=True)
    )
    
    orphaned_processed_mails = ProcessedMail.objects.exclude(mail_id__in=existing_mail_ids)
    orphaned_mail_ids = list(orphaned_processed_mails.values_list('mail_id', flat=True))
    
    sus_mails_to_delete = SusMailNote.objects.filter(mail_id__in=orphaned_mail_ids)
    
    with transaction.atomic():
        count_sus = sus_mails_to_delete.delete()[0]
        count_proc = orphaned_processed_mails.delete()[0]
    
    flags.append(f"- Deleted {count_proc} old ProcessedMail and {count_sus} SusMailNote records.")
    
    # -- TRANSACTIONS --
    existing_entry_ids = set(
        WalletJournalEntry.objects.values_list('entry_id', flat=True)
    )
    
    orphaned_processed_transactions = ProcessedTransaction.objects.exclude(entry_id__in=existing_entry_ids)
    orphaned_entry_ids = list(orphaned_processed_transactions.values_list('entry_id', flat=True))
    
    sus_transactions_to_delete = SusTransactionNote.objects.filter(transaction_id__in=orphaned_entry_ids)
    
    with transaction.atomic():
        count_sus = sus_transactions_to_delete.delete()[0]
        count_proc = orphaned_processed_transactions.delete()[0]
    
    flags.append(f"- Deleted {count_proc} old ProcessedTransaction and {count_sus} SusTransactionNote records.")

    if flags:
        flags_text = "\n".join(flags)
        send_message(f"### DB Cleanup Complete:\n{flags_text}")