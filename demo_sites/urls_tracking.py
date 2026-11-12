from django.urls import path

from . import views

urlpatterns = [
    path("", views.tracking_home),
    path("track/<str:tracking_number>/", views.tracking_detail, name="tracking-detail"),
]
