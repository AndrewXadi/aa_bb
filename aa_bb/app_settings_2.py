from django.apps import apps
from django.conf import settings
from esi.clients import EsiClientProvider
from django.utils import timezone
from datetime import timedelta
from .models import CorporationInfoCache, Alliance_names
import re
import os


import logging
logger = logging.getLogger(__name__)
esi = EsiClientProvider()
TTL_SHORT = timedelta(hours=4)

def get_main_corp_id():
    from allianceauth.eveonline.models import EveCharacter
    try:
        char = EveCharacter.objects.filter(character_ownership__user__is_superuser=True).first()
        if char:
            return char.corporation_id
    except Exception:
        pass
    return 123456789888888  # Fallback

def get_owner_name():
    from allianceauth.eveonline.models import EveCharacter
    try:
        char = EveCharacter.objects.filter(character_ownership__user__is_superuser=True).first()
        if char:
            return char.character_name
    except Exception:
        pass
    return None  # Fallback

def get_corp_info(corp_id):
    if not (1_000_000 <= corp_id < 3_000_000_000):  # Valid range for known corp IDs
        logger.warning(f"Skipping invalid corp_id: {corp_id}")
        return {
            "name": f"Invalid Corp ({corp_id})",
            "alliance_id": None
        }

    # Try DB cache first
    try:
        entry = CorporationInfoCache.objects.get(pk=corp_id)
        if timezone.now() - entry.updated < TTL_SHORT:
            return {"name": entry.name, "alliance_id": getattr(entry, "alliance_id", None)}
        else:
            entry.delete()
    except CorporationInfoCache.DoesNotExist:
        pass

    # Fetch from ESI
    member_count = 0
    try:
        #logger.debug(f"Fetching corp info for corp_id {corp_id}")
        result = esi.client.Corporation.get_corporations_corporation_id(
            corporation_id=corp_id
        ).results()
        data = {
            "name": result.get("name", f"Unknown ({corp_id})"),
            # alliance_id is not part of CorporationInfoCache model, we return it only
            "alliance_id": result.get("alliance_id"),
        }
        member_count = result.get("member_count", 0)
    except Exception as e:
        logger.warning(f"Error fetching corp {corp_id}: {e}")
        data = {"name": f"Unknown Corp ({corp_id})", "alliance_id": None}

    # Store/update DB cache
    try:
        CorporationInfoCache.objects.update_or_create(
            corp_id=corp_id,
            defaults={"name": data["name"], "member_count": member_count},
        )
    except Exception:
        pass

    return data
    
def get_alliance_name(alliance_id):
    if not alliance_id:
        return "None"
    # Try DB cache first with 4h TTL
    try:
        rec = Alliance_names.objects.get(pk=alliance_id)
        if timezone.now() - rec.updated < TTL_SHORT:
            return rec.name
    except Alliance_names.DoesNotExist:
        rec = None

    try:
        result = esi.client.Alliance.get_alliances_alliance_id(
            alliance_id=alliance_id
        ).results()
        name = result.get("name", f"Unknown ({alliance_id})")
    except Exception as e:
        logger.warning(f"Error fetching alliance {alliance_id}: {e}")
        name = f"Unknown ({alliance_id})"

    try:
        Alliance_names.objects.update_or_create(pk=alliance_id, defaults={"name": name})
    except Exception:
        pass

    return name

def get_site_url():  # regex sso url
    regex = r"^(.+)\/s.+"
    matches = re.finditer(regex, settings.ESI_SSO_CALLBACK_URL, re.MULTILINE)
    url = "http://"

    for m in matches:
        url = m.groups()[0]  # first match

    return url

def get_contact_email():  # regex sso url
    return settings.ESI_USER_CONTACT_EMAIL


def aablacklist_active():
    return apps.is_installed("blacklist")


def afat_active():
    return apps.is_installed("afat")


def install_package_and_migrate(link: str) -> bool:
    from .app_settings import send_message, get_pings
    """
    Install a package from `link` in the current environment,
    then run Django migrations. Returns True on success.
    """
    import sys
    import subprocess
    from pathlib import Path

    send_message(f"Starting package update from link: {link}")

    # 1) Install in the same environment as this process
    try:
        pip_cmd = [sys.executable, "-m", "pip", "install", link]
        res = subprocess.run(pip_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            tail = (res.stderr or res.stdout or "").splitlines()[-20:]
            logger.error("pip install failed: %s", res.stderr)
            send_message(f"#{get_pings('Error')} pip install failed:\n```{os.linesep.join(tail)}```")
            return False
    except Exception as e:
        logger.exception("pip install raised exception")
        send_message(f"#{get_pings('Error')} pip install raised an exception:\n```{e}```")
        return False

    # 2) Run migrations, prefer in-process call_command
    try:
        from django.core.management import call_command
        call_command("migrate", interactive=False, verbosity=1)
        send_message("Migrations completed successfully, make sure to restart AA.")
        return True
    except Exception as e:
        logger.warning("call_command('migrate') failed; trying manage.py fallback: %s", e)

    # 3) Fallback: locate manage.py and run it with the same interpreter
    try:
        from django.conf import settings
        base = Path(getattr(settings, "BASE_DIR", ".")).resolve()
        manage_path = None

        # Common locations relative to BASE_DIR
        for candidate in (base, base.parent, base.parent.parent):
            cand = candidate / "manage.py"
            if cand.exists():
                manage_path = cand
                break

        # Last resort: shallow search up one level
        if manage_path is None:
            for p in base.parent.glob("**/manage.py"):
                manage_path = p
                break

        if manage_path is None:
            raise FileNotFoundError("manage.py not found under project path(s)")

        mig = subprocess.run(
            [sys.executable, str(manage_path), "migrate", "--noinput"],
            capture_output=True,
            text=True,
        )
        if mig.returncode != 0:
            tail = (mig.stderr or mig.stdout or "").splitlines()[-40:]
            logger.error("manage.py migrate failed: %s", mig.stderr)
            send_message(f"#{get_pings('Error')} manage.py migrate failed:\n```{os.linesep.join(tail)}```")
            return False

        send_message("Migrations completed successfully, make sure to restart AA.")
        return True

    except Exception as e2:
        logger.exception("Fallback manage.py migrate failed")
        send_message(f"#{get_pings('Error')} Could not run migrations:\n```{e2}```")
        return False
