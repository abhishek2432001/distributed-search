from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from documents.views import health_check

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("documents.urls")),
    # Health check (no tenant header required)
    path("health", health_check, name="health-check"),
    # OpenAPI schema & Swagger UI
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
