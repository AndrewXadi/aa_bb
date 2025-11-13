import logging
from typing import Optional

from celery import shared_task
from django.utils import timezone
from django.contrib.auth import get_user_model
from allianceauth.authentication.models import UserProfile
from allianceauth.eveonline.models import EveCharacter
from allianceauth.services.modules.discord.models import DiscordUser
from aadiscordbot.tasks import run_task_function
from aadiscordbot.utils.auth import get_discord_user_id
from aadiscordbot.cogs.utils.exceptions import NotAuthenticated
from aadiscordbot.app_settings import get_admins
from corptools.api.helpers import get_alts_queryset

from .models import BigBrotherConfig
from .modelss import TicketToolConfig, PapCompliance, LeaveRequest, ComplianceTicket
from .app_settings import send_message, get_user_profiles, get_character_id

logger = logging.getLogger(__name__)
User = get_user_model()


def corp_check(user) -> bool:
    if not TicketToolConfig.get_solo().corp_check_enabled:
        return True
    """
    Return True if the given user is compliant according to the currently
    selected ComplianceFilter in TicketToolConfig (all chars must comply).
    If no config or no filter is set, default to True (treat as compliant).
    """
    try:
        cfg: Optional[TicketToolConfig] = TicketToolConfig.get_solo()
    except Exception:
        # If the singleton isn't set up yet, be lenient.
        logger.warning("TicketToolConfig.get_solo() failed; treating user as compliant.")
        return True

    if not cfg or not cfg.compliance_filter:
        # No filter chosen -> treat everyone as compliant
        return True

    try:
        # process_filter(user) returns the 'check' boolean for this user,
        # where 'check' already applies the filter and the 'negate' flag.
        return bool(cfg.compliance_filter.process_filter(user))
    except Exception:
        # Misconfiguration or unexpected error: log and be lenient.
        logger.exception("Error while running compliance filter for user id=%s", user.id)
        return True
def lawn_check(user):
    if not TicketToolConfig.get_solo().lawn_check_enabled:
        return True
    return True
def paps_check(user):
    if not TicketToolConfig.get_solo().paps_check_enabled:
        return True
    lr_qs = LeaveRequest.objects.filter(
            user=user,
            status="in_progress",
        ).exists()
    if lr_qs:
        return True
    """
    Check PAP compliance for a given User.
    - If no PapCompliance row exists for their profile -> treat as compliant (True).
    - If row exists and pap_compliant > 0 -> compliant (True).
    - If row exists and pap_compliant == 0 -> non-compliant (False).
    """
    try:
        profile = user.profile  # thanks to related_name='profile'
    except UserProfile.DoesNotExist:
        return True  # no profile at all, treat as compliant

    pc = PapCompliance.objects.filter(user_profile=profile).first()
    if not pc:
        return True

    return pc.pap_compliant > 0
def afk_check(user):
    if not TicketToolConfig.get_solo().afk_check_enabled:
        return True
    tcfg = TicketToolConfig.get_solo()
    max_afk_days = tcfg.Max_Afk_Days
    lr_qs = LeaveRequest.objects.filter(
            user=user,
            status="in_progress",
        ).exists()
    if lr_qs:
        return True
    profile = UserProfile.objects.get(user=user)
    if not profile:
        return False
    try:
        main_id = profile.main_character.character_id
    except Exception:
        main_id = get_character_id(profile)

    # Load main character
    ec = EveCharacter.objects.filter(character_id=main_id).first()
    if not ec:
        return False

    # Find the most recent logoff among all alts
    latest_logoff = None
    for char in get_alts_queryset(ec):
        audit = getattr(char, "characteraudit", None)
        ts = getattr(audit, "last_known_logoff", None) if audit else None
        if ts and (latest_logoff is None or ts > latest_logoff):
            latest_logoff = ts

    if not latest_logoff:
        return False

    # Compute days since that logoff
    days_since = (timezone.now() - latest_logoff).days
    if days_since >= max_afk_days:
        return False
    return True

def discord_check(user):
    if not TicketToolConfig.get_solo().discord_check_enabled:
        return True
    try:
        discord_id = get_discord_user_id(user)
    except NotAuthenticated:
        return False
    return True



@shared_task
def hourly_compliance_check():
    cfg = BigBrotherConfig.get_solo()
    if not cfg.dlc_tickets_active:
        logger.info("Ticket DLC disabled; skipping hourly_compliance_check.")
        return
    tcfg = TicketToolConfig.get_solo()
    max_days = {
        "corp_check": tcfg.corp_check,
        "lawn_check": tcfg.lawn_check,
        "paps_check": tcfg.paps_check,
        "afk_check": tcfg.afk_check,
        "discord_check": tcfg.discord_check,
    }

    # Per-reason reminder frequency (in days)
    reminder_frequency = {
        "corp_check": tcfg.corp_check_frequency,
        "lawn_check": tcfg.lawn_check_frequency,
        "paps_check": tcfg.paps_check_frequency,
        "afk_check": tcfg.afk_check_frequency,
        "discord_check": tcfg.discord_check_frequency,
    }

    reason_checkers = {
        "corp_check": (corp_check, tcfg.corp_check_reason),
        "lawn_check": (lawn_check, tcfg.lawn_check_reason),
        "paps_check": (paps_check, tcfg.paps_check_reason),
        "afk_check": (afk_check, tcfg.afk_check_reason),
        "discord_check": (discord_check, tcfg.discord_check_reason),
    }

    reminder_messages = {
        "corp_check": tcfg.corp_check_reminder,
        "lawn_check": tcfg.lawn_check_reminder,
        "paps_check": tcfg.paps_check_reminder,
        "afk_check": tcfg.afk_check_reminder,
        "discord_check": tcfg.discord_check_reminder,
    }

    now = timezone.now()

    profiles = list(get_user_profiles())
    allowed_users = {p.user for p in profiles}

    # 1. Check compliance reasons
    for UserProfil in get_user_profiles():
        user = UserProfil.user
        if user in tcfg.excluded_users.all():
            continue
        for reason, (checker, msg_template) in reason_checkers.items():
            checked = checker(user)
            if not checked:
                logger.info(f"user{user},reason{reason},checked{checked}")
                ensure_ticket(user, reason)

    # 2. Process existing tickets
    for ticket in ComplianceTicket.objects.all():
        reason = ticket.reason

        if reason == "char_removed" or reason == "awox_kill":
            logger.info(f"reason:{reason}, resolved:{ticket.is_resolved}")
            if ticket.is_resolved:
                logger.info(f"reason:{reason}")
                close_ticket(ticket)
                send_message(f"ticket for <@{ticket.discord_user_id}> resolved")
            continue

        checker, _ = reason_checkers[reason]

        # resolved?
        if ticket.user and checker(ticket.user):
            close_ticket(ticket)
            send_message(f"ticket for <@{ticket.discord_user_id}> resolved")
            continue

        if ticket.user not in allowed_users:
            close_ticket(ticket)
            send_message(f"User <@{ticket.discord_user_id}> is no longer a member, closing ticket")
            continue

        if not ticket.user:
            close_ticket(ticket)
            send_message(f"ticket for <@{ticket.discord_user_id}> closed due to missing auth user")
            continue

        # Reminder logic with per-reason frequency + max-days cap
        days_elapsed = (now - ticket.created_at).days
        if days_elapsed <= 0:
            continue  # don't ping on creation day

        max_dayss = max_days.get(reason, 30)
        if days_elapsed > max_dayss:
            # escalation: ping staff role to kick the user
            mention = f"<@&{tcfg.Role_ID}>"           # role mention
            user_mention = f"<@{ticket.discord_user_id}>"
            msg = (f"⚠️ {mention} please review compliance ticket for {user_mention}. "
                   f"Issue **{reason}** has exceeded {max_dayss} days without resolution. "
                   f"Consider kicking this user.")

            run_task_function.apply_async(
                args=["aa_bb.tasks_bot.send_ticket_reminder"],
                kwargs={
                    "task_args": [ticket.discord_channel_id, ticket.discord_user_id, msg],
                    "task_kwargs": {}
                }
            )
            continue

        # last_reminder_sent acts as "last day number we pinged"
        freq_days = reminder_frequency.get(reason, 1)
        last_day_pinged = ticket.last_reminder_sent or 0
        if (days_elapsed - last_day_pinged) < freq_days:
            continue  # not time to remind yet

        # Build the message: mention the user + role + days left
        days_left = max_dayss - days_elapsed
        mention = f"{ticket.discord_user_id}"
        template = reminder_messages[reason]  # must support {namee}, {role}, {days}
        if reason == "paps_check":
            msg = template.format(days=days_left)
        else:
            msg = template.format(namee=mention, role=tcfg.Role_ID, days=days_left)

        # Queue the bot-side reminder (ensure task_kwargs is present)
        run_task_function.apply_async(
            args=["aa_bb.tasks_bot.send_ticket_reminder"],
            kwargs={
                "task_args": [ticket.discord_channel_id, ticket.discord_user_id, msg],
                "task_kwargs": {}
            }
        )

        # Mark today as reminded so we don't ping again today
        ticket.last_reminder_sent = days_elapsed
        ticket.save(update_fields=["last_reminder_sent"])

    # Rebalance ticket categories after processing tickets
    try:
        run_task_function.apply_async(
            args=["aa_bb.tasks_bot.rebalance_ticket_categories"],
            kwargs={
                "task_args": [],
                "task_kwargs": {}
            }
        )
    except Exception:
        # Non-fatal if scheduling fails
        pass


def ensure_ticket(user, reason):
    tcfg = TicketToolConfig.get_solo()
    max_afk_days = tcfg.Max_Afk_Days
    reason_checkers = {
        "corp_check": (corp_check, tcfg.corp_check_reason),
        "lawn_check": (lawn_check, tcfg.lawn_check_reason),
        "paps_check": (paps_check, tcfg.paps_check_reason),
        "afk_check": (afk_check, tcfg.afk_check_reason),
        "discord_check": (discord_check, tcfg.discord_check_reason),
    }
    try:
        discord_id = get_discord_user_id(user)
        username = ""
        _, msg_template = reason_checkers[reason]
        if reason == "afk_check":
            ticket_message = msg_template.format(namee=discord_id, role=tcfg.Role_ID, days=max_afk_days)
        elif reason == "discord_check":
            username = user.username
            ticket_message = msg_template.format(namee=username, role=tcfg.Role_ID, days=max_afk_days)
        else:
            ticket_message = msg_template.format(namee=discord_id, role=tcfg.Role_ID)
    except NotAuthenticated:
        # User has no Discord → fall back to first superuser with Discord linked
        superusers = User.objects.filter(is_superuser=True)
        username = user.username
        discord_user = None

        # Prefer a superuser with a linked Discord account
        if superusers.exists():
            discord_user = DiscordUser.objects.filter(user__in=superusers).first()

        # If no superuser exists or none have Discord linked, try the first configured Discord admin
        if not discord_user:
            try:
                admin_uids = get_admins() or []
            except Exception:
                admin_uids = []

            if admin_uids:
                discord_user = DiscordUser.objects.filter(uid__in=admin_uids).first()

        # If still nothing, log and notify, then stop
        if not discord_user:
            logger.error(f"Failed to create a {reason} ticket for {username}. No eligible fallback found: no superuser or Discord admin with Discord linked.")
            send_message(f"Failed to create a {reason} ticket for {username}. No eligible fallback found: no superuser or Discord admin with Discord linked.")
            return

        discord_id = discord_user.uid
        _, msg_template = reason_checkers[reason]
        if reason == "afk_check":
            ticket_message = (
                f"⚠️ Compliance issue for **{user.username}** "
                f"(no Discord linked!)\n\n"
                f"{msg_template.format(namee=user.username, role=tcfg.Role_ID, days=max_afk_days)}"
            )
        elif reason == "discord_check":
            ticket_message = (
                f"⚠️ Compliance issue for **{user.username}** "
                f"(no Discord linked!)\n\n"
                f"{msg_template.format(namee=user.username, role=tcfg.Role_ID, days=max_afk_days)}"
            )
        else:
            ticket_message = (
                f"⚠️ Compliance issue for **{user.username}** "
                f"(no Discord linked!)\n\n"
                f"{msg_template.format(namee=user.username, role=tcfg.Role_ID)}"
            )

    # prevent duplicates
    exists = ComplianceTicket.objects.filter(
        user=user, reason=reason, is_resolved=False
    ).exists()
    if not exists:
        send_message(f"ticket for {user.username} created, reason - {reason}")
        run_task_function.apply_async(
            args=["aa_bb.tasks_bot.create_compliance_ticket"],
            kwargs={
                "task_args": [user.id, discord_id, reason, ticket_message],
                "task_kwargs": {}
            }
        )


def close_ticket(ticket):
    run_task_function.delay(
        "aa_bb.tasks_bot.close_ticket_channel",
        task_args=[ticket.discord_channel_id],
        task_kwargs={}
    )
    ticket.delete()

def close_char_removed_ticket(ticket):
    ticket.is_resolved = True
    ticket.save()
