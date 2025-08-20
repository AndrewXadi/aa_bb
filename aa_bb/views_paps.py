from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.views.decorators.http import require_POST
from django.conf import settings
from django.utils.timezone import now
from django.db.models import Count
from django.urls import reverse
from .models import BigBrotherConfig, PapsConfig
from .app_settings import get_user_profiles, get_user_characters, afat_active
from afat.models import Fat
from datetime import datetime
import os
import matplotlib.pyplot as plt
import calendar

@login_required
@permission_required("aa_bb.can_generate_paps")
def index(request):
    cfg = BigBrotherConfig.get_solo()
    if not cfg.is_paps_active:
        return render(request, "paps/disabled.html")

    today = now()
    month = int(request.GET.get("month", today.month))
    year = int(request.GET.get("year", today.year))

    users_data = []
    profiles = get_user_profiles()
    profile_dict = {p.main_character.character_name: p for p in profiles}

    bulk_data = ""
    error_messages = []

    # Handle bulk data POST
    if request.method == "POST":
        bulk_data = request.POST.get("bulk_data", "")
        lines = bulk_data.strip().split("\n")
        for line in lines:
            parts = line.strip().split()
            if len(parts) < 3:
                continue
            player_name = " ".join(parts[:-2])
            try:
                lawn = int(parts[-2])
                imp  = int(parts[-1])
            except ValueError:
                error_messages.append(player_name)
                continue

            profile = profile_dict.get(player_name)
            if not profile:
                error_messages.append(player_name)
                continue

            # Save PAPs to POST fields so table inputs get prefilled
            request.POST = request.POST.copy()
            request.POST[f"lawn_paps_{profile.user.id}"] = lawn
            request.POST[f"imperium_paps_{profile.user.id}"] = imp

    # Build table data
    for profile in profiles:
        user_id = profile.user.id
        characters = get_user_characters(user_id)
        corp_paps = 0
        lawn_paps = 0
        imperium_paps = 0

        if afat_active():
            for char in characters:
                fats = Fat.objects.filter(
                    character__character_id=char,
                    fatlink__created__month=month,
                    fatlink__created__year=year,
                )
                corp_paps += fats.count()

        # Override with POSTed values (manual or bulk)
        if request.method == "POST":
            corp_paps = int(request.POST.get(f"corp_paps_{user_id}", corp_paps))
            lawn_paps = int(request.POST.get(f"lawn_paps_{user_id}", lawn_paps))
            imperium_paps = int(request.POST.get(f"imperium_paps_{user_id}", imperium_paps))

        users_data.append({
            "user": profile,
            "corp_paps": corp_paps,
            "lawn_paps": lawn_paps,
            "imperium_paps": imperium_paps,
        })

    return render(
        request,
        "paps/index.html",
        {
            "users_data": users_data,
            "month": month,
            "year": year,
            "bulk_data": bulk_data,
            "error_messages": error_messages,
        },
    )


@login_required
@permission_required("aa_bb.can_access_paps")
def history(request):
    cfg = BigBrotherConfig.get_solo()
    if not cfg.is_paps_active:
        return render(request, "paps/disabled.html")
    month = int(request.GET.get("month", 8))  # default to August
    year = int(request.GET.get("year", 2025)) # default to 2025

    chart_relative_path = f"aa_bb/paps/pap_chart_{year}_{month}.png"
    chart_full_path = os.path.join(settings.STATIC_ROOT, chart_relative_path)
    chart_exists = os.path.isfile(chart_full_path)

    return render(request, "paps/history.html", {
        "month": month,
        "year": year,
        "chart_exists": chart_exists,
        "chart_url": chart_relative_path,
    })


@require_POST
@login_required
@permission_required("aa_bb.can_generate_paps")
def generate_pap_chart(request):
    month = int(request.POST.get("month"))
    year = int(request.POST.get("year"))

    # Gather submitted PAP values
    users_data = []
    for profile in get_user_profiles():
        user_id = profile.user.id
        conf = PapsConfig.get_solo()
        corp_paps = min(int(request.POST.get(f"corp_paps_{user_id}", 0)) * conf.corp_modifier, conf.max_corp_paps)  # cap at 4
        lawn_paps = int(request.POST.get(f"lawn_paps_{user_id}", 0)) * conf.lawn_modifier     # double lawn
        imperium_paps = int(request.POST.get(f"imperium_paps_{user_id}", 0)) * conf.imp_modifier

        users_data.append({
            "name": profile.main_character.character_name,
            "corp": corp_paps,
            "lawn": lawn_paps,
            "imperium": imperium_paps,
        })

    # Chart save path
    app_static_dir = os.path.join(settings.BASE_DIR, "aa_bb", "templates", "paps", "charts")
    os.makedirs(app_static_dir, exist_ok=True)
    filename = f"pap_chart_{year}_{month}.png"
    filepath = os.path.join(app_static_dir, filename)

    # Generate stacked chart
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor('#4B4B4B')  # Dark grey background
    ax.set_facecolor('#4B4B4B')

    names = [u["name"] for u in users_data]
    corp = [u["corp"] for u in users_data]
    lawn = [u["lawn"] for u in users_data]
    imp  = [u["imperium"] for u in users_data]

    x = range(len(names))

    # Stacked bars: Lawn (bottom), Imperium (middle), Corp (top)
    ax.bar(x, lawn, label="Lawn Paps", color="#58D68D")
    ax.bar(x, imp, bottom=lawn, label="Imperium Paps", color="#F5B041")
    bottom_stack = [l + im for l, im in zip(lawn, imp)]
    ax.bar(x, corp, bottom=bottom_stack, label="Corp Paps", color="#5DADE2")

    # Horizontal red dashed line at y=6
    ax.axhline(y=conf.required_paps, color='red', linestyle='--', linewidth=2, label='PAP Requirement')

    # Labels and style
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=45, ha="right", color='white')
    ax.set_ylabel("Total Paps", color='white')
    month_name = calendar.month_name[month]
    main_corporation = BigBrotherConfig.get_solo().main_corporation
    ax.set_title(f"{main_corporation} Fleet Breakdown for {month_name} {year}", color='white', fontweight='bold')

    # All spines and ticks in white
    ax.spines['bottom'].set_color('black')
    ax.spines['top'].set_color('black')
    ax.spines['left'].set_color('black')
    ax.spines['right'].set_color('black')
    ax.tick_params(axis='x', colors='white')
    ax.tick_params(axis='y', colors='white')

    # Legend in top-right
    ax.legend(loc='upper right', facecolor='#4B4B4B', edgecolor='white', labelcolor='white')

    # Add labels above the stacked bars
    for i, (l, im, c) in enumerate(zip(lawn, imp, corp)):
        total = l + im + c
        ax.text(i, total + 0.5, str(total), ha='center', va='bottom', color='white', fontsize=10, fontweight='bold')

    plt.tight_layout()
    plt.savefig(filepath, dpi=150, facecolor=fig.get_facecolor())  # save with background
    plt.close(fig)

    # Redirect to history page
    return redirect(f"{reverse('paps:history')}?month={month}&year={year}")