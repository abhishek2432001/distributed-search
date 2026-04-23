import logging
import uuid

from django.core.cache import cache
from django.http import JsonResponse

from .tenant_context import clear_current_tenant, set_current_tenant

logger = logging.getLogger(__name__)

_EXEMPT_PREFIXES = ("/health", "/api/schema/", "/api/docs/", "/admin/")
_TENANT_CACHE_TTL = 600


def _cache_key(tenant_id: str) -> str:
    return f"tenant:{tenant_id}:config"


def invalidate_tenant_cache(tenant_id: str) -> None:
    cache.delete(_cache_key(str(tenant_id)))


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if any(request.path.startswith(p) for p in _EXEMPT_PREFIXES):
            request.tenant = None
            return self.get_response(request)

        tenant_id = request.headers.get("X-Tenant-ID", "").strip()

        if not tenant_id:
            return JsonResponse(
                {"error": "Missing required header: X-Tenant-ID", "code": "TENANT_HEADER_MISSING"},
                status=400,
            )

        try:
            uuid.UUID(tenant_id)
        except ValueError:
            return JsonResponse(
                {"error": "Invalid X-Tenant-ID format. Must be a valid UUID.", "code": "TENANT_INVALID_FORMAT"},
                status=400,
            )

        tenant = self._resolve_tenant(tenant_id)
        if tenant is None:
            return JsonResponse(
                {"error": f"Tenant '{tenant_id}' not found.", "code": "TENANT_NOT_FOUND"},
                status=404,
            )

        if not tenant.is_active:
            return JsonResponse(
                {"error": "This tenant account is inactive.", "code": "TENANT_INACTIVE"},
                status=403,
            )

        set_current_tenant(tenant)
        request.tenant = tenant

        try:
            response = self.get_response(request)
        finally:
            clear_current_tenant()

        return response

    def _resolve_tenant(self, tenant_id: str):
        cached = cache.get(_cache_key(tenant_id))
        if cached is not None:
            return self._hydrate_tenant(cached)

        from .models import Tenant

        try:
            tenant = Tenant.objects.get(id=tenant_id)
        except Tenant.DoesNotExist:
            logger.warning("Tenant not found: %s", tenant_id)
            return None

        tenant_dict = {
            "id": str(tenant.id),
            "name": tenant.name,
            "slug": tenant.slug,
            "is_active": tenant.is_active,
            "rate_limit": tenant.rate_limit,
        }
        cache.set(_cache_key(tenant_id), tenant_dict, timeout=_TENANT_CACHE_TTL)
        return tenant

    @staticmethod
    def _hydrate_tenant(data: dict):
        from .models import Tenant

        tenant = Tenant.__new__(Tenant)
        tenant.__dict__.update(data)
        return tenant
