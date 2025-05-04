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
from django.utils.safestring import mark_safe
import json
from aa_bb.checks.awox import render_awox_kills_html
from aa_bb.checks.corp_changes import get_frequent_corp_changes
from aa_bb.checks.cyno import cyno
from aa_bb.checks.hostile_assets import render_assets
from aa_bb.checks.hostile_clones import render_clones
from aa_bb.checks.imp_blacklist import generate_blacklist_links
from aa_bb.checks.lawn_blacklist import get_user_character_names_lawn
from aa_bb.checks.notifications import game_time, skill_injected
from aa_bb.checks.sus_contacts import render_contacts
from aa_bb.checks.sus_mails import (
    get_user_mails,
    is_mail_row_hostile,
    get_cell_style_for_mail_cell,
    gather_user_mails,
    render_mails,
)
from aa_bb.checks.sus_trans import sus_tra
from aa_bb.checks.corp_blacklist import (
    get_corp_blacklist_html,
    add_user_characters_to_blacklist,
    check_char_corp_bl,
)
from aa_bb.checks.sus_contracts import (
    get_user_contracts,
    is_contract_row_hostile,
    get_cell_style_for_contract_row,
    gather_user_contracts,
)
from .app_settings import get_system_owner, aablacklist_active, get_user_characters, get_entity_info
from .models import BigBrotherConfig
from corptools.models import Contract  # Ensure this is the correct import for Contract model
#from datetime import datetime
from django.utils import timezone
from celery import shared_task

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

CARD_DEFINITIONS = [
    {"title": 'IMP Blacklist', "key": "imp_bl"},
    {"title": '<span style=\"color: Orange;\"><b>WiP </b></span>LAWN Blacklist', "key": "lawn_bl"},
    {"title": 'Corp Blacklist', "key": "corp_bl"},
    {"title": 'Player Corp History', "key": "freq_corp"},
    {"title": 'AWOX Kills', "key": "awox"},
    {"title": 'Clones in hostile space', "key": "sus_clones"},
    {"title": 'Assets in hostile space', "key": "sus_asset"},
    {"title": 'Suspicious Contacts', "key": "sus_conta"},
    {"title": 'Suspicious Contracts', "key": "sus_contr"},
    {"title": 'Suspicious Mails', "key": "sus_mail"},
    {"title": '<span style=\"color: #FF0000;\"><b>WiP </b></span>Suspicious Transactions', "key": "sus_tra"},
    {"title": '<span style=\"color: #FF0000;\"><b>WiP </b></span>Cyno?', "key": "cyno"},
]


def get_user_id(character_name):
    try:
        ownership = CharacterOwnership.objects.select_related('user') \
            .get(character__character_name=character_name)
        return ownership.user.id
    except CharacterOwnership.DoesNotExist:
        return None

def get_mail_keywords():
    return BigBrotherConfig.get_solo().mail_keywords

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

    if key in ("sus_contr", "sus_mail"):
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
    warm_entity_cache_task.delay(user_id)
    cards = []
    for card in CARD_DEFINITIONS:
        content, status = get_card_data(request, user_id, card["key"])
        cards.append({
            "title":   card["title"],
            "content": content,
            "status":  status,
        })
    return JsonResponse({"cards": cards})

@login_required
@permission_required("aa_bb.basic_access")
def warm_cache(request):
    option  = request.GET.get("option", "")
    user_id = get_user_id(option)
    if user_id:
        warm_entity_cache_task.delay(user_id)
        return JsonResponse({"started": True})
    return JsonResponse({"error": "Unknown account"}, status=400)

@shared_task
def warm_entity_cache_task(user_id):
    qs    = gather_user_mails(user_id)
    unique_ids = set()
    # map mail_id -> its timestamp for as_of
    mail_timestamps = {}
    for m in qs:
        unique_ids.add(m.from_id)
        mail_timestamps[m.id_key] = getattr(m, "timestamp", timezone.now())
        for mr in m.recipients.all():
            unique_ids.add(mr.recipient_id)
    for eid in unique_ids:
        ts = mail_timestamps.get(eid, timezone.now())
        get_entity_info(eid, ts)


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
        "CARD_DEFINITIONS": CARD_DEFINITIONS,
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
        if is_contract_row_hostile(row):
            # build style map for visible columns
            style_map = {
                col: get_cell_style_for_contract_row(col, row)
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
def stream_contracts_sse(request: WSGIRequest):
    option = request.GET.get("option", "")
    user_id = get_user_id(option)
    if not user_id:
        return HttpResponseBadRequest("Unknown account")

    qs    = gather_user_contracts(user_id)
    total = qs.count()
    if total == 0:
        # No contracts
        return StreamingHttpResponse(
            '<p>No contracts found.</p>', content_type='text/html'
        )

    # Prepare header list once
    # We peek at one hydrated row to determine headers:
    sample_batch = qs[:1]
    sample_map   = get_user_contracts(sample_batch)
    sample_row   = next(iter(sample_map.values()))
    HIDDEN       = {
        'assignee_alliance_id','assignee_corporation_id',
        'issuer_alliance_id','issuer_corporation_id',
        'assignee_id','issuer_id','contract_id'
    }
    headers = [h for h in sample_row.keys() if h not in HIDDEN and h != 'cell_styles']

    def generator():
        # Initial SSE heartbeat
        yield ": ok\n\n"
        processed = hostile_count = 0

        # Stream header once as an SSE event
        header_html = (
            "<tr>" +
            "".join(f"<th>{html.escape(h.replace('_',' ').title())}</th>" for h in headers) +
            "</tr>"
        )
        yield f"event: header\ndata:{json.dumps(header_html)}\n\n"

        for contract in qs:
            processed += 1
            # Ping to keep connection alive
            yield ": ping\n\n"

            # Hydrate just this one
            batch_map = get_user_contracts([contract])
            row = next(iter(batch_map.values()))

            style_map = {
                col: get_cell_style_for_contract_row(col, row)
                for col in headers
            }
            row['cell_styles'] = style_map

            if is_contract_row_hostile(row):
                hostile_count += 1
                tr_html = _render_contract_row_html(row, headers)
                yield f"event: contract\ndata:{json.dumps(tr_html)}\n\n"

            # Progress update
            yield (
                "event: progress\n"
                f"data:{processed},{total},{hostile_count}\n\n"
            )

        # Done
        yield "event: done\ndata:bye\n\n"

    resp = StreamingHttpResponse(generator(), content_type='text/event-stream')
    resp["Cache-Control"]     = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp



VISIBLE = [
    "sent_date", "subject",
    "sender_name", "sender_corporation", "sender_alliance",
    "recipient_names", "recipient_corps", "recipient_alliances",
    "content", "status",
]

def _render_contract_row_html(row: dict, headers: list) -> str:
    """
    Render one hostile contract row, applying inline styles 
    from row['cell_styles'] *or* from any hidden-ID–based flags.
    """
    cells = []

    # for any visible header like "issuer_name", map its ID column:
    def id_for(col):
        # replace suffixes _name, _corporation, _alliance with _id
        for suffix in ("_name", "_corporation", "_alliance"):
            if col.endswith(suffix):
                return col.replace(suffix, "_id")
        return None

    style_map = row.get('cell_styles', {})

    for col in headers:
        val   = row.get(col, "")
        text  = html.escape(str(val))

        # first, try the direct style:
        style = style_map.get(col, "")

        # if none, see if there's a hidden-ID style to inherit:
        if not style:
            id_col = id_for(col)
            if id_col:
                # compute style on the ID column if not already done
                style = style_map.get(id_col) or get_cell_style_for_contract_row(id_col, row)

        # render the cell
        if style:
            cells.append(f'<td style="{style}">{text}</td>')
        else:
            cells.append(f'<td>{text}</td>')

    return "<tr>" + "".join(cells) + "</tr>"

def _render_mail_row_html(row: dict) -> str:
    """
    Render a single hostile mail row as <tr>…</tr> using only VISIBLE columns,
    applying red styling to any name whose ID is hostile.
    """
    cells = []
    cfg = BigBrotherConfig.get_solo()

    for col in VISIBLE:
        val = row.get(col, "")
        # recipients come as lists
        if isinstance(val, list):
            spans = []
            for i, item in enumerate(val):
                style = ""
                if col == "recipient_names":
                    rid = row["recipient_ids"][i]
                    if check_char_corp_bl(rid):
                        style = "color:red;"
                elif col == "recipient_corps":
                    cid = row["recipient_corp_ids"][i]
                    if cid and str(cid) in cfg.hostile_corporations:
                        style = "color:red;"
                elif col == "recipient_alliances":
                    aid = row["recipient_alliance_ids"][i]
                    if aid and str(aid) in cfg.hostile_alliances:
                        style = "color:red;"
                span = (
                    f'<span style="{style}">{html.escape(str(item))}</span>'
                    if style else
                    f'<span>{html.escape(str(item))}</span>'
                )
                spans.append(span)
            cell_html = ", ".join(spans)
        else:
            # single-valued columns: subject, content, sender_*
            style = ""
            if col.startswith("sender_"):
                style = get_cell_style_for_mail_cell(col, row, None)
            if style:
                cell_html = f'<span style="{style}">{html.escape(str(val))}</span>'
            else:
                cell_html = html.escape(str(val))
        cells.append(f"<td>{cell_html}</td>")

    return "<tr>" + "".join(cells) + "</tr>"

@login_required
@permission_required("aa_bb.basic_access")
def stream_mails_sse(request):
    """Stream hostile mails one row at a time via SSE, hydrating sender+recipients."""
    option  = request.GET.get("option", "")
    user_id = get_user_id(option)
    if not user_id:
        return HttpResponseBadRequest("Unknown account")

    qs    = gather_user_mails(user_id)
    total = qs.count()
    if total == 0:
        return StreamingHttpResponse("<p>No mails found.</p>",
                                     content_type="text/html")

    def generator():
        # initial SSE heartbeat
        yield ": ok\n\n"
        processed = hostile_count = 0

        for m in qs:
            processed += 1
            # per-mail ping
            yield ": ping\n\n"

            sent = getattr(m, "timestamp", timezone.now())

            # 1) hydrate sender
            sender_id = m.from_id
            logger.info(f"getting info for {sender_id}")
            sinfo     = get_entity_info(sender_id, sent)
            yield ": ping\n\n"  # immediately after expensive call

            # 2) hydrate each recipient
            recipient_ids           = []
            recipient_names         = []
            recipient_corps         = []
            recipient_corp_ids      = []
            recipient_alliances     = []
            recipient_alliance_ids  = []
            for mr in m.recipients.all():
                rid   = mr.recipient_id
                logger.info(f"getting info for {rid}")
                rinfo = get_entity_info(rid, sent)
                yield ": ping\n\n"  # after each recipient lookup

                recipient_ids.append(rid)
                recipient_names.append(rinfo["name"])
                recipient_corps.append(rinfo["corp_name"])
                recipient_corp_ids.append(rinfo["corp_id"])
                recipient_alliances.append(rinfo["alli_name"])
                recipient_alliance_ids.append(rinfo["alli_id"])

            # build our single-mail row dict
            row = {
                "message_id":              m.id_key,
                "sent_date":               sent,
                "subject":                 m.subject or "",
                "sender_name":             sinfo["name"],
                "sender_id":               sender_id,
                "sender_corporation":      sinfo["corp_name"],
                "sender_corporation_id":   sinfo["corp_id"],
                "sender_alliance":         sinfo["alli_name"],
                "sender_alliance_id":      sinfo["alli_id"],
                "recipient_names":         recipient_names,
                "recipient_ids":           recipient_ids,
                "recipient_corps":         recipient_corps,
                "recipient_corp_ids":      recipient_corp_ids,
                "recipient_alliances":     recipient_alliances,
                "recipient_alliance_ids":  recipient_alliance_ids,
                "status":                  "Read" if m.is_read else "Unread",
            }

            # 3) check hostility and, if hostile, stream the <tr>
            if is_mail_row_hostile(row):
                hostile_count += 1
                tr = _render_mail_row_html(row)
                yield f"event: mail\ndata:{json.dumps(tr)}\n\n"

            # 4) final per-mail progress
            yield (
                "event: progress\n"
                f"data:{processed},{total},{hostile_count}\n\n"
            )

        # done
        yield "event: done\ndata:bye\n\n"

    resp = StreamingHttpResponse(generator(),
                                 content_type="text/event-stream")
    resp["Cache-Control"]     = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


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
        content = render_mails(target_user_id)
        status  = not (content and "red" in content)

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
