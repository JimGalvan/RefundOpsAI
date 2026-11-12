"""URL configuration for RefundOps."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    path("", include("console.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
