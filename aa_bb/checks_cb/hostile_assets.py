from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCorporationInfo
from corptools.models import CorporationAudit, CorpAsset, EveLocation
from ..app_settings import get_system_owner
from ..models import BigBrotherConfig
from django.utils.html import format_html
from typing import List, Optional, Dict
import logging

logger = logging.getLogger(__name__)

def get_asset_locations(corp_id: int) -> Dict[int, Optional[str]]:
    """
    Return a dict mapping system IDs to their names (or None if unnamed)
    where the given corporation has one or more assets in space.
    """
    try:
        corp_info = EveCorporationInfo.objects.get(corporation_id=corp_id)
        corp_audit = CorporationAudit.objects.get(corporation=corp_info)
    except CorporationAudit.DoesNotExist:
        return {}

    system_map: Dict[int, Optional[str]] = {}

    def add_system(system_obj):
        if system_obj:
            key = getattr(system_obj, 'pk', None)
            system_map[key] = system_obj.name

    # All corp assets (exclude ones where location_flag is "solar_system")
    assets = CorpAsset.objects.select_related('location_name__system') \
                              .filter(corporation=corp_audit) \
                              .exclude(location_flag="solar_system")

    for asset in assets:
        loc = asset.location_name
        add_system(getattr(loc, 'system', None))

    sorted_items = sorted(
        system_map.items(),
        key=lambda kv: (kv[1] or "").lower()
    )
    return dict(sorted_items)

def get_corp_hostile_asset_locations(user_id: int) -> Dict[str, str]:
    """
    Returns a dict of system display name → owning alliance name
    for systems where the user's characters have assets in space,
    including only those owned by hostile alliances or that are
    unresolvable.
    """
    # get_asset_locations now returns Dict[int, Optional[str]]
    systems = get_asset_locations(user_id)
    if not systems:
        return {}

    # parse hostile alliance IDs
    hostile_str = BigBrotherConfig.get_solo().hostile_alliances or ""
    hostile_ids = {int(s) for s in hostile_str.split(",") if s.strip().isdigit()}
    logger.debug(f"Hostile alliance IDs: {hostile_ids}")

    hostile_map: Dict[str, str] = {}

    # iterate system_id, system_name pairs
    for system_id, system_name in systems.items():
        display_name = system_name or f"Unknown ({system_id})"

        # build the dict that get_system_owner expects
        owner_info = get_system_owner({
            "id":   system_id,
            "name": display_name
        })

        if not owner_info:
            # treat fully missing owner info as unresolvable
            hostile_map[display_name] = "Unresolvable"
            #logger.debug(f"No ownership info for assets in {display_name}; marked Unresolvable")
            continue

        # attempt to parse owner_id
        try:
            oid = int(owner_info["owner_id"])
        except (ValueError, TypeError):
            oid = None

        oname = owner_info.get("owner_name") or (f"ID {oid}" if oid is not None else "Unresolvable")

        # include only hostile or unresolvable owners
        if oid in hostile_ids or "Unresolvable" in oname:
            hostile_map[display_name] = oname
            logger.info(f"Hostile asset system: {display_name} owned by {oname} ({oid})")

    return hostile_map


def render_assets(corp_id: int) -> Optional[str]:
    """
    Returns an HTML table listing each system where the user's characters have assets,
    the system's sovereign owner, and highlights in red any owner on the hostile list.
    """
    systems = get_asset_locations(corp_id)
    logger.info(f"corp id {corp_id}, systems {len(systems)}")
    if not systems:
        return None

    # Parse hostile IDs into a set of ints
    hostile_str = BigBrotherConfig.get_solo().hostile_alliances or ""
    hostile_ids = {int(s) for s in hostile_str.split(",") if s.strip().isdigit()}
    #logger.debug(f"Hostile IDs for assets: {hostile_ids}")

    html = '<table class="table table-striped">'
    html += '<thead><tr><th>System</th><th>Owner</th></tr></thead><tbody>'

    for system_id, system_name in systems.items():
        # build the dict your get_system_owner() wants:
        owner_info = get_system_owner({
            "id":   system_id,
            "name": system_name or f"Unknown ({system_id})"
        })
        if owner_info:
            try:
                # owner_id might be '' or None
                oid = int(owner_info["owner_id"]) if owner_info["owner_id"] else None
            except (ValueError, TypeError):
                oid = None

            if oid is not None:
                oname = owner_info["owner_name"] or f"ID {oid}"
                hostile = oid in hostile_ids or "Unresolvable" in oname
            else:
                oname = "—"
                hostile = False
        else:
            oname = "—"
            hostile = False

        # ← THIS must be indented inside the loop!
        row_tpl = (
            '<tr><td>{}</td><td style="color: red;">{}</td></tr>'
            if hostile
            else '<tr><td>{}</td><td>{}</td></tr>'
        )
        html += format_html(row_tpl, system_name, oname)

    html += "</tbody></table>"
    return html
