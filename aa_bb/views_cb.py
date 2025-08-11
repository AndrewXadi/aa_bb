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
from allianceauth.authentication.models import CharacterOwnership
from django_celery_beat.models import PeriodicTask
from django.utils.safestring import mark_safe
import json
from django.http import HttpResponseForbidden
from .forms import LeaveRequestForm
from aa_bb.checks.awox import render_awox_kills_html
from aa_bb.checks.corp_changes import get_frequent_corp_changes
from aa_bb.checks.cyno import render_user_cyno_info_html
from aa_bb.checks_cb.hostile_assets import render_assets
from aa_bb.checks.hostile_clones import render_clones
from aa_bb.checks.imp_blacklist import generate_blacklist_links
from aa_bb.checks.lawn_blacklist import get_user_character_names_lawn
from aa_bb.checks.notifications import game_time, skill_injected
from aa_bb.checks_cb.sus_trans import (
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
from aa_bb.checks_cb.sus_contracts import (
    get_user_contracts,
    is_contract_row_hostile,
    get_cell_style_for_contract_row,
    gather_user_contracts,
)
from .app_settings import get_system_owner, aablacklist_active, get_user_characters, get_entity_info, get_main_character_name, get_character_id, send_message, get_pings, resolve_corporation_name
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
    {"title": 'Assets in hostile space', "key": "sus_asset"},
    {"title": 'Suspicious Contracts', "key": "sus_contr"},
    {"title": 'Suspicious Transactions', "key": "sus_tra"},
]


from esi.models import Token
from allianceauth.eveonline.models import EveCorporationInfo

# Index view
@login_required
@permission_required("aa_bb.basic_access_cb")
def index(request: WSGIRequest):
    dropdown_options = []
    task_name = 'BB run regular updates'
    task = PeriodicTask.objects.filter(name=task_name).first()
    if not BigBrotherConfig.get_solo().is_active or (task and not task.enabled):
        msg = (
            "Corp Brother is currently inactive; please fill settings and enable the task"
        )
        return render(request, "aa_cb/disabled.html", {"message": msg})
    ignored_str = BigBrotherConfig.get_solo().ignored_corporations or ""
    ignored_ids = {int(s) for s in ignored_str.split(",") if s.strip().isdigit()}
    ignored_corps = EveCorporationInfo.objects.filter(
            corporation_id__in=ignored_ids).distinct()
    logger.info(f"ignored ids: {str(ignored_ids)}, corps {len(ignored_corps)}")

    if request.user.has_perm("aa_bb.full_access_cb"):
        # Full access: all registered corporations
        qs = EveCorporationInfo.objects.all()

    elif request.user.has_perm("aa_bb.recruiter_access_cb"):
        # Recruiter: corps with tokens added by guest-state users
        qs = EveCorporationInfo.objects.filter(
            corporation_id__in=Token.objects.filter(
                token_type=Token.TOKEN_TYPE_CORPORATION,
                user__state=1
            ).values_list("character__corporation_id", flat=True)  # adjust if no FK to character
        ).distinct()

    else:
        qs = None

    if qs is not None:
        qsa = qs.exclude(corporation_id__in=ignored_corps.values_list("corporation_id", flat=True))
        logger.info(f"qs len: {len(qs)}, qsa {len(qsa)}, ignored {len(ignored_corps)}")
        logger.info(f"qs first corp id: {repr(qs[0].corporation_id)} type: {type(qs[0].corporation_id)}")
        logger.info(f"ignored corps: {[ (repr(corp), type(corp)) for corp in ignored_corps ]}")
        qsa = qsa.filter(
            corporationaudit__isnull=False,
            corporationaudit__last_update_assets__isnull=False,
            corporationaudit__last_update_wallet__isnull=False,
            corporationaudit__last_update_contracts__isnull=False,
        )
        dropdown_options = (
            qsa.values_list("corporation_id", "corporation_name")
              .order_by("corporation_name")
        )

    context = {
        "dropdown_options": dropdown_options,
        "CARD_DEFINITIONS": CARD_DEFINITIONS,
    }
    return render(request, "aa_cb/index.html", context)


# Bulk loader (fallback)
@login_required
@permission_required("aa_bb.basic_access_cb")
def load_cards(request: WSGIRequest) -> JsonResponse:
    corp_id = request.GET.get("option")  # now contains corporation_id
    warm_entity_cache_task.delay(corp_id)
    cards = []
    for card in CARD_DEFINITIONS:
        content, status = get_card_data(request, corp_id, card["key"])
        cards.append({
            "title":   card["title"],
            "content": content,
            "status":  status,
        })
    logger.warning("load_cards")
    return JsonResponse({"cards": cards})


def get_user_id(character_name):
    try:
        ownership = CharacterOwnership.objects.select_related('user') \
            .get(character__character_name=character_name)
        return ownership.user.id
    except CharacterOwnership.DoesNotExist:
        return None

def get_mail_keywords():
    return BigBrotherConfig.get_solo().mail_keywords


def get_card_data(request, corp_id: int, key: str):
    logger.warning("get_card_data")
    if key == "sus_asset":
        content = render_assets(corp_id)
        status  = not (content and "red" in content)

    else:
        content = "WiP"
        status  = True

    return content, status

# Single-card loader
@login_required
@permission_required("aa_bb.basic_access_cb")
def load_card(request):
    corp_id = request.GET.get("option")
    idx    = request.GET.get("index")

    if corp_id is None or idx is None:
        return HttpResponseBadRequest("Missing parameters")

    try:
        idx      = int(idx)
        card_def = CARD_DEFINITIONS[idx]
    except (ValueError, IndexError):
        return HttpResponseBadRequest("Invalid card index")

    key   = card_def["key"]
    title = card_def["title"]
    logger.info(key)
    if key in ("sus_contr","sus_tra"):
        # handled via paginated endpoints
        return JsonResponse({"key": key, "title": title})

    content, status = get_card_data(request, corp_id, key)
    return JsonResponse({
        "title":   title,
        "content": content,
        "status":  status,
    })


@shared_task(bind=True)
def warm_entity_cache_task(self, user_id):
    """
    Gather mails, contracts, transactions; warm entity cache.
    Track progress in the DB via WarmProgress.
    """
    user_main = resolve_corporation_name(user_id) or str(user_id)
    logger.info(f"corp_name: {user_main}")
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
@permission_required("aa_bb.basic_access_cb")
def warm_cache(request):
    """
    Endpoint to kick off warming for a given character name (option).
    Immediately registers a WarmProgress row so queued tasks also appear.
    """
    logger.warning(f"warm triggered")
    option  = request.GET.get("option", "")
    user_id = option
    logger.warning(f"uid2:{user_id}")
    if not user_id:
        return JsonResponse({"error": "Unknown account"}, status=400)

    # Pre-create progress record so queued jobs show up
    user_main = resolve_corporation_name(user_id) or str(user_id)
    WarmProgress.objects.get_or_create(
        user_main=user_main,
        defaults={"current": 0, "total": 0}
    )

    # Enqueue the celery task
    warm_entity_cache_task.delay(user_id)
    return JsonResponse({"started": True})


@login_required
@permission_required("aa_bb.basic_access_cb")
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





# Paginated endpoints for Suspicious Contracts
@login_required
@permission_required("aa_bb.basic_access_cb")
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
@permission_required("aa_bb.basic_access_cb")
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
@permission_required("aa_bb.basic_access_cb")
def stream_contracts_sse(request: WSGIRequest):
    option = request.GET.get("option", "")
    user_id = option
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


@login_required
@permission_required("aa_bb.basic_access_cb")
def stream_transactions_sse(request):
    """
    Stream hostile wallet‐transactions one <tr> at a time via SSE,
    hydrating first‐ and second‐party info on the fly.
    """
    option  = request.GET.get("option", "")
    user_id = option
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