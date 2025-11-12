"""App URLs"""

from django.urls import path
from aa_bb import views, views_faq

app_name = "aa_bb"

urlpatterns = [
    # Main index view
    path("", views.index, name="index"),
    path("manual/", views_faq.manual_cards, name="manual"),
    path("manual/cards/", views_faq.manual_cards, name="manual_cards"),
    path("manual/settings/", views_faq.manual_settings, name="manual_settings"),
    path(
        "manual/settings/bigbrother/",
        views_faq.manual_settings_bb,
        name="manual_settings_bb",
    ),
    path(
        "manual/settings/paps/",
        views_faq.manual_settings_paps,
        name="manual_settings_paps",
    ),
    path(
        "manual/settings/tickets/",
        views_faq.manual_settings_tickets,
        name="manual_settings_tickets",
    ),
    path("manual/modules/", views_faq.manual_modules, name="manual_modules"),
    path("manual/modules/reddit/login/", views_faq.reddit_oauth_login, name="reddit_oauth_login"),
    path(
        "manual/modules/reddit/oauth/callback/",
        views_faq.reddit_oauth_callback,
        name="reddit_oauth_callback",
    ),
    path("manual/faq/", views_faq.manual_faq, name="manual_faq"),

    # Bulk loader (not used by paginated SUS_CONTR but retained)
    path("load_cards/", views.load_cards, name="load_cards"),

    # Single card AJAX fetch (all cards except paging for SUS_CONTR)
    path("load_card/", views.load_card, name="load_card"),
    path("warm_cache/", views.warm_cache, name="warm_cache"),
    path("warm-progress/", views.get_warm_progress, name="warm_progress"),

    # Suspicious Contracts streaming fallback (if desired)
    path("stream_contracts_sse/", views.stream_contracts_sse, name="stream_contracts_sse"),
    path("stream_mails_sse/", views.stream_mails_sse, name="stream_mails_sse"),
    path("stream_transactions_sse/", views.stream_transactions_sse, name="stream_transactions_sse"),

    # Paginated Suspicious Contracts endpoints
    path("list_contract_ids/", views.list_contract_ids, name="list_contract_ids"),
    path("check_contract_batch/", views.check_contract_batch, name="check_contract_batch"),

    # Blacklist management
    path("blacklist/add/", views.add_blacklist_view, name="add_blacklist"),
]
