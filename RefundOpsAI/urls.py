"""URL configuration for RefundOps."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import path
from django.views.generic import TemplateView


def health(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("", TemplateView.as_view(template_name="home.html"), name="home"),
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
]
