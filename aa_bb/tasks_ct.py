# myapp/tasks/kickstart_selective.py
# This file can live outside the corptools app.

import datetime
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence

from celery import shared_task
from django.utils import timezone
from allianceauth.services.hooks import get_extension_logger
from esi.errors import TokenExpiredError, TokenError, TokenInvalidError
from esi.models import Token
from .app_settings import send_message
from corptools.models import EveCharacter

# Corptools imports (read-only usage)
from corptools import app_settings
from corptools.models import CharacterAudit, CorptoolsConfiguration

# Per-module update tasks (exact names match corptools.tasks.character)
from corptools.tasks.character import (
    update_char_assets,
    update_char_contacts,
    update_char_notifications,
    update_char_roles,
    update_char_titles,
    update_char_mining_ledger,
    update_char_wallet,
    update_char_transactions,
    update_char_orders,
    update_char_order_history,
    update_char_contracts,
    update_char_skill_list,
    update_char_skill_queue,
    update_char_clones,
    update_char_mail,
    update_char_loyaltypoints,
    update_char_industry_jobs, 
    update_char_corp_history, 
    update_char_location,
)

logger = get_extension_logger(__name__)


@dataclass(frozen=True)
class ModuleRule:
    name: str
    enabled_flag: Optional[str]            # app_settings enable flag; None => no flag, handle specially
    runtime_disable_attr: Optional[str]    # CorptoolsConfiguration temp-disable attr; None => no runtime toggle
    last_update_fields: Sequence[str]      # CharacterAudit fields; if none exist on model => skip module
    required_scopes: Sequence[str]         # ESI scopes; empty => no scopes required
    tasks: Sequence[Callable]
    extra_predicate: Optional[Callable[[], bool]] = None

def _safe_identity_refresh(char_id: int):
    try:
        # This mirrors the button: ensure the EveCharacter row is up to date
        EveCharacter.objects.update_character(char_id)
    except Exception as e:
        # Don’t fail the whole sweep; just log and continue.
        logger.warning(f"Identity refresh failed for {char_id}: {e}", exc_info=True)

def _is_enabled(flag_name: Optional[str]) -> bool:
    if not flag_name:
        return True  # no dedicated enable flag; treat as enabled (we'll still honor runtime disable)
    return bool(getattr(app_settings, flag_name, False))


def _not_temp_disabled(conf: CorptoolsConfiguration, attr: Optional[str]) -> bool:
    if not attr:
        return True
    # If attr is missing on conf, treat as disabled (skip) to be safe
    return bool(getattr(conf, attr, False) is False)


def _available_fields(audit: CharacterAudit, fields: Iterable[str]) -> List[str]:
    return [f for f in fields if hasattr(audit, f)]


def _is_stale_value(dt, cutoff) -> bool:
    # None => never updated => stale
    return (dt is None) or (dt <= cutoff)


def _any_available_field_stale(audit: CharacterAudit, fields: Iterable[str], cutoff) -> bool:
    avail = _available_fields(audit, fields)
    if not avail:
        # Per your rule: if a module doesn't expose a last_update field, skip it
        return False
    return any(_is_stale_value(getattr(audit, f, None), cutoff) for f in avail)


def _has_valid_token_with_scopes(char_id: int, scopes: Sequence[str]) -> bool:
    if not scopes:
        # No scopes needed for this module
        return True
    token = Token.get_token(char_id, scopes)
    if not token:
        return False
    try:
        return bool(token.valid_access_token())
    except (TokenExpiredError, TokenInvalidError) as e:
        # Expired or invalid refresh token: skip this character for this module
        logger.info(f"Skipping char {char_id}: unusable token for scopes {scopes} ({e.__class__.__name__})")
        return False
    except Exception as e:
        # Belt-and-suspenders: never let a single token error kill the whole sweep
        logger.warning(f"Unexpected token error for char {char_id} (scopes {scopes}): {e}", exc_info=True)
        return False


# NOTE on scopes:
# - Corp History is public; we require no scopes.
# - Location typically needs 'esi-location.read_location.v1' and 'esi-location.read_ship_type.v1'.
#   If your deployment uses different scopes, adjust here.

RULES: List[ModuleRule] = [
    # Corp History (aka "Public Data")
    ModuleRule(
        name="Corp History",
        enabled_flag=None,                          # no explicit app_settings enable flag
        runtime_disable_attr="disable_update_pub_data",
        last_update_fields=["last_update_pub_data"],
        required_scopes=[],                         # public endpoints; no scopes
        tasks=[update_char_corp_history],
        # queued with force_refresh=True below, mirroring how update_character does it
    ),

    # Location (only if CharacterAudit tracks a last_update field for it)
    ModuleRule(
        name="Location",
        enabled_flag="CT_CHAR_LOCATIONS_MODULE",
        runtime_disable_attr="disable_update_location",
        last_update_fields=["last_update_location", "last_update_locations"],
        required_scopes=["esi-location.read_location.v1", "esi-location.read_ship_type.v1"],
        tasks=[update_char_location],
    ),

    # Existing modules, unchanged:
    ModuleRule(
        name="Assets",
        enabled_flag="CT_CHAR_ASSETS_MODULE",
        runtime_disable_attr="disable_update_assets",
        last_update_fields=["last_update_assets"],
        required_scopes=["esi-assets.read_assets.v1"],
        tasks=[update_char_assets],
    ),
    ModuleRule(
        name="Contacts",
        enabled_flag="CT_CHAR_CONTACTS_MODULE",
        runtime_disable_attr="disable_update_contacts",
        last_update_fields=["last_update_contacts"],
        required_scopes=["esi-characters.read_contacts.v1"],
        tasks=[update_char_contacts],
    ),
    ModuleRule(
        name="Notifications",
        enabled_flag="CT_CHAR_NOTIFICATIONS_MODULE",
        runtime_disable_attr="disable_update_notif",
        last_update_fields=["last_update_notif"],
        required_scopes=["esi-characters.read_notifications.v1"],
        tasks=[update_char_notifications],
    ),
    ModuleRule(
        name="Roles/Titles",
        enabled_flag="CT_CHAR_ROLES_MODULE",
        runtime_disable_attr="disable_update_roles",
        last_update_fields=["last_update_roles", "last_update_titles"],
        required_scopes=[
            "esi-characters.read_titles.v1",
            "esi-characters.read_corporation_roles.v1",
        ],
        tasks=[update_char_roles, update_char_titles],
    ),
    ModuleRule(
        name="Industry",
        enabled_flag="CT_CHAR_INDUSTRY_MODULE",
        runtime_disable_attr="disable_update_indy",
        last_update_fields=["last_update_indy"],
        required_scopes=["esi-industry.read_character_jobs.v1"],
        tasks=[update_char_industry_jobs],
    ),
    ModuleRule(
        name="Mining",
        enabled_flag="CT_CHAR_MINING_MODULE",
        runtime_disable_attr="disable_update_mining",
        last_update_fields=["last_update_mining"],
        required_scopes=["esi-industry.read_character_mining.v1"],
        tasks=[update_char_mining_ledger],
    ),
    ModuleRule(
        name="Wallet/Markets",
        enabled_flag="CT_CHAR_WALLET_MODULE",
        runtime_disable_attr="disable_update_wallet",
        last_update_fields=["last_update_wallet", "last_update_orders"],
        required_scopes=[
            "esi-wallet.read_character_wallet.v1",
            "esi-markets.read_character_orders.v1",
        ],
        tasks=[update_char_wallet, update_char_transactions, update_char_orders, update_char_order_history],
    ),
    ModuleRule(
        name="Contracts",
        enabled_flag="CT_CHAR_WALLET_MODULE",
        runtime_disable_attr="disable_update_wallet",
        last_update_fields=["last_update_contracts"],
        required_scopes=["esi-contracts.read_character_contracts.v1"],
        tasks=[update_char_contracts],
        extra_predicate=lambda: (not getattr(app_settings, "CT_CHAR_PAUSE_CONTRACTS", False)),
    ),
    ModuleRule(
        name="Skills",
        enabled_flag="CT_CHAR_SKILLS_MODULE",
        runtime_disable_attr="disable_update_skills",
        last_update_fields=["last_update_skills", "last_update_skill_que"],
        required_scopes=[
            "esi-skills.read_skills.v1",
            "esi-skills.read_skillqueue.v1",
        ],
        tasks=[update_char_skill_list, update_char_skill_queue],
    ),
    ModuleRule(
        name="Clones",
        enabled_flag="CT_CHAR_CLONES_MODULE",
        runtime_disable_attr="disable_update_clones",
        last_update_fields=["last_update_clones"],
        required_scopes=[
            "esi-clones.read_clones.v1",
            "esi-clones.read_implants.v1",
        ],
        tasks=[update_char_clones],
    ),
    ModuleRule(
        name="Mail",
        enabled_flag="CT_CHAR_MAIL_MODULE",
        runtime_disable_attr="disable_update_mails",
        last_update_fields=["last_update_mails"],
        required_scopes=["esi-mail.read_mail.v1"],
        tasks=[update_char_mail],
    ),
    ModuleRule(
        name="Loyalty Points",
        enabled_flag="CT_CHAR_LOYALTYPOINTS_MODULE",
        runtime_disable_attr="disable_update_loyaltypoints",
        last_update_fields=["last_update_loyaltypoints"],
        required_scopes=["esi-characters.read_loyalty.v1"],
        tasks=[update_char_loyaltypoints],
    ),
]


@shared_task
def kickstart_stale_ct_modules(days_stale: int = 2, limit: Optional[int] = None, dry_run: bool = False) -> str:
    """
    For each CharacterAudit with ownership:
      - For each module rule:
          * require module enabled (if it has an enable flag),
          * require runtime NOT temp-disabled (if it has a toggle),
          * require a valid token holding that module's scopes (if any),
          * if ANY available last_update_* is older than `days_stale`, enqueue ONLY that module's task(s)
            with force_refresh=True.
    No extras are queued here; corptools' orchestration can handle them elsewhere.
    """
    conf = CorptoolsConfiguration.get_solo()
    cutoff = timezone.now() - datetime.timedelta(days=days_stale)
    cutoff_really_stale = timezone.now() - datetime.timedelta(days=days_stale,hours=6)

    qs = CharacterAudit.objects.filter(
        character__character_ownership__isnull=False
    ).select_related("character")

    if limit:
        qs = qs[: int(limit)]

    total_chars = 0
    total_tasks = 0
    updated_names = []

    for audit in qs.iterator():
        total_chars += 1
        char_id = audit.character.character_id
        per_char_enqueues = 0
        kickedcharactermodel = False

        for rule in RULES:
            really_stale = _any_available_field_stale(audit, rule.last_update_fields, cutoff_really_stale)
            if not _is_enabled(rule.enabled_flag):
                continue
            if not _not_temp_disabled(conf, rule.runtime_disable_attr):
                continue
            if rule.extra_predicate and not rule.extra_predicate():
                continue
            if not _has_valid_token_with_scopes(char_id, rule.required_scopes):
                continue
            if not _any_available_field_stale(audit, rule.last_update_fields, cutoff):
                continue

            for task in rule.tasks:
                if dry_run:
                    logger.info(f"[DRY-RUN] Would queue {task.name} for char {char_id} (module={rule.name})")
                else:
                    if not kickedcharactermodel and really_stale:
                        _safe_identity_refresh(char_id)
                        kickedcharactermodel = True
                    task.apply_async(
                        args=[char_id],
                        kwargs={"force_refresh": True},
                        priority=6,
                        countdown=1,
                    )
                    total_tasks += 1
                    per_char_enqueues += 1

        if per_char_enqueues:
            updated_names.append(audit.character.character_name)   # <-- store name
            logger.info(f"Queued {per_char_enqueues} module task(s) for char {audit.character.character_name} ({char_id})")
    if updated_names:
        names_str = ", ".join(updated_names)
        summary = (
            f"## CT audit complete:\n"
            f"- Processed {total_chars} characters\n"
            f"- Found and queued for update {total_tasks} modules (stale > {days_stale}d).\n"
            f"- Updated characters:\n{names_str}"
        )
        send_message(summary)
    return summary