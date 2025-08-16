from allianceauth.authentication.models import CharacterOwnership
from corptools.models import CharacterRoles, CharacterAudit
from allianceauth.eveonline.models import EveCharacter
from esi.models import Token
from django.utils.html import format_html
from django.utils.safestring import mark_safe

def get_user_roles(user_id):
    characters = CharacterOwnership.objects.filter(user__id=user_id).select_related("character")

    roles_dict = {}

    for ownership in characters:
        eve_char = ownership.character  # EveCharacter instance
        char_name = eve_char.character_name

        try:
            audit = CharacterAudit.objects.get(character=eve_char)
            char_roles = CharacterRoles.objects.get(character=audit)

            roles_dict[char_name] = {
                "director": char_roles.director,
                "accountant": char_roles.accountant,
                "station_manager": char_roles.station_manager,
                "personnel_manager": char_roles.personnel_manager,
                # you could add "titles": list(char_roles.titles.values_list("name", flat=True)) if needed
            }
        except (CharacterAudit.DoesNotExist, CharacterRoles.DoesNotExist):
            # no audit/roles available for this character
            roles_dict[char_name] = {
                "director": False,
                "accountant": False,
                "station_manager": False,
                "personnel_manager": False,
            }

    return roles_dict

def get_user_tokens(user_id):
    from esi.models import Token, Scope

    CHARACTER_SCOPES = [
        "publicData",
        "esi-calendar.read_calendar_events.v1",
        "esi-location.read_location.v1",
        "esi-location.read_ship_type.v1",
        "esi-mail.read_mail.v1",
        "esi-skills.read_skills.v1",
        "esi-skills.read_skillqueue.v1",
        "esi-wallet.read_character_wallet.v1",
        "esi-search.search_structures.v1",
        "esi-clones.read_clones.v1",
        "esi-characters.read_contacts.v1",
        "esi-universe.read_structures.v1",
        "esi-killmails.read_killmails.v1",
        "esi-assets.read_assets.v1",
        "esi-fleets.read_fleet.v1",
        "esi-fleets.write_fleet.v1",
        "esi-ui.open_window.v1",
        "esi-ui.write_waypoint.v1",
        "esi-fittings.read_fittings.v1",
        "esi-characters.read_loyalty.v1",
        "esi-characters.read_standings.v1",
        "esi-industry.read_character_jobs.v1",
        "esi-markets.read_character_orders.v1",
        "esi-characters.read_corporation_roles.v1",
        "esi-location.read_online.v1",
        "esi-contracts.read_character_contracts.v1",
        "esi-clones.read_implants.v1",
        "esi-characters.read_fatigue.v1",
        "esi-characters.read_notifications.v1",
        "esi-industry.read_character_mining.v1",
        "esi-characters.read_titles.v1",
    ]

    CORPORATION_SCOPES = [
        "esi-corporations.read_corporation_membership.v1",
        "esi-corporations.read_structures.v1",
        "esi-killmails.read_corporation_killmails.v1",
        "esi-corporations.track_members.v1",
        "esi-wallet.read_corporation_wallets.v1",
        "esi-corporations.read_divisions.v1",
        "esi-assets.read_corporation_assets.v1",
        "esi-corporations.read_titles.v1",
        "esi-contracts.read_corporation_contracts.v1",
        "esi-corporations.read_starbases.v1",
        "esi-industry.read_corporation_jobs.v1",
        "esi-markets.read_corporation_orders.v1",
        "esi-industry.read_corporation_mining.v1",
        "esi-planets.read_customs_offices.v1",
        "esi-search.search_structures.v1",
        "esi-universe.read_structures.v1",
        "esi-characters.read_corporation_roles.v1",
    ]

    characters = CharacterOwnership.objects.filter(user__id=user_id).select_related("character")
    tokens_dict = {}

    for ownership in characters:
        eve_char = ownership.character
        char_name = eve_char.character_name

        # Get all tokens for this character
        all_tokens = Token.objects.filter(character_id=eve_char.character_id, user_id=user_id)
        
        char_scopes_owned = set()
        corp_scopes_owned = set()
        
        for token in all_tokens:
            token_scopes = set(token.scopes.values_list("name", flat=True))
            # intersect with the sets of character/corp scopes to avoid unrelated scopes
            char_scopes_owned.update(token_scopes & set(CHARACTER_SCOPES))
            corp_scopes_owned.update(token_scopes & set(CORPORATION_SCOPES))

        missing_character_scopes = set(CHARACTER_SCOPES) - char_scopes_owned
        missing_corporation_scopes = set(CORPORATION_SCOPES) - corp_scopes_owned

        has_char_token = len(missing_character_scopes) == 0
        has_corp_token = len(missing_corporation_scopes) == 0

        tokens_dict[char_name] = {
            "character_token": has_char_token,
            "corporation_token": has_corp_token,
            "missing_character_scopes": ", ".join(sorted(missing_character_scopes)),
            "missing_corporation_scopes": ", ".join(sorted(missing_corporation_scopes)),
        }

    return tokens_dict

def get_user_roles_and_tokens(user_id):
    roles = get_user_roles(user_id)
    tokens = get_user_tokens(user_id)

    combined = {}

    # union of all characters in roles or tokens
    for char_name in set(roles.keys()) | set(tokens.keys()):
        combined[char_name] = {}
        if char_name in roles:
            combined[char_name].update(roles[char_name])
        if char_name in tokens:
            combined[char_name].update(tokens[char_name])

    return combined


def render_user_roles_tokens_html(user_id: int) -> str:
    """
    Returns an HTML snippet showing, for each of the user's characters:
      - director / accountant / station_manager / personnel_manager (True/False)
      - whether they have a character_token
      - whether they have a corporation_token
    """
    data = get_user_roles_and_tokens(user_id)
    html = ""

    for char_name, info in data.items():
        # header
        html += format_html("<h3>{}</h3>", char_name)

        # table start
        html += """
        <table class="table table-striped">
          <thead>
            <tr>
              <th>Attribute</th><th>Value</th>
            </tr>
          </thead>
          <tbody>
        """

        # roles
        has_roles = False
        for key, label in (
            ("director", "Director"),
            ("accountant", "Accountant"),
            ("station_manager", "Station Manager"),
            ("personnel_manager", "Personnel Manager"),
        ):
            val = info.get(key, False)
            # highlight True roles in red if corporation_token is False
            if val:
                val_txt = mark_safe('<span style="color:orange;">True</span>')
                has_roles = True
            else:
                val_txt = "False"
            html += format_html(
                "<tr><td>{}</td><td>{}</td></tr>", label, val_txt
            )

        # tokens
        for key, label in (
            ("character_token", "Character Token"),
            ("corporation_token", "Corporation Token"),
        ):
            val = info.get(key, False)
            # if character_token is False → make it red
            if key == "character_token" and not val:
                val_txt = mark_safe('<span style="color:red;">False</span>')
            elif key == "corporation_token" and not val:
                if has_roles:
                    val_txt = mark_safe('<span style="color:red;">False</span>')
                else:
                    val_txt = mark_safe('False')
            else:
                val_txt = mark_safe('<span style="color:green;">True</span>')
            html += format_html(
                "<tr><td>{}</td><td>{}</td></tr>", label, val_txt
            )

        # scopes
        for key, label in (
            ("missing_character_scopes", "Missing Character Scopes"),
            ("missing_corporation_scopes", "Missing Corporation Scopes"),
        ):
            val = info.get(key, "")
            if val:  # only add row if non-empty
                html += format_html(
                    "<tr><td>{}</td><td colspan='2'><span style='color:red;'>{}</span></td></tr>",
                    label,
                    mark_safe(val)  # comma-separated string of missing scopes
                )

        # table end
        html += "</tbody></table>"

    return format_html(html)