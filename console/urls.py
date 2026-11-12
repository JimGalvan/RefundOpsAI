from django.urls import path

from console import views

urlpatterns = [
    path("", views.queue, name="queue"),
    path("cases/sync/", views.case_sync, name="case_sync"),
    path("cases/<int:pk>/", views.case_detail, name="case_detail"),
    path("cases/<int:pk>/investigate/", views.case_investigate, name="case_investigate"),
    path("cases/<int:pk>/feed/", views.case_feed, name="case_feed"),
    path("cases/<int:pk>/frame.jpg", views.case_frame, name="case_frame"),
    path("cases/<int:pk>/decide/", views.case_decide, name="case_decide"),
    path("sources/", views.sources, name="sources"),
]
