from django.contrib.auth.decorators import login_required, permission_required
from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from allianceauth.authentication.models import UserProfile, CharacterOwnership
from aa_bb.checks.awox import get_awox_kills
from aa_bb.checks.corp_changes import get_frequent_corp_changes

import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


CARD_DEFINITIONS = [
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Transactions', "key": "sus_tra"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Contracts', "key": "sus_con"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Contacts', "key": "sus_contr"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Mails', "key": "sus_mail"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>IMP Blacklist', "key": "imp_bl"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Assets in hostile space', "key": "sus_asset"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Clones in hostile space', "key": "sus_clones"},
    {"title": 'Frequent Corp Changes', "key": "freq_corp"},
    {"title": 'AWOX Kills', "key": "awox"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Cyno?', "key": "cyno"},
]


def get_user_id(character_name):
    try:
        ownership = CharacterOwnership.objects.select_related('user').get(character__character_name=character_name)
        return ownership.user.id
    except CharacterOwnership.DoesNotExist:
        return None


def get_card_data(user_id, key):
    if key == "awox":
        content = get_awox_kills(user_id)
        status = content == "No awox kills found."
    elif key == "freq_corp":
        content = get_frequent_corp_changes(user_id)
        if "red" in content or "orange" in content:
            status = False
        else:
            status = True
    else:
        content = "WiP"
        status = True
    return content, status


@login_required
@permission_required("aa_bb.basic_access")
def index(request: WSGIRequest) -> HttpResponse:
    dropdown_options = []

    if request.user.has_perm("aa_bb.full_access"):
        dropdown_options = list(
            UserProfile.objects.exclude(main_character=None)
            .values_list("main_character__character_name", flat=True)
            .order_by("main_character__character_name")
        )
    elif request.user.has_perm("aa_bb.recruiter_access"):
        dropdown_options = list(
            UserProfile.objects.filter(state=1)
            .exclude(main_character=None)
            .values_list("main_character__character_name", flat=True)
            .order_by("main_character__character_name")
        )

    context = {"dropdown_options": dropdown_options}
    return render(request, "aa_bb/index.html", context)


@login_required
@permission_required("aa_bb.basic_access")
def load_cards(request: WSGIRequest) -> JsonResponse:
    selected_option = request.GET.get("option")
    user_id = get_user_id(selected_option)

    content_vars = {}
    status_vars = {}

    cards = []

    for card in CARD_DEFINITIONS:
        key = card["key"]
        title = card["title"]
        content, status = get_card_data(user_id, key)
        content_vars[f"content_{key}"] = content
        status_vars[f"status_{key}"] = status

        cards.append({
            "title": title,
            "content": content,
            "status": status,
        })

    return JsonResponse({"cards": cards})


@login_required
@permission_required("aa_bb.basic_access")
def load_card(request: WSGIRequest) -> JsonResponse:
    selected_option = request.GET.get("option")
    index = int(request.GET.get("index", 0))

    if not selected_option or index < 0 or index >= len(CARD_DEFINITIONS):
        return JsonResponse({"error": "Invalid request"}, status=400)

    user_id = get_user_id(selected_option)
    card_def = CARD_DEFINITIONS[index]
    content, status = get_card_data(user_id, card_def["key"])

    return JsonResponse({
        "title": card_def["title"],
        "content": content,
        "status": status,
        "index": index,
    })
