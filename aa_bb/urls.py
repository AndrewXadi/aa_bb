"""App URLs"""

# Django
from django.urls import path

# AA Example App
from aa_bb import views
from .views import load_cards

app_name: str = "BigBrother"

urlpatterns = [
    path("", views.index, name="index"),
    path("load_cards/", views.load_cards, name="load_cards"),
    path("load_card/", views.load_card, name="load_card"),
]
