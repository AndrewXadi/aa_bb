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
from django.core.cache import cache

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
    gather_user_contracts,
)
from .app_settings import get_system_owner, aablacklist_active, get_user_characters
from .models import BigBrotherConfig
from corptools.models import Contract  # Ensure this is the correct import for Contract model

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

CARD_DEFINITIONS = [
    {"title": 'IMP Blacklist', "key": "imp_bl"},
    {"title": '<span style=\"color: Orange;\"><b>WiP </b></span>LAWN Blacklist', "key": "lawn_bl"},
    {"title": 'Corp Blacklist', "key": "corp_bl"},
    {"title": 'Player Corp History', "key": "freq_corp"},
    {"title": 'Suspicious Contacts', "key": "sus_conta"},
    {"title": 'Suspicious Contracts', "key": "sus_contr"},
    {"title": '<span style=\"color: #FF0000;\"><b>WiP </b></span>Suspicious Mails', "key": "sus_mail"},
    {"title": '<span style=\"color: #FF0000;\"><b>WiP </b></span>Suspicious Transactions', "key": "sus_tra"},
    {"title": 'AWOX Kills', "key": "awox"},
    {"title": 'Clones in hostile space', "key": "sus_clones"},
    {"title": 'Assets in hostile space', "key": "sus_asset"},
    {"title": '<span style=\"color: #FF0000;\"><b>WiP </b></span>Cyno?', "key": "cyno"},
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

    if key == "sus_contr":
        # handled via paginated endpoints
        return JsonResponse({"key": key, "title": title})

    target_user_id = get_user_id(option)
    if target_user_id is None:
        return JsonResponse({"error": "Unknown account"}, status=404)

    content, status = get_card_data(request, target_user_id, key)
    return JsonResponse({
        "title":   title,
        "content": content,
        "status":  status,
    })


# Bulk loader (fallback)
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
    context = {
        "dropdown_options": dropdown_options,
        "CARD_DEFINITIONS": CARD_DEFINITIONS,     # ← add this
    }
    return render(request, "aa_bb/index.html", context)


# Paginated endpoints for Suspicious Contracts
@login_required
@permission_required("aa_bb.basic_access")
def list_contract_ids(request):
    """
    Return JSON list of all contract IDs and issue dates for the selected user.
    """
    option = request.GET.get("option")
    user_id = get_user_id(option)
    if user_id is None:
        return JsonResponse({"error": "Unknown account"}, status=404)

    user_chars = get_user_characters(user_id)
    qs = Contract.objects.filter(
        character__character__character_id__in=user_chars
    ).order_by('-date_issued').values_list('contract_id', 'date_issued')

    contracts = [
        {'id': cid, 'date': dt.isoformat()} for cid, dt in qs
    ]
    return JsonResponse({'contracts': contracts})


@login_required
@permission_required("aa_bb.basic_access")
def check_contract_batch(request):
    """
    Check a slice of contracts for hostility by start/limit parameters.
    Returns JSON with `checked` count and list of `hostile_found`,
    each entry including a `cell_styles` dict for inline styling.
    Now uses gather_user_contracts + get_user_contracts(qs) on the full set.
    """
    option = request.GET.get("option")
    start  = int(request.GET.get("start", 0))
    limit  = int(request.GET.get("limit", 10))
    user_id = get_user_id(option)
    if user_id is None:
        return JsonResponse({"error": "Unknown account"}, status=404)

    # 1) Ensure we have the full QuerySet
    cache_key = f"contract_qs_{user_id}"
    qs_all = cache.get(cache_key)
    if qs_all is None:
        qs_all = gather_user_contracts(user_id)
        cache.set(cache_key, qs_all, 300)

    # 2) Slice out just this batch of model instances
    batch_qs = qs_all[start:start + limit]

    # 3) Hydrate only this batch
    batch_map = get_user_contracts(batch_qs)

    HIDDEN = {
        'assignee_alliance_id', 'assignee_corporation_id',
        'issuer_alliance_id', 'issuer_corporation_id',
        'assignee_id', 'issuer_id', 'contract_id'
    }

    hostile = []
    for cid, row in batch_map.items():
        if is_row_hostile(row):
            # build style map for visible columns
            style_map = {
                col: get_cell_style_for_row(col, row)
                for col in row
                if col not in HIDDEN
            }
            # package only the visible fields + styles
            payload = {col: row[col] for col in row if col not in HIDDEN}
            payload['cell_styles'] = style_map
            hostile.append(payload)

    return JsonResponse({
        'checked': len(batch_qs),
        'hostile_found': hostile
    })




@login_required
@permission_required("aa_bb.basic_access")
def stream_contracts(request: WSGIRequest):
    option = request.GET.get("option")
    if not option:
        return HttpResponseBadRequest("Missing account option")

    user_id = get_user_id(option)
    if user_id is None:
        return HttpResponseBadRequest("Unknown account")

    # 1) Grab the entire QuerySet (fast DB filter, no per-row work)
    qs = gather_user_contracts(user_id)
    total = qs.count()

    if total == 0:
        return StreamingHttpResponse(
            '<p>No contracts found.</p>', content_type='text/html'
        )

    batch_size = 10

    def generator():
        # We'll derive headers on first batch
        headers = None

        # Emit table start
        yield '<table class="table table-striped"><thead><tr>'

        processed = 0
        for offset in range(0, total, batch_size):
            batch_qs = qs[offset:offset + batch_size]

            # 2) Build full details just for this batch
            batch_map = get_user_contracts(batch_qs)  # your updated function
            rows = sorted(
                batch_map.values(),
                key=lambda x: x['issued_date'],
                reverse=True
            )

            # On the very first batch, figure out the visible columns:
            if headers is None:
                HIDDEN = {
                    'assignee_alliance_id','assignee_corporation_id',
                    'issuer_alliance_id','issuer_corporation_id',
                    'assignee_id','issuer_id','contract_id'
                }
                headers = [h for h in rows[0].keys() if h not in HIDDEN]
                # Emit header cells
                for h in headers:
                    label = html.escape(h.replace('_', ' ').title())
                    yield f'<th>{label}</th>'
                yield '</tr></thead><tbody>'

            # 3) Stream only the hostile rows in this batch
            batch_hostile = [r for r in rows if is_row_hostile(r)]
            for row in batch_hostile:
                yield '<tr>'
                for col in headers:
                    cell = html.escape(str(row.get(col, '')))
                    style = get_cell_style_for_row(col, row) or ''
                    if style:
                        yield f'<td style="{style}">{cell}</td>'
                    else:
                        yield f'<td>{cell}</td>'
                yield '</tr>'

            processed += len(batch_qs)
            # 4) Emit a little footer after each batch
            yield (
                f'<tr><td colspan="{len(headers)}" '
                f'style="font-style: italic; text-align:center;">'
                f'Processed {processed}/{total} contracts…</td></tr>'
            )

        # Close table
        yield '</tbody></table>'

    return StreamingHttpResponse(generator(), content_type='text/html')



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
            "Go <a href='https://auth.lawnalliance.space/blacklist/blacklist/'>"
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
