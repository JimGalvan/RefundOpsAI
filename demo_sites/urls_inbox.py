from django.urls import path

from . import views

urlpatterns = [
    path("", views.tool_home, {"site": "inbox"}),
    path("login/", views.login, {"site": "inbox"}),
    path("logout/", views.logout),
    path("inbox/", views.inbox),
    path("inbox/<str:message_id>/", views.message_detail),
]
