from django.contrib.auth.decorators import login_required, permission_required
from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from allianceauth.authentication.models import UserProfile, CharacterOwnership
from aa_bb.checks.awox import render_awox_kills_html
from aa_bb.checks.corp_changes import get_frequent_corp_changes
from aa_bb.checks.cyno import cyno
from aa_bb.checks.hostile_assets import render_assets
from aa_bb.checks.hostile_clones import render_clones
from aa_bb.checks.imp_blacklist import get_user_character_names
from aa_bb.checks.lawn_blacklist import lawn_bl
from aa_bb.checks.notifications import game_time
from aa_bb.checks.notifications import skill_injected
from aa_bb.checks.sus_contacts import sus_conta
from aa_bb.checks.sus_contracts import sus_contra
from aa_bb.checks.sus_mails import sus_mail
from aa_bb.checks.sus_trans import sus_tra
from .app_settings import get_system_owner
from .models import BigBrotherConfig

import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


CARD_DEFINITIONS = [
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Transactions', "key": "sus_tra"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Contracts', "key": "sus_con"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Contacts', "key": "sus_contr"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Mails', "key": "sus_mail"},
    {"title": '<span style="color: Orange;"><b>WiP </b></span>IMP Blacklist', "key": "imp_bl"},
    {"title": 'Assets in hostile space', "key": "sus_asset"},
    {"title": 'Clones in hostile space', "key": "sus_clones"},
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
        content = render_awox_kills_html(user_id)
        status = content == None
    elif key == "freq_corp":
        content = get_frequent_corp_changes(user_id)
        if "red" in content:
            status = False
        else:
            status = True
    elif key == "sus_clones":
        content = render_clones(user_id)
        if "danger" in content or "warning" in content:
            status = False
        else:
            status = True
    elif key == "sus_asset":
        content = render_assets(user_id)
        if "red" in content:
            status = False
        else:
            status = True
    elif key == "imp_bl":
        content = f"<a href='https://gice.goonfleet.com/Blacklist?q={get_user_character_names(user_id)}'>Click here</a>"
        #if "red" in content:
        status = False
        #else:
            #status = True
    else:
        content = "WiP"
        status = True
    return content, status


@login_required
@permission_required("aa_bb.basic_access")
def index(request: WSGIRequest) -> HttpResponse:
    dropdown_options = []
    if BigBrotherConfig.get_solo() == False:
        context = "Big Brother is currently in an inactive state, please make sure it is up to date, you have filled the settings and turned on the task"
    elif request.user.has_perm("aa_bb.full_access"):
        dropdown_options = list(
            UserProfile.objects.exclude(main_character=None)
            .values_list("main_character__character_name", flat=True)
            .order_by("main_character__character_name")
        )
        context = {"dropdown_options": dropdown_options}
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
