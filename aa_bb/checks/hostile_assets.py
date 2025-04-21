from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist

from allianceauth.authentication.models import CharacterOwnership
from corptools.models import CharacterAudit, CharacterAsset, EveLocation
from ..app_settings import get_system_owner
from ..models import BigBrotherConfig
from django.utils.html import format_html
from typing import List, Optional, Dict
import logging

logger = logging.getLogger(__name__)

def get_asset_locations(user_id: int) -> List[str]:
    """
    Return a list of system names where any of the given user's characters
    has one or more assets in space (based on CharacterAsset.location_name.system).
    """
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return []

    system_names = set()

    # for each EVE character owned by this user
    for co in CharacterOwnership.objects.filter(user=user).select_related('character'):
        eve_char = co.character

        # get their audit record
        try:
            char_audit = CharacterAudit.objects.get(character=eve_char)
        except ObjectDoesNotExist:
            continue

        # all their assets
        for asset in CharacterAsset.objects.select_related('location_name__system').filter(character=char_audit):
            loc = asset.location_name
            if loc and loc.system:
                system_names.add(loc.system.name)

    return list(system_names)


def get_hostile_asset_locations(user_id: int) -> Dict[str, str]:
    """
    Returns a dict of system names → alliance names for systems where the user's
    characters have assets and the owning alliance is in the hostile list.
    """
    systems = get_asset_locations(user_id)
    if not systems:
        return {}

    # Parse hostile alliance IDs into a set of ints
    hostile_str = BigBrotherConfig.get_solo().hostile_alliances or ""
    hostile_ids = {int(s) for s in hostile_str.split(",") if s.strip().isdigit()}
    logger.debug(f"Hostile alliance string: {hostile_str}")
    logger.debug(f"Parsed hostile IDs: {hostile_ids}")

    hostile_map = {}

    for system in sorted(systems):
        owner_info = get_system_owner(system)  # {'owner_id','owner_name','owner_type'} or None
        if owner_info:
            oid = int(owner_info["owner_id"])
            oname = owner_info["owner_name"] or f"ID {oid}"
            if oid in hostile_ids:
                hostile_map[system] = oname
                logger.debug(f"Hostile asset system found: {system} owned by {oname} (ID {oid})")
        else:
            logger.debug(f"No ownership info for system: {system}")

    return hostile_map


def render_assets(user_id: int) -> Optional[str]:
    """
    Returns an HTML table listing each system where the user's characters have assets,
    the system's sovereign owner, and highlights in red any owner on the hostile list.
    """
    systems = get_asset_locations(user_id)
    if not systems:
        return None

    # Parse hostile IDs into a set of ints
    hostile_str = BigBrotherConfig.get_solo().hostile_alliances or ""
    hostile_ids = {int(s) for s in hostile_str.split(",") if s.strip().isdigit()}
    logger.debug(f"Hostile IDs for assets: {hostile_ids}")

    html = '<table class="table table-striped">'
    html += '<thead><tr><th>System</th><th>Owner</th></tr></thead><tbody>'

    for system in sorted(systems):
        owner_info = get_system_owner(system)
        if owner_info:
            oid = int(owner_info["owner_id"])
            oname = owner_info["owner_name"] or f"ID {oid}"
            hostile = oid in hostile_ids
        else:
            oname = "—"
            hostile = False

        row_tpl = '<tr><td>{}</td><td style="color: red;">{}</td></tr>' if hostile else '<tr><td>{}</td><td>{}</td></tr>'
        html += format_html(row_tpl, system, oname)

    html += "</tbody></table>"
    return html
