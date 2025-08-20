from django.apps import apps
from django.conf import settings
from esi.clients import EsiClientProvider
import re

import logging
logger = logging.getLogger(__name__)
esi = EsiClientProvider()

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

    try:
        logger.debug(f"Fetching corp info for corp_id {corp_id}")
        result = esi.client.Corporation.get_corporations_corporation_id(
            corporation_id=corp_id
        ).results()
        return {
            "name": result.get("name", f"Unknown ({corp_id})"),
            "alliance_id": result.get("alliance_id")
        }
    except Exception as e:
        logger.warning(f"Error fetching corp {corp_id}: {e}")
        return {
            "name": f"Unknown Corp ({corp_id})",
            "alliance_id": None
        }
    
def get_alliance_name(alliance_id):
    if not alliance_id:
        return "None"
    try:
        result = esi.client.Alliance.get_alliances_alliance_id(
            alliance_id=alliance_id
        ).results()
        return result.get("name", f"Unknown ({alliance_id})")
    except Exception as e:
        logger.warning(f"Error fetching alliance {alliance_id}: {e}")
        return f"Unknown ({alliance_id})"

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