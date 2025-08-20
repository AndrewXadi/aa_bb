from django.urls import path
from . import views_paps

app_name = "paps"

urlpatterns = [
    path("generate/", views_paps.index, name="index"),
    path("", views_paps.history, name="history"),
    path("generate-chart/", views_paps.generate_pap_chart, name="generate_pap_chart"),
]