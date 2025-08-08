from allianceauth.authentication.models import CharacterOwnership
from ..app_settings import aablacklist_active, send_message, get_pings
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse 
from django.middleware.csrf import get_token 

def check_corp_bl(user_id):
    if not aablacklist_active():
        return None
    status_map = {}
    for co in CharacterOwnership.objects.filter(user__id=user_id):
        cid = co.character.character_id
        status_map[co.character.character_name] = check_char_corp_bl(cid)
    return status_map

def check_char_corp_bl(cid):
    if not aablacklist_active():
        return False
    from blacklist.models import EveNote
    blacklisted_ids = EveNote.objects.filter(
        blacklisted=True,
        eve_catagory='character'
    ).values_list('eve_id', flat=True)
    return cid in blacklisted_ids

def get_corp_blacklist_html(
    request,                   # ← new first parameter
    issuer_user_id: int,
    target_user_id: int
) -> str:
    if not aablacklist_active():
        return (
            "Please "
            "<a href='https://github.com/Solar-Helix-Independent-Transport/"
            "allianceauth-blacklist/tree/main'>install blacklist</a> first"
        )

    # Reverse the correct namespaced POST URL:
    action_url = reverse("BigBrother:add_blacklist")  
    # Generate a real CSRF token:
    token = get_token(request)

    status_map = check_corp_bl(target_user_id)
    items = list(status_map.items())

    html = [
        f"<form method='post' action='{action_url}'>",
        f"  <input type='hidden' name='csrfmiddlewaretoken' value='{token}'/>",
        f"  <input type='hidden' name='issuer_user_id' value='{issuer_user_id}'/>",
        f"  <input type='hidden' name='target_user_id' value='{target_user_id}'/>",
        "  <ul>",
    ]

    # render 4 chars per <li>
    for i in range(0, len(items), 4):
        chunk = items[i : i + 4]
        line = ", ".join(
            ( "🚩 " + n if bl else "✅ " + n )
            for n, bl in chunk
        )
        html.append(f"    <li>{line}</li>")

    if request.user.has_perm("aa_bb.can_blacklist_characters"):
        action_url = reverse("BigBrother:add_blacklist")
        token      = get_token(request)
        html += [
            f"<form method='post' action='{action_url}'>",
            f"  <input type='hidden' name='csrfmiddlewaretoken' value='{token}'/>",
            f"  <input type='hidden' name='issuer_user_id' value='{issuer_user_id}'/>",
            f"  <input type='hidden' name='target_user_id' value='{target_user_id}'/>",
            "  <label for='reason'>Reason (max 4000 chars):</label><br/>",
            "  <textarea id='reason' name='reason' maxlength='4000' rows='4' cols='50'></textarea><br/>",
            "  <button type='submit'>Add to Blacklist</button>",
            "</form>",
        ]

    return "\n".join(html)


from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist

def add_user_characters_to_blacklist(
    issuer_user_id: int,
    target_user_id: int,
    reason: str,
    max_reason_length: int = 4000
) -> list[str]:
    if not aablacklist_active():
        return None

    from blacklist.models import EveNote
    """
    Blacklist every character owned by `target_user_id`, tagging each entry
    with the issuing user's main character (or username) and the given reason.
    Returns the list of newly blacklisted character names.
    """

    # 1. Load issuer and determine their “main” character
    issuer = User.objects.get(pk=issuer_user_id)
    try:
        main_char = issuer.profile.main_character
    except (ObjectDoesNotExist, AttributeError):
        main_char = None
    if main_char is None:
        co_first = CharacterOwnership.objects.filter(user=issuer).first()
        main_char = co_first.character if co_first else None
    added_by = main_char.character_name if main_char else issuer.get_username()

    # 2. Truncate and clean the reason
    reason_clean = (reason or "").strip()
    if len(reason_clean) > max_reason_length:
        reason_clean = reason_clean[:max_reason_length]

    # 3. Fetch target user’s main character
    target_user = User.objects.get(pk=target_user_id)
    try:
        target_main_char = target_user.profile.main_character
    except (ObjectDoesNotExist, AttributeError):
        target_main_char = None
    if target_main_char is None:
        co_first_t = CharacterOwnership.objects.filter(user=target_user).first()
        target_main_char = co_first_t.character if co_first_t else None
    target_main_name = (
        target_main_char.character_name
        if target_main_char
        else target_user.get_username()
    )

    # 4. Build the final reason string
    timestamp = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    final_reason = (
        f"Time Stamp: {timestamp}\n"
        f"Main Character: {target_main_name}\n"
        f"Added by: {added_by}\n"
        f"Reason: {reason_clean}"
    )

    # 5. Iterate and create EveNote entries
    newly_blacklisted = []
    for co in CharacterOwnership.objects.filter(user__id=target_user_id):
        char = co.character
        exists = EveNote.objects.filter(
            eve_id=char.character_id,
            eve_catagory='character',
            blacklisted=True
        ).exists()
        if exists:
            continue

        EveNote.objects.create(
            eve_id=char.character_id,
            eve_name=char.character_name,
            eve_catagory='character',
            blacklisted=True,
            reason=final_reason,
            added_by=added_by,
            corporation_id=None,
            corporation_name=None,
            alliance_id=None,
            alliance_name=None,
        )
        newly_blacklisted.append(char.character_name)
        send_message(f"{get_pings("New Blacklist Entry")}{target_main_name}'s character {char.character_name} added to blacklist by {added_by}")

    return newly_blacklisted
