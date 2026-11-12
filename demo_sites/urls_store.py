from django.urls import path

from . import views

urlpatterns = [
    path("", views.store),
    path("policy/", views.store, {"section": "policy"}),
    path("contact/", views.store, {"section": "contact"}),
]
