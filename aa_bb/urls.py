"""App URLs"""

# Django
from django.urls import path

# AA BB
from aa_bb import views

app_name: str = "BigBrother"

urlpatterns = [
    path("", views.index, name="index"),
    path("load_cards/", views.load_cards, name="load_cards"),
    path("load_card/", views.load_card, name="load_card"),
    path("blacklist/add/", views.add_blacklist_view, name="add_blacklist"),
]
