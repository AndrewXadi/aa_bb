"""App URLs"""

from django.urls import path
from aa_bb import views

app_name = "BigBrother"

urlpatterns = [
    # Main index view
    path("",                 views.index,                  name="index"),

    # Bulk loader (not used by paginated SUS_CONTR but retained)
    path("load_cards/",      views.load_cards,             name="load_cards"),

    # Single card AJAX fetch (all cards except paging for SUS_CONTR)
    path("load_card/",       views.load_card,              name="load_card"),

    # Suspicious Contracts streaming fallback (if desired)
    path('stream_contracts/',  views.stream_contracts,        name='stream_contracts'),
    path("stream_mails_sse/", views.stream_mails_sse, name="stream_mails_sse"),

    # Paginated Suspicious Contracts endpoints
    path('list_contract_ids/', views.list_contract_ids,       name='list_contract_ids'),
    path('check_contract_batch/', views.check_contract_batch, name='check_contract_batch'),

    # Blacklist management
    path("blacklist/add/",   views.add_blacklist_view,     name="add_blacklist"),
]
