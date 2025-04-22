import logging
from django.contrib.auth.decorators import login_required, permission_required
from django.core.handlers.wsgi import WSGIRequest
from django.http import JsonResponse
from django.shortcuts import render

from allianceauth.authentication.models import UserProfile, CharacterOwnership
from aa_bb.checks.awox import render_awox_kills_html
from aa_bb.checks.corp_changes import get_frequent_corp_changes
from aa_bb.checks.cyno import cyno
from aa_bb.checks.hostile_assets import render_assets
from aa_bb.checks.hostile_clones import render_clones
from aa_bb.checks.imp_blacklist import get_user_character_names
from aa_bb.checks.lawn_blacklist import get_user_character_names_lawn
from aa_bb.checks.notifications import game_time, skill_injected
from aa_bb.checks.sus_contacts import sus_conta
from aa_bb.checks.sus_contracts import sus_contra
from aa_bb.checks.sus_mails import sus_mail
from aa_bb.checks.sus_trans import sus_tra
from .app_settings import get_system_owner
from .models import BigBrotherConfig

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


CARD_DEFINITIONS = [
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Transactions',"key": "sus_tra"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Contracts',"key": "sus_con"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Contacts',"key": "sus_contr"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Mails',"key": "sus_mail"},
    {"title": 'IMP Blacklist',"key": "imp_bl"},
    {"title": '<span style="color: Orange;"><b>WiP </b></span>LAWN Blacklist',"key": "lawn_bl"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Corp Blacklist',"key": "corp_bl"},
    {"title": 'Assets in hostile space',"key": "sus_asset"},
    {"title": 'Clones in hostile space',"key": "sus_clones"},
    {"title": 'Frequent Corp Changes',"key": "freq_corp"},
    {"title": 'AWOX Kills',"key": "awox"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Cyno?',"key": "cyno"},
]


def get_user_id(character_name):
    try:
        ownership = CharacterOwnership.objects.select_related('user') \
            .get(character__character_name=character_name)
        return ownership.user.id
    except CharacterOwnership.DoesNotExist:
        return None


def get_card_data(user_id, key):
    # your existing logic here; may raise exceptions
    if key == "awox":
        content = render_awox_kills_html(user_id)
        status = content is None
    elif key == "freq_corp":
        content = get_frequent_corp_changes(user_id)
        status = "red" not in content
    elif key == "sus_clones":
        content = render_clones(user_id)
        status = not any(w in content for w in ("danger","warning"))
    elif key == "sus_asset":
        content = render_assets(user_id)
        status = "red" not in content
    elif key == "imp_bl":
        content = f"<a href='https://gice.goonfleet.com/Blacklist?q={get_user_character_names(user_id)}'>Click here</a>"
        status = False
    elif key == "lawn_bl":
        content = (
            "Go <a href=https://auth.lawnalliance.space/blacklist/blacklist/>here</a> "
            f"and check those names:<br>{get_user_character_names_lawn(user_id)}"
        )
        status = False
    else:
        content = "WiP"
        status = True

    return content, status


@login_required
@permission_required("aa_bb.basic_access")
def index(request: WSGIRequest):
    dropdown_options = []
    if BigBrotherConfig.get_solo() is False:
        context = "Big Brother is currently in an inactive state, please make sure it is up to date, you have filled the settings and turned on the task"
    elif request.user.has_perm("aa_bb.full_access"):
        dropdown_options = (
            UserProfile.objects.exclude(main_character=None)
            .values_list("main_character__character_name", flat=True)
            .order_by("main_character__character_name")
        )
        context = {"dropdown_options": dropdown_options}
    elif request.user.has_perm("aa_bb.recruiter_access"):
        dropdown_options = (
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
    # unchanged bulk loader
    selected_option = request.GET.get("option")
    user_id = get_user_id(selected_option)
    cards = []
    for card in CARD_DEFINITIONS:
        content, status = get_card_data(user_id, card["key"])
        cards.append({
            "title":   card["title"],
            "content": content,
            "status":  status,
        })
    return JsonResponse({"cards": cards})


@login_required
@permission_required("aa_bb.basic_access")
def load_card(request: WSGIRequest) -> JsonResponse:
    selected_option = request.GET.get("option")
    try:
        index = int(request.GET.get("index", 0))
    except ValueError:
        return JsonResponse({"error": "Invalid index"}, status=400)

    if not selected_option or index < 0 or index >= len(CARD_DEFINITIONS):
        return JsonResponse({"error": "Invalid request"}, status=400)

    user_id = get_user_id(selected_option)
    card_def = CARD_DEFINITIONS[index]

    try:
        content, status = get_card_data(user_id, card_def["key"])
        return JsonResponse({
            "title":   card_def["title"],
            "content": content,
            "status":  status,
            "index":   index,
        })
    except Exception as exc:
        # log full traceback for debugging
        logger.exception(f"Error loading card {index + 1} for user {user_id}")
        # return only the exception message
        return JsonResponse({"error": str(exc)}, status=500)
