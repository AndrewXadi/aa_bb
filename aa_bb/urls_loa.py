from django.urls import path
from . import views

app_name = "loa"  # This is the LoA namespace

urlpatterns = [
    path("", views.loa_loa, name="index"),   # This will be loa:index
    path("admin/", views.loa_admin, name="admin"),
    path("request/", views.loa_request, name="request"),
    path("delete/<int:pk>/", views.delete_request, name="delete_request"),
    path("deleteadmin/<int:pk>/", views.delete_request_admin, name="delete_request_admin"),
    path("approve/<int:pk>/", views.approve_request, name="approve_request"),
    path("deny/<int:pk>/",    views.deny_request,    name="deny_request"),
]