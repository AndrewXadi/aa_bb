import html
import logging

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.core.handlers.wsgi import WSGIRequest
from django.http import (
    JsonResponse,
    HttpResponseBadRequest,
    StreamingHttpResponse,
)
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.core.cache import cache
import time
from allianceauth.authentication.models import UserProfile, CharacterOwnership
from django_celery_beat.models import PeriodicTask
from django.utils.safestring import mark_safe
import json
from django.http import HttpResponseForbidden
from .forms import LeaveRequestForm
from aa_bb.checks.awox import render_awox_kills_html
from aa_bb.checks.corp_changes import get_frequent_corp_changes
from aa_bb.checks.cyno import render_user_cyno_info_html
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
from aa_bb.checks.sus_trans import (
    get_user_transactions,
    is_transaction_hostile,
    gather_user_transactions,
    render_transactions,
    SUS_TYPES,
)
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
from .app_settings import get_system_owner, aablacklist_active, get_user_characters, get_entity_info, get_main_character_name, get_character_id, send_message
from .models import BigBrotherConfig, WarmProgress, LeaveRequest
from corptools.models import Contract  # Ensure this is the correct import for Contract model
#from datetime import datetime
from django.utils import timezone
from celery import shared_task
from celery.exceptions import Ignore
from aa_bb.checks.skills import render_user_skills_html

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
    {"title": 'Suspicious Transactions', "key": "sus_tra"},
    {"title": 'Cyno?', "key": "cyno"},
    {"title": 'Skills', "key": "skills"},
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
    logger.info(key)
    if key in ("sus_contr", "sus_mail","sus_tra"):
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

@shared_task(bind=True)
def warm_entity_cache_task(self, user_id):
    """
    Gather mails, contracts, transactions; warm entity cache.
    Track progress in the DB via WarmProgress.
    """
    user_main = get_main_character_name(user_id) or str(user_id)
    qs = WarmProgress.objects.all()
    users = [
        {"user": wp.user_main, "current": wp.current, "total": wp.total}
        for wp in qs
    ]
    # Check for existing progress entry
    try:
        progress = WarmProgress.objects.get(user_main=user_main)
    except WarmProgress.DoesNotExist:
        progress = None

    if progress and progress.total > 0:
        first_current = progress.current
        logger.info(f"[{user_main}] detected in-progress run (current={first_current}); probing…")
        time.sleep(20)

        # re-fetch to see if it's moved
        try:
            progress = WarmProgress.objects.get(user_main=user_main)
            second_current = progress.current
        except WarmProgress.DoesNotExist:
            second_current = None

        # Now *abort* if there *was* progress; otherwise continue
        if second_current != first_current:
            logger.info(
                f"[{user_main}] progress advanced from {first_current} to {second_current}; aborting new task."
            )
            raise Ignore(f"Task for {user_main} is already running.")
        else:
            logger.info(
                f"[{user_main}] no progress in 20 s (still {first_current}); continuing with new task."
            )

    # Build list of (entity_id, timestamp)
    entries = []
    for c in gather_user_contracts(user_id):
        issuer_id = get_character_id(c.issuer_name)
        entries.append((issuer_id, getattr(c, "date_issued")))
        assignee = c.assignee_id or c.acceptor_id
        entries.append((assignee, getattr(c, "date_issued")))
    for m in gather_user_mails(user_id):
        entries.append((m.from_id, getattr(m, "timestamp")))
        for mr in m.recipients.all():
            entries.append((mr.recipient_id, getattr(m, "timestamp")))
    for t in gather_user_transactions(user_id):
        entries.append((t.first_party_id, getattr(t, "date")))
        entries.append((t.second_party_id, getattr(t, "date")))

    total = len(entries)
    logger.info(f"Starting warm cache for {user_main} ({total} entries)")

    # Initialize or update the progress record
    WarmProgress.objects.update_or_create(
        user_main=user_main,
        defaults={"current": 0, "total": total}
    )

    # Process each entry, updating the DB record
    for idx, (eid, ts) in enumerate(entries, start=1):
        WarmProgress.objects.filter(user_main=user_main).update(current=idx)
        get_entity_info(eid, ts)

    # Clean up when done
    WarmProgress.objects.filter(user_main=user_main).delete()
    logger.info(f"Completed warm cache for {user_main}")
    return total

@login_required
@permission_required("aa_bb.basic_access")
def warm_cache(request):
    """
    Endpoint to kick off warming for a given character name (option).
    Immediately registers a WarmProgress row so queued tasks also appear.
    """
    option  = request.GET.get("option", "")
    user_id = get_user_id(option)
    if not user_id:
        return JsonResponse({"error": "Unknown account"}, status=400)

    # Pre-create progress record so queued jobs show up
    user_main = get_main_character_name(user_id) or str(user_id)
    WarmProgress.objects.get_or_create(
        user_main=user_main,
        defaults={"current": 0, "total": 0}
    )

    # Enqueue the celery task
    warm_entity_cache_task.delay(user_id)
    return JsonResponse({"started": True})


@login_required
@permission_required("aa_bb.basic_access")
def get_warm_progress(request):
    """
    AJAX endpoint returning all in-flight and queued warm-up info:
      {
        in_progress: bool,
        users: [ { user, current, total }, … ],
        queued: { count, names: [...] }
      }
    """
    qs = WarmProgress.objects.all()
    users = [
        {"user": wp.user_main, "current": wp.current, "total": wp.total}
        for wp in qs
    ]
    # Those still at current == 0 are queued/not yet started
    queued_names = [wp.user_main for wp in qs if wp.current == 0]

    #logger.debug(f"get_warm_progress → users={users}, queued={queued_names}")
    return JsonResponse({
        "in_progress": bool(users),
        "users": users,
        "queued": {
            "count": len(queued_names),
            "names": queued_names,
        },
    })

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

    def generator():
        # Initial SSE heartbeat
        yield ": ok\n\n"
        processed = hostile_count = 0

        if total == 0:
            # tell client we're done with zero hostile
            yield "event: done\ndata:0\n\n"
            return

        for c in qs:
            processed += 1
            # Ping to keep connection alive
            yield ": ping\n\n"

            issued = getattr(c, "date_issued", timezone.now())
            issuer_id = get_character_id(c.issuer_name)
            yield ": ping\n\n"
            cid = c.contract_id
            if c.assignee_id != 0:
                assignee_id = c.assignee_id
            else:
                assignee_id = c.acceptor_id
            yield ": ping\n\n"
            logger.info(f"getting info for {issuer_id}")
            iinfo     = get_entity_info(issuer_id, issued)
            yield ": ping\n\n"
            logger.info(f"getting info for {assignee_id}")
            ainfo     = get_entity_info(assignee_id, issued)
            yield ": ping\n\n"

            # Hydrate just this one

            row = {
                'contract_id':              cid,
                'issued_date':              issued,
                'end_date':                 c.date_completed or c.date_expired,
                'contract_type':            c.contract_type,
                'issuer_name':              iinfo["name"],
                'issuer_id':                issuer_id,
                'issuer_corporation':       iinfo["corp_name"],
                'issuer_corporation_id':    iinfo["corp_id"],
                'issuer_alliance':          iinfo["alli_name"],
                'issuer_alliance_id':       iinfo["alli_id"],
                'assignee_name':            ainfo["name"],
                'assignee_id':              assignee_id,
                'assignee_corporation':     ainfo["corp_name"],
                'assignee_corporation_id':  ainfo["corp_id"],
                'assignee_alliance':        ainfo["alli_name"],
                'assignee_alliance_id':     ainfo["alli_id"],
                'status':                   c.status,
            }

            style_map = {
                col: get_cell_style_for_contract_row(col, row)
                for col in row
            }
            yield ": ping\n\n"
            row['cell_styles'] = style_map

            if is_contract_row_hostile(row):
                hostile_count += 1
                tr_html = _render_contract_row_html(row)
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

VISIBLE_CONTR = [
    "issued_date", "end_date",
    "contract_type", "issuer_name", "issuer_corporation",
    "issuer_alliance", "assignee_name", "assignee_corporation",
    "assignee_alliance", "status",
]

def _render_contract_row_html(row: dict) -> str:
    """
    Render one hostile contract row, applying inline styles 
    from row['cell_styles'] *or* from any hidden-ID–based flags.
    """
    cells = []

    # for any visible header like "issuer_name", map its ID column:
    def id_for(col):
        if col.endswith("_name"):
            return col[:-5] + "_id"
        elif col.endswith("_corporation"):
            return col[:-12] + "_corporation_id"
        elif col.endswith("_alliance"):
            return col[:-9] + "_alliance_id"
        return None

    style_map = row.get('cell_styles', {})

    for col in VISIBLE_CONTR:
        val   = row.get(col, "")
        text  = html.escape(str(val))

        # first, try the direct style:
        style = style_map.get(col, "") or ""

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
            if col == "sender_name":
                for key in ["GM ","CCP "]:
                    if key in str(row["sender_name"]):
                        style = "color:red;"
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


@login_required
@permission_required("aa_bb.basic_access")
def stream_transactions_sse(request):
    """
    Stream hostile wallet‐transactions one <tr> at a time via SSE,
    hydrating first‐ and second‐party info on the fly.
    """
    option  = request.GET.get("option", "")
    user_id = get_user_id(option)
    if not user_id:
        return HttpResponseBadRequest("Unknown account")

    qs    = gather_user_transactions(user_id)
    total = qs.count()
    if total == 0:
        return StreamingHttpResponse(
            "<p>No transactions found.</p>",
            content_type="text/html"
        )

    # Determine headers from a single hydrated row
    sample = qs[:1]
    sample_map    = get_user_transactions(sample)
    sample_row    = next(iter(sample_map.values()))
    HIDDEN        = {
        'first_party_id','second_party_id',
        'first_party_corporation_id','second_party_corporation_id',
        'first_party_alliance_id','second_party_alliance_id',
        'entry_id'
    }
    headers = [h for h in sample_row.keys() if h not in HIDDEN]

    def generator():
        yield ": ok\n\n"                # initial heartbeat
        processed = hostile_count = 0

        # Emit table header row once
        header_html = (
            "<tr>" +
            "".join(f"<th>{html.escape(h.replace('_',' ').title())}</th>" for h in headers) +
            "</tr>"
        )
        yield f"event: header\ndata:{json.dumps(header_html)}\n\n"

        for entry in qs:
            processed += 1
            yield ": ping\n\n"         # keep‐alive

            # hydrate this one entry
            row = get_user_transactions([entry])[entry.entry_id]

            if is_transaction_hostile(row):
                hostile_count += 1

                # build the <tr> using same style logic as render_transactions()
                cells = []
                cfg = BigBrotherConfig.get_solo()
                for col in headers:
                    val = row.get(col, "")
                    text = html.escape(str(val))
                    style = ""
                    # type‐based red
                    if col == 'type' and any(st in row['type'] for st in SUS_TYPES):
                        style = 'color:red;'
                    # first/second party name
                    if col in ('first_party_name','second_party_name'):
                        id_col = col.replace("_name", "_id")
                        pid = row[id_col]
                        if check_char_corp_bl(pid):
                            style = 'color:red;'
                    # corps & alliances
                    if col.endswith('corporation'):
                        cid = row[f"{col}_id"]
                        if cid and str(cid) in cfg.hostile_corporations:
                            style = 'color:red;'
                    if col.endswith('alliance'):
                        aid = row[f"{col}_id"]
                        if aid and str(aid) in cfg.hostile_alliances:
                            style = 'color:red;'
                    def make_td(text, style=""):
                        style_attr = f' style="{style}"' if style else ""
                        return f"<td{style_attr}>{text}</td>"
                    cells.append(make_td(text, style))
                tr_html = "<tr>" + "".join(cells) + "</tr>"
                yield f"event: transaction\ndata:{json.dumps(tr_html)}\n\n"

            # progress update
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
        content = render_transactions(target_user_id)
        status  = not content

    elif key == "cyno":
        content = render_user_cyno_info_html(target_user_id)
        status  = not (content and "red" in content)

    elif key == "skills":
        content = render_user_skills_html(target_user_id)
        status  = not (content and "red" in content)

    else:
        content = "WiP"
        status  = True

    return content, status


@require_POST
@permission_required("can_blacklist_characters")
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


@login_required
@permission_required("aa_bb.can_access_loa")
def loa_loa(request):
    cfg = BigBrotherConfig.get_solo()
    if not cfg.is_loa_active:
        return render(request, "loa/disabled.html")
    user_requests = LeaveRequest.objects.filter(user=request.user).order_by('-created_at')
    return render(request, "loa/index.html", {"loa_requests": user_requests})

@login_required
@permission_required("aa_bb.can_view_all_loa")
def loa_admin(request):
    cfg = BigBrotherConfig.get_solo()
    if not cfg.is_loa_active:
        return render(request, "loa/disabled.html")
    # Filtering
    qs = LeaveRequest.objects.select_related('user').order_by('-created_at')
    user_filter   = request.GET.get('user')
    status_filter = request.GET.get('status')

    if user_filter:
        qs = qs.filter(user__id=user_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)

    # Build dropdown options from existing requests
    users_in_requests = (
        LeaveRequest.objects
                    .values_list('user__id', 'user__username')
                    .distinct()
    )

    context = {
        'loa_requests': qs,
        'users': users_in_requests,
        'status_choices': LeaveRequest.STATUS_CHOICES,
        'current_user': user_filter,
        'current_status': status_filter,
    }
    return render(request, "loa/admin.html", context)

@login_required
@permission_required("aa_bb.can_access_loa")
def loa_request(request):
    cfg = BigBrotherConfig.get_solo()
    if not cfg.is_loa_active:
        return render(request, "loa/disabled.html")

    if request.method == 'POST':
        form = LeaveRequestForm(request.POST)
        if form.is_valid():
            main_char = get_main_character_name(request.user.id)
            # 2) save with main_character
            lr = form.save(commit=False)
            lr.user = request.user
            lr.main_character = main_char
            lr.save()

            # 3) send webhook with character
            hook = cfg.loawebhook
            pingroleID = cfg.pingroleID
            send_message(f"## <@&{pingroleID}> {main_char} requested LOA:\n- from **{lr.start_date}**\n- to **{lr.end_date}**\n- reason: **{lr.reason}**", hook)

            return redirect('loa:index')
        else:
            form.add_error(None, "Please fill in all fields correctly.")
    else:
        form = LeaveRequestForm()

    return render(request, 'loa/request.html', {'form': form})

@login_required
@permission_required("aa_bb.can_access_loa")
def delete_request(request, pk):
    if request.method == 'POST':
        lr = get_object_or_404(LeaveRequest, pk=pk, user=request.user)
        if lr.user != request.user:
            return HttpResponseForbidden("You may only delete your own requests.")
        elif lr.status == 'pending':
            lr.delete()
            hook = BigBrotherConfig.get_solo().loawebhook
            send_message(f"## {lr.main_character} deleted their LOA:\n- from **{lr.start_date}**\n- to **{lr.end_date}**\n- reason: **{lr.reason}**", hook)
    return redirect('loa:index')

@login_required
@permission_required("aa_bb.can_manage_loa")
def delete_request_admin(request, pk):
    if request.method == 'POST':
        lr = get_object_or_404(LeaveRequest, pk=pk, user=request.user)
        lr.delete()
        hook = BigBrotherConfig.get_solo().loawebhook
        userrr = get_main_character_name(request.user.id)
        send_message(f"## {userrr} deleted {lr.main_character}'s LOA:\n- from **{lr.start_date}**\n- to **{lr.end_date}**\n- reason: **{lr.reason}**", hook)
    return redirect('loa:admin')

@login_required
@permission_required("aa_bb.can_manage_loa")
def approve_request(request, pk):
    if request.method == 'POST':
        lr = get_object_or_404(LeaveRequest, pk=pk)
        lr.status = 'approved'
        lr.save()
        hook = BigBrotherConfig.get_solo().loawebhook
        userrr = get_main_character_name(request.user.id)
        send_message(f"## {userrr} approved {lr.main_character}'s LOA:\n- from **{lr.start_date}**\n- to **{lr.end_date}**\n- reason: **{lr.reason}**", hook)
    return redirect('loa:admin')

@login_required
@permission_required("aa_bb.can_manage_loa")
def deny_request(request, pk):
    if request.method == 'POST':
        lr = get_object_or_404(LeaveRequest, pk=pk)
        lr.status = 'denied'
        lr.save()
        hook = BigBrotherConfig.get_solo().loawebhook
        userrr = get_main_character_name(request.user.id)
        send_message(f"## {userrr} denied {lr.main_character}'s LOA:\n- from **{lr.start_date}**\n- to **{lr.end_date}**\n- reason: **{lr.reason}**", hook)
    return redirect('loa:admin')