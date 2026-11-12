from django.urls import path

from . import views

urlpatterns = [
    path("", views.tool_home, {"site": "portal"}),
    path("login/", views.login, {"site": "portal"}),
    path("logout/", views.logout),
    path("orders/", views.orders),
    path("orders/<str:order_ref>/", views.order_detail),
]
