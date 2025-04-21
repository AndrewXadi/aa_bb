from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist

from allianceauth.authentication.models import CharacterOwnership
from corptools.models import CharacterAudit, Clone, JumpClone

from django.utils.html import format_html
from typing import List, Optional, Dict

from ..app_settings import get_system_owner
from ..models import BigBrotherConfig
import logging

logger = logging.getLogger(__name__)

def get_clones(user_id: int) -> List[str]:
    """
    Return a list of system names (or raw location IDs) where this user has clones.
    """
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return []

    system_names = set()

    for co in CharacterOwnership.objects.filter(user=user).select_related('character'):
        eve_char = co.character

        try:
            char_audit = CharacterAudit.objects.get(character=eve_char)
        except ObjectDoesNotExist:
            continue

        # Home clone
        try:
            home = Clone.objects.select_related('location_name__system') \
                                .get(character=char_audit)
            if home.location_name and home.location_name.system:
                system_names.add(home.location_name.system.name)
            elif home.location_id is not None:
                system_names.add(f"Location ID {home.location_id}")
        except Clone.DoesNotExist:
            pass

        # Jump clones
        for jc in JumpClone.objects.select_related('location_name__system') \
                                   .filter(character=char_audit):
            if jc.location_name and jc.location_name.system:
                system_names.add(jc.location_name.system.name)
            elif jc.location_id is not None:
                system_names.add(f"Location ID {jc.location_id}")

    return list(system_names)


def get_hostile_clone_locations(user_id: int) -> Dict[str, str]:
    """
    Returns a dict of system name or location ID → owning alliance name,
    including 'Unresolvable' where owner info is unavailable.
    Only includes locations owned by hostile alliances or that are unresolvable.
    """
    systems = get_clones(user_id)
    if not systems:
        return {}

    hostile_str = BigBrotherConfig.get_solo().hostile_alliances or ""
    hostile_ids = {int(s) for s in hostile_str.split(",") if s.strip().isdigit()}
    logger.debug(f"Hostile alliance string: {hostile_str}")
    logger.debug(f"Parsed hostile IDs: {hostile_ids}")

    hostile_map: Dict[str, str] = {}

    for system in sorted(systems):
        owner_info = get_system_owner(system)  # May return None
        if owner_info:
            oid = int(owner_info["owner_id"])
            oname = owner_info["owner_name"] or f"ID {oid}"
            if oid in hostile_ids:
                hostile_map[system] = oname
                logger.debug(f"Hostile clone found: {system} owned by {oname} (ID {oid})")
        else:
            # Always include unresolvables
            hostile_map[system] = "Unresolvable"
            logger.debug(f"No ownership info for clone in: {system}, marked Unresolvable")

    return hostile_map



def render_clones(user_id: int) -> Optional[str]:
    """
    Returns an HTML table of clones, coloring hostile ones red,
    and labeling & highlighting Unresolvable owners appropriately.
    """
    systems = get_clones(user_id)
    if not systems:
        return None

    hostile_str = BigBrotherConfig.get_solo().hostile_alliances or ""
    hostile_ids = {int(s) for s in hostile_str.split(",") if s.strip().isdigit()}
    logger.debug(f"Hostile IDs: {hostile_ids}")

    html = ['<table class="table table-striped">',
            '<thead><tr><th>System</th><th>Owner</th></tr></thead><tbody>']

    for system in sorted(systems):
        owner_info = get_system_owner(system)
        if owner_info:
            oid = int(owner_info["owner_id"])
            oname = owner_info["owner_name"] or f"ID {oid}"
            hostile = oid in hostile_ids
            unresolvable = False
        else:
            oname = "Unresolvable"
            hostile = False
            unresolvable = True

        if hostile:
            row_tpl = '<tr><td>{}</td><td class="text-danger">{}</td></tr>'
        elif unresolvable:
            row_tpl = '<tr><td>{}</td><td class="text-warning"><em>{}</em></td></tr>'
        else:
            row_tpl = '<tr><td>{}</td><td>{}</td></tr>'

        html.append(format_html(row_tpl, system, oname))

    html.append('</tbody></table>')
    return "".join(html)