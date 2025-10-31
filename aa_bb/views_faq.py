from django.apps import apps as django_apps
from django.db import OperationalError, ProgrammingError
from django.contrib.auth.decorators import login_required, permission_required
from django.core.handlers.wsgi import WSGIRequest
from django.shortcuts import render
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django_celery_beat.models import PeriodicTask

from .models import BigBrotherConfig, PapsConfig
from .modelss import TicketToolConfig


@login_required
@permission_required("aa_bb.basic_access")
def manual_cards(request: WSGIRequest):
    """Manual tab: card reference."""
    return render(request, "faq/cards.html")


@login_required
@permission_required("aa_bb.basic_access")
def manual_settings(request: WSGIRequest):
    """Manual tab: BigBrotherConfig settings."""
    return render(request, "faq/settings_bigbrother.html")


@login_required
@permission_required("aa_bb.basic_access")
def manual_settings_bb(request: WSGIRequest):
    """Alias for BigBrotherConfig settings."""
    return render(request, "faq/settings_bigbrother.html")


@login_required
@permission_required("aa_bb.basic_access")
def manual_settings_paps(request: WSGIRequest):
    """Manual tab: PapsConfig settings."""
    return render(request, "faq/settings_paps.html")


@login_required
@permission_required("aa_bb.basic_access")
def manual_settings_tickets(request: WSGIRequest):
    """Manual tab: TicketToolConfig settings."""
    return render(request, "faq/settings_tickets.html")


@login_required
@permission_required("aa_bb.basic_access")
def manual_modules(request: WSGIRequest):
    """Manual tab: module status overview with live checks."""
    cfg = BigBrotherConfig.get_solo()
    paps_cfg = PapsConfig.get_solo()
    ticket_cfg_error = None
    try:
        ticket_cfg = TicketToolConfig.get_solo()
    except (OperationalError, ProgrammingError) as exc:
        ticket_cfg = None
        ticket_cfg_error = str(exc)

    task_name = "BB run regular updates"
    periodic_task = PeriodicTask.objects.filter(name=task_name).first()

    corptools_installed = django_apps.is_installed("corptools")
    charlink_installed = django_apps.is_installed("charlink")
    blacklist_installed = django_apps.is_installed("blacklist")
    discordbot_installed = django_apps.is_installed("aadiscordbot")

    modules = []

    def code(name: str):
        return format_html("<code>{}</code>", name)

    def register_issue(issues: list, actions: list, condition: bool, issue_text, action_text=None):
        if condition:
            issues.append(issue_text)
            if action_text and action_text not in actions:
                actions.append(action_text)

    def make_module(name, summary, issues, actions, info=None, active_override=None):
        info = info or []
        issues = list(dict.fromkeys(issues))
        actions = list(dict.fromkeys(actions))
        active = active_override if active_override is not None else (len(issues) == 0)
        if issues:
            details = issues + info
        else:
            details = [format_html("{}", _("All requirements satisfied."))] + info
        if not actions:
            actions = [format_html("{}", _("No action needed."))] if not issues else [format_html("{}", _("Review configuration and retry the checks."))]
        return {
            "name": name,
            "summary": summary,
            "active": bool(active),
            "details": details,
            "actions": actions,
        }

    # BigBrother Core Dashboard
    core_issues, core_actions, core_info = [], [], []
    register_issue(
        core_issues,
        core_actions,
        not cfg.token,
        format_html("{} is empty.", code("BigBrotherConfig.token")),
        format_html("Paste the issued token into {} and save the singleton.", code("BigBrotherConfig")),
    )
    register_issue(
        core_issues,
        core_actions,
        not cfg.is_active,
        format_html("{} reports the plugin as inactive.", code("BigBrotherConfig.is_active")),
        format_html("Validate the token (check Celery logs) and rerun the updater until {} flips to True.", code("is_active")),
    )
    register_issue(
        core_issues,
        core_actions,
        periodic_task is None,
        format_html("Celery periodic task {} is missing.", code(task_name)),
        format_html("Create the periodic task in Django admin → Periodic tasks and restart Celery workers."),
    )
    if periodic_task is not None:
        register_issue(
            core_issues,
            core_actions,
            not periodic_task.enabled,
            format_html("Celery periodic task {} exists but is disabled.", code(task_name)),
            format_html("Enable the task in Django admin → Periodic tasks and restart Celery workers."),
        )
        if periodic_task.last_run_at:
            core_info.append(
                format_html(
                    "Last successful update: {} UTC.",
                    timezone.localtime(periodic_task.last_run_at).strftime("%Y-%m-%d %H:%M"),
                )
            )
    modules.append(
        make_module(
            _("BigBrother Core Dashboard"),
            _("Pilot-focused dashboard that streams compliance cards."),
            core_issues,
            core_actions,
            info=core_info,
        )
    )

    # CorpBrother Dashboard
    corp_issues, corp_actions, corp_info = [], [], []
    register_issue(
        corp_issues,
        corp_actions,
        not cfg.is_active,
        format_html("{} must be True for CorpBrother to load.", code("BigBrotherConfig.is_active")),
        format_html("Resolve the core activation issues listed above."),
    )
    register_issue(
        corp_issues,
        corp_actions,
        not corptools_installed,
        format_html("Dependency {} is not installed.", code("corptools")),
        format_html("Install allianceauth-corptools and add it to {}.", code("INSTALLED_APPS")),
    )
    register_issue(
        corp_issues,
        corp_actions,
        not charlink_installed,
        format_html("Dependency {} is not installed.", code("charlink")),
        format_html("Install allianceauth-charlink and add it to {}.", code("INSTALLED_APPS")),
    )
    if corptools_installed:
        corp_info.append(format_html("{} detected.", code("corptools")))
    if charlink_installed:
        corp_info.append(format_html("{} detected.", code("charlink")))
    modules.append(
        make_module(
            _("CorpBrother Dashboard"),
            _("Corporation-wide audit dashboard for recruiters and directors."),
            corp_issues,
            corp_actions,
            info=corp_info,
        )
    )

    # Leave of Absence
    loa_issues, loa_actions, loa_info = [], [], []
    register_issue(
        loa_issues,
        loa_actions,
        not cfg.is_loa_active,
        format_html("{} is disabled.", code("BigBrotherConfig.is_loa_active")),
        format_html("Enable the toggle in BigBrotherConfig and restart AllianceAuth."),
    )
    if not discordbot_installed:
        register_issue(
            loa_issues,
            loa_actions,
            True,
            format_html("{} app is not installed; Discord notifications will fail.", code("aadiscordbot")),
            format_html("Install and configure aadiscordbot for ticket and LoA notifications."),
        )
    if cfg.loawebhook:
        loa_info.append(format_html("LoA webhook configured: {}", cfg.loawebhook))
    modules.append(
        make_module(
            _("Leave of Absence"),
            _("AllianceAuth LoA request pages and Discord alerts."),
            loa_issues,
            loa_actions,
            info=loa_info,
        )
    )

    # PAP Statistics
    paps_issues, paps_actions, paps_info = [], [], []
    register_issue(
        paps_issues,
        paps_actions,
        not cfg.is_paps_active,
        format_html("{} is disabled.", code("BigBrotherConfig.is_paps_active")),
        format_html("Enable PAP stats in BigBrotherConfig and restart AllianceAuth."),
    )
    register_issue(
        paps_issues,
        paps_actions,
        not corptools_installed,
        format_html("Dependency {} is not installed.", code("corptools")),
        format_html("Install allianceauth-corptools and add it to {}.", code("INSTALLED_APPS")),
    )
    if paps_cfg:
        paps_info.append(
            format_html("Required PAPs per month: {}", paps_cfg.required_paps)
        )
        paps_info.append(
            format_html("Corp modifier: {} / Lawn modifier: {} / IMP modifier: {}", paps_cfg.corp_modifier, paps_cfg.lawn_modifier, paps_cfg.imp_modifier)
        )
    modules.append(
        make_module(
            _("PAP Statistics"),
            _("Monthly PAP entry form and compliance tracker."),
            paps_issues,
            paps_actions,
            info=paps_info,
        )
    )

    # Cache Warmer
    warmer_issues, warmer_actions = [], []
    register_issue(
        warmer_issues,
        warmer_actions,
        not cfg.is_warmer_active,
        format_html("{} is disabled.", code("BigBrotherConfig.is_warmer_active")),
        format_html("Enable the cache warmer or increase your gunicorn timeout to avoid stream resets."),
    )
    modules.append(
        make_module(
            _("Cache Warmer"),
            _("Background task that preloads contracts, mails and transactions before streaming cards."),
            warmer_issues,
            warmer_actions,
        )
    )

    # Daily notifications
    daily_issues, daily_actions, daily_info = [], [], []
    register_issue(
        daily_issues,
        daily_actions,
        not cfg.are_daily_messages_active,
        format_html("{} is disabled.", code("BigBrotherConfig.are_daily_messages_active")),
        format_html("Enable daily messages in BigBrotherConfig and restart Celery workers."),
    )
    register_issue(
        daily_issues,
        daily_actions,
        not cfg.dailywebhook,
        format_html("{} is empty.", code("BigBrotherConfig.dailywebhook")),
        format_html("Set a Discord webhook URL in {}.", code("dailywebhook")),
    )
    register_issue(
        daily_issues,
        daily_actions,
        cfg.dailyschedule is None,
        format_html("{} is not linked to a schedule.", code("BigBrotherConfig.dailyschedule")),
        format_html("Create a crontab/interval schedule and assign it to {}.", code("dailyschedule")),
    )
    if not discordbot_installed:
        register_issue(
            daily_issues,
            daily_actions,
            True,
            format_html("{} app is not installed; daily Discord posts will fail.", code("aadiscordbot")),
            format_html("Install and configure aadiscordbot."),
        )
    if cfg.dailyschedule:
        daily_info.append(format_html("Schedule: {}", cfg.dailyschedule))
    modules.append(
        make_module(
            _("Daily Notifications"),
            _("Scheduled digest sent via Discord webhook."),
            daily_issues,
            daily_actions,
            info=daily_info,
        )
    )

    # Optional notification streams
    for idx in range(1, 6):
        stream_name = _("Optional Notification Stream %(number)s") % {"number": idx}
        summary = _("Additional Discord webhook stream number %(number)s.") % {"number": idx}
        issues, actions, info = [], [], []
        flag = getattr(cfg, f"are_opt_messages{idx}_active")
        webhook = getattr(cfg, f"optwebhook{idx}")
        schedule = getattr(cfg, f"optschedule{idx}")

        if not flag:
            register_issue(
                issues,
                actions,
                True,
                format_html("{} is disabled.", code(f"are_opt_messages{idx}_active")),
                format_html("Enable the toggle if you want to send this stream."),
            )
        if flag and not webhook:
            register_issue(
                issues,
                actions,
                True,
                format_html("{} is empty.", code(f"optwebhook{idx}")),
                format_html("Set a Discord webhook URL in {}.", code(f"optwebhook{idx}")),
            )
        if flag and schedule is None:
            register_issue(
                issues,
                actions,
                True,
                format_html("{} is not linked to a schedule.", code(f"optschedule{idx}")),
                format_html("Assign a crontab/interval schedule to {}.", code(f"optschedule{idx}")),
            )
        if flag and not discordbot_installed:
            register_issue(
                issues,
                actions,
                True,
                format_html("{} app is not installed; Discord posts will fail.", code("aadiscordbot")),
                format_html("Install and configure aadiscordbot."),
            )
        if flag and webhook:
            info.append(format_html("Webhook configured."))
        if flag and schedule:
            info.append(format_html("Schedule: {}", schedule))

        modules.append(
            make_module(
                stream_name,
                summary,
                issues,
                actions,
                info=info,
                active_override=flag and all(cond for cond in [webhook, schedule, discordbot_installed]),
            )
        )

    # LoA inactivity alerts (AFK tickets)
    afk_issues, afk_actions, afk_info = [], [], []
    register_issue(
        afk_issues,
        afk_actions,
        not cfg.is_loa_active,
        format_html("{} must be enabled for LoA inactivity monitoring.", code("BigBrotherConfig.is_loa_active")),
        format_html("Enable the LoA module in BigBrotherConfig."),
    )
    if ticket_cfg is None:
        register_issue(
            afk_issues,
            afk_actions,
            True,
            format_html("TicketToolConfig could not be loaded ({}).", ticket_cfg_error or _("database schema mismatch")),
            format_html("Run {} to apply pending migrations.", format_html("<code>manage.py migrate aa_bb</code>")),
        )
    else:
        register_issue(
            afk_issues,
            afk_actions,
            not ticket_cfg.afk_check_enabled,
            format_html("{} is disabled.", code("TicketToolConfig.afk_check_enabled")),
            format_html("Toggle AFK checks on in TicketToolConfig."),
        )
        register_issue(
            afk_issues,
            afk_actions,
            ticket_cfg.Max_Afk_Days <= 0,
            format_html("{} should be greater than zero.", code("TicketToolConfig.Max_Afk_Days")),
            format_html("Set a sensible threshold (e.g. 7) in TicketToolConfig."),
        )
        if not discordbot_installed:
            register_issue(
                afk_issues,
                afk_actions,
                True,
                format_html("{} app is not installed; ticket notifications will fail.", code("aadiscordbot")),
                format_html("Install and configure aadiscordbot."),
            )
        afk_info.append(format_html("Current inactivity threshold: {} day(s).", ticket_cfg.Max_Afk_Days))
    modules.append(
        make_module(
            _("LoA inactivity alerts"),
            _("Ticket automation that warns when users stop logging in without an LoA."),
            afk_issues,
            afk_actions,
            info=afk_info,
        )
    )

    # Ticket automation (general)
    ticket_issues, ticket_actions, ticket_info = [], [], []
    if ticket_cfg is None:
        register_issue(
            ticket_issues,
            ticket_actions,
            True,
            format_html("TicketToolConfig could not be loaded ({}).", ticket_cfg_error or _("database schema mismatch")),
            format_html("Run {} to apply pending migrations.", format_html("<code>manage.py migrate aa_bb</code>")),
        )
    else:
        register_issue(
            ticket_issues,
            ticket_actions,
            not discordbot_installed,
            format_html("{} app is not installed.", code("aadiscordbot")),
            format_html("Install aadiscordbot and configure the Discord bot token."),
        )
        register_issue(
            ticket_issues,
            ticket_actions,
            ticket_cfg.Category_ID in (None, 0),
            format_html("{} is not set.", code("TicketToolConfig.Category_ID")),
            format_html("Provide the Discord category ID where tickets should be created."),
        )
        if ticket_cfg.staff_roles:
            ticket_info.append(format_html("Staff roles: {}", ticket_cfg.staff_roles))
        else:
            register_issue(
                ticket_issues,
                ticket_actions,
                True,
                format_html("{} has not been defined; only the bot will see tickets.", code("TicketToolConfig.staff_roles")),
                format_html("Add a comma-separated list of Discord role IDs to {}.", code("staff_roles")),
            )
        register_issue(
            ticket_issues,
            ticket_actions,
            not charlink_installed,
            format_html("{} is not installed; char → user mapping may be limited.", code("charlink")),
            format_html("Install allianceauth-charlink to improve ticket context."),
        )
    modules.append(
        make_module(
            _("Ticket automation"),
            _("Discord-based compliance ticket workflow driven by TicketToolConfig."),
            ticket_issues,
            ticket_actions,
            info=ticket_info,
        )
    )

    # Blacklist integration
    blacklist_issues, blacklist_actions = [], []
    register_issue(
        blacklist_issues,
        blacklist_actions,
        not blacklist_installed,
        format_html("{} app is not installed; Corp Blacklist features will be unavailable.", code("blacklist")),
        format_html("Install allianceauth-blacklist and add it to {}.", code("INSTALLED_APPS")),
    )
    modules.append(
        make_module(
            _("Blacklist integration"),
            _("Allows BigBrother to add characters to AllianceAuth Blacklist directly from the dashboard."),
            blacklist_issues,
            blacklist_actions,
        )
    )

    return render(request, "faq/modules.html", {"modules": modules})


@login_required
@permission_required("aa_bb.basic_access")
def manual_faq(request: WSGIRequest):
    """Manual tab: FAQ content."""
    return render(request, "faq/faq.html")
