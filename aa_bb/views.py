import html
import logging

from django.contrib.auth.decorators import login_required, permission_required
from django.core.handlers.wsgi import WSGIRequest
from django.http import (
    JsonResponse,
    HttpResponseBadRequest,
    StreamingHttpResponse,
)
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST

from allianceauth.authentication.models import UserProfile, CharacterOwnership
from django_celery_beat.models import PeriodicTask

from aa_bb.checks.awox import render_awox_kills_html
from aa_bb.checks.corp_changes import get_frequent_corp_changes
from aa_bb.checks.cyno import cyno
from aa_bb.checks.hostile_assets import render_assets
from aa_bb.checks.hostile_clones import render_clones
from aa_bb.checks.imp_blacklist import generate_blacklist_links
from aa_bb.checks.lawn_blacklist import get_user_character_names_lawn
from aa_bb.checks.notifications import game_time, skill_injected
from aa_bb.checks.sus_contacts import render_contacts
from aa_bb.checks.sus_mails import sus_mail
from aa_bb.checks.sus_trans import sus_tra
from aa_bb.checks.corp_blacklist import (
    get_corp_blacklist_html,
    add_user_characters_to_blacklist,
)
from aa_bb.checks.sus_contracts import (
    get_user_contracts,
    is_row_hostile,
    get_cell_style_for_row,
)
from .app_settings import get_system_owner, aablacklist_active
from .models import BigBrotherConfig

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

CARD_DEFINITIONS = [
    {"title": 'IMP Blacklist', "key": "imp_bl"},
    {"title": '<span style="color: Orange;"><b>WiP </b></span>LAWN Blacklist', "key": "lawn_bl"},
    {"title": 'Corp Blacklist', "key": "corp_bl"},
    {"title": 'Player Corp History', "key": "freq_corp"},
    {"title": 'Suspicious Contacts', "key": "sus_conta"},
    {"title": 'Suspicious Contracts', "key": "sus_contr"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Mails', "key": "sus_mail"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Suspicious Transactions', "key": "sus_tra"},
    {"title": 'AWOX Kills', "key": "awox"},
    {"title": 'Clones in hostile space', "key": "sus_clones"},
    {"title": 'Assets in hostile space', "key": "sus_asset"},
    {"title": '<span style="color: #FF0000;"><b>WiP </b></span>Cyno?', "key": "cyno"},
]


def get_user_id(character_name):
    try:
        ownership = CharacterOwnership.objects.select_related('user') \
            .get(character__character_name=character_name)
        return ownership.user.id
    except CharacterOwnership.DoesNotExist:
        return None


# Single-card loader
@login_required
@permission_required("aa_bb.basic_access")
def load_card(request):
    option = request.GET.get("option")
    idx    = request.GET.get("index")

    if option is None or idx is None:
        return HttpResponseBadRequest("Missing parameters")

    try:
        idx      = int(idx)
        card_def = CARD_DEFINITIONS[idx]
    except (ValueError, IndexError):
        return HttpResponseBadRequest("Invalid card index")

    key   = card_def["key"]
    title = card_def["title"]

    # Handle streaming for suspicious contracts
    if key == "sus_contr":
        # Delegate to the streaming view, return HTML directly
        return stream_contracts(request)

    # Otherwise generate content normally
    target_user_id = get_user_id(option)
    if target_user_id is None:
        return JsonResponse({"error": "Unknown account"}, status=404)

    content, status = get_card_data(request, target_user_id, key)
    return JsonResponse({
        "title":   title,
        "content": content,
        "status":  status,
    })


# Bulk loader (fallback, if used)
@login_required
@permission_required("aa_bb.basic_access")
def load_cards(request: WSGIRequest) -> JsonResponse:
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


# Index view
@login_required
@permission_required("aa_bb.basic_access")
def index(request: WSGIRequest):
    dropdown_options = []
    task_name = 'BB run regular updates'
    task = PeriodicTask.objects.filter(name=task_name).first()
    if not BigBrotherConfig.get_solo().is_active or (task and not task.enabled):
        msg = (
            "Big Brother is currently inactive; please fill settings and enable the task"
        )
        return render(request, "aa_bb/disabled.html", {"message": msg})

    if request.user.has_perm("aa_bb.full_access"):
        qs = UserProfile.objects.exclude(main_character=None)
    elif request.user.has_perm("aa_bb.recruiter_access"):
        qs = UserProfile.objects.filter(state=1).exclude(main_character=None)
    else:
        qs = None

    if qs is not None:
        dropdown_options = (
            qs.values_list("main_character__character_name", flat=True)
              .order_by("main_character__character_name")
        )
    return render(request, "aa_bb/index.html", {"dropdown_options": dropdown_options})


# Streaming view for Suspicious Contracts
@login_required
@permission_required("aa_bb.basic_access")
def stream_contracts(request: WSGIRequest):
    option = request.GET.get("option")
    if not option:
        return HttpResponseBadRequest("Missing account option")

    target_user_id = get_user_id(option)
    if target_user_id is None:
        return HttpResponseBadRequest("Unknown account")

    # Fetch and filter
    contracts  = get_user_contracts(target_user_id)
    all_rows   = sorted(
        contracts.values(), key=lambda x: x['issued_date'], reverse=True
    )
    hostile_rows = [r for r in all_rows if is_row_hostile(r)]

    if not hostile_rows:
        return StreamingHttpResponse(
            '<p>No hostile contracts found.</p>', content_type='text/html'
        )

    # Determine headers
    first = hostile_rows[0]
    HIDDEN = {
        'assignee_alliance_id', 'assignee_corporation_id',
        'issuer_alliance_id', 'issuer_corporation_id',
        'assignee_id', 'issuer_id', 'contract_id'
    }
    headers = [h for h in first.keys() if h not in HIDDEN]
    labels  = [html.escape(h.replace('_', ' ').title()) for h in headers]

    # Generator
    def row_generator():
        yield '<table class="table table-striped"><thead><tr>'
        for lab in labels:
            yield f'<th>{lab}</th>'
        yield '</tr></thead><tbody>'
        for row in hostile_rows:
            yield '<tr>'
            for col in headers:
                cell  = html.escape(str(row.get(col, '')))
                style = get_cell_style_for_row(col, row)
                if style:
                    yield f'<td style="{style}">{cell}</td>'
                else:
                    yield f'<td>{cell}</td>'
            yield '</tr>'
        yield '</tbody></table>'

    return StreamingHttpResponse(row_generator(), content_type='text/html')


# Card data helper

def get_card_data(request, target_user_id: int, key: str):
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
            "Go <a href='https://auth.lawnalliance.space/blacklist/blacklist/'>"  \
            "here</a> and check those names:<br>" + names
        )
        status  = False

    elif key == "corp_bl":
        issuer_id = request.user.id
        content   = get_corp_blacklist_html(request, issuer_id, target_user_id)
        status    = not (content and "🚩" in content)

    elif key == "sus_conta":
        content = render_contacts(target_user_id)
        status  = not (content and "red" in content)

    elif key == "sus_mail":
        content = sus_mail(target_user_id)
        status  = not content

    elif key == "sus_tra":
        content = sus_tra(target_user_id)
        status  = not content

    elif key == "cyno":
        content = cyno(target_user_id)
        status  = not content

    else:
        content = "WiP"
        status  = True

    return content, status


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
    return redirect(
        request.META.get("HTTP_REFERER", "/"),
        message=f"Blacklisted: {', '.join(added)}"
    )
