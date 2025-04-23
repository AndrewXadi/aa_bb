import logging
from django.contrib.auth.decorators import login_required, permission_required
from django.core.handlers.wsgi import WSGIRequest
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST

from allianceauth.authentication.models import UserProfile, CharacterOwnership
from aa_bb.checks.awox import render_awox_kills_html
from aa_bb.checks.corp_changes import get_frequent_corp_changes
from aa_bb.checks.cyno import cyno
from aa_bb.checks.hostile_assets import render_assets
from aa_bb.checks.hostile_clones import render_clones
from aa_bb.checks.imp_blacklist import generate_blacklist_links
from aa_bb.checks.lawn_blacklist import get_user_character_names_lawn
from aa_bb.checks.notifications import game_time, skill_injected
from aa_bb.checks.sus_contacts import sus_conta
from aa_bb.checks.sus_contracts import sus_contra
from aa_bb.checks.sus_mails import sus_mail
from aa_bb.checks.sus_trans import sus_tra
from aa_bb.checks.corp_blacklist import get_corp_blacklist_html, add_user_characters_to_blacklist
from .app_settings import get_system_owner, aablacklist_active
from .models import BigBrotherConfig
from django_celery_beat.models import PeriodicTask

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


CARD_DEFINITIONS = [
    {"title": 'IMP Blacklist',"key": "imp_bl"},
    {"title": '<span style="color: Orange;"><b>WiP </b></span>LAWN Blacklist',"key": "lawn_bl"},
    {"title": 'Corp Blacklist',"key": "corp_bl"},
    {"title": 'Player Corp History',"key": "freq_corp"},
    {"title": 'AWOX Kills',"key": "awox"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Contacts',"key": "sus_contr"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Contracts',"key": "sus_con"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Mails',"key": "sus_mail"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Transactions',"key": "sus_tra"},
    {"title": 'Clones in hostile space',"key": "sus_clones"},
    {"title": 'Assets in hostile space',"key": "sus_asset"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Cyno?',"key": "cyno"},
]


def get_user_id(character_name):
    try:
        ownership = CharacterOwnership.objects.select_related('user') \
            .get(character__character_name=character_name)
        return ownership.user.id
    except CharacterOwnership.DoesNotExist:
        return None


# views.py (or wherever get_card_data lives)


def get_card_data(request, target_user_id: int, key: str):
    """
    request           = the incoming Django request (so we know who is logged in)
    target_user_id    = the user whose cards we’re rendering
    key               = which card (awox, freq_corp, ..., corp_bl)
    """
    if key == "awox":
        content = render_awox_kills_html(target_user_id)
        status  = content is None

    elif key == "freq_corp":
        content = get_frequent_corp_changes(target_user_id)
        status  = "red" not in content

    elif key == "sus_clones":
        content = render_clones(target_user_id)
        status  = not (content and any(w in content for w in ("danger", "warning")))

    elif key == "sus_asset":
        content = render_assets(target_user_id)
        status  = not (content and "red" in content)

    elif key == "imp_bl":
        links   = generate_blacklist_links(target_user_id)
        content = "<br>".join(links)
        status  = False

    elif key == "lawn_bl":
        names   = get_user_character_names_lawn(target_user_id)
        content = (
            "Go <a href='https://auth.lawnalliance.space/blacklist/blacklist/'>here</a> "
            f"and check those names:<br>{names}"
        )
        status  = False

    elif key == "corp_bl":
        issuer_id = request.user.id
        content   = get_corp_blacklist_html(request, issuer_id, target_user_id)
        status = not (content and "🚩" in content)

    else:
        content = "WiP"
        status  = True

    return content, status



@login_required
@permission_required("aa_bb.basic_access")
def index(request: WSGIRequest):
    dropdown_options = []
    task_name = 'BB run regular updates'
    task = PeriodicTask.objects.filter(name=task_name).first()
    if BigBrotherConfig.get_solo().is_active is False or task and task.enabled == False:
        context = "Big Brother is currently in an inactive state, please make sure it is up to date, you have filled the settings and turned on the task"
        return render(request, "aa_bb/disabled.html")
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
        content, status = get_card_data(request, user_id, card["key"])
        cards.append({
            "title":   card["title"],
            "content": content,
            "status":  status,
        })
    return JsonResponse({"cards": cards})


# views.py

@login_required
@permission_required("aa_bb.basic_access")
def load_card(request):
    option = request.GET.get("option")
    idx    = request.GET.get("index")

    # 1. Validate parameters
    if option is None or idx is None:
        return HttpResponseBadRequest("Missing parameters")

    # 2. Convert index to int and fetch definition
    try:
        idx       = int(idx)
        card_def  = CARD_DEFINITIONS[idx]
    except (ValueError, IndexError):
        return HttpResponseBadRequest("Invalid card index")

    key   = card_def["key"]
    title = card_def["title"]

    # 3. Lookup the target user ID from the selected option
    target_user_id = get_user_id(option)
    if target_user_id is None:
        return JsonResponse({"error": "Unknown account"}, status=404)

    # 4. Generate content & status
    content, status = get_card_data(request, target_user_id, key)

    # 5. Return JSON
    return JsonResponse({
        "title":   title,
        "content": content,
        "status":  status,
    })


@require_POST
def add_blacklist_view(request):
    issuer_id = int(request.POST["issuer_user_id"])
    target_id = int(request.POST["target_user_id"])
    reason    = request.POST.get("reason", "")
    added = add_user_characters_to_blacklist(
        issuer_user_id=issuer_id,
        target_user_id=target_id,
        reason=reason
    )
    # e.g. redirect back with a success message
    return redirect(request.META.get("HTTP_REFERER", "/"), 
        message=f"Blacklisted: {', '.join(added)}")