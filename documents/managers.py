from django.db import models


class TenantAwareQuerySet(models.QuerySet):
    def for_tenant(self, tenant):
        return self.filter(tenant=tenant)

    def active(self):
        return self.filter(is_deleted=False)

    def pending_index(self):
        return self.active().filter(index_status="PENDING")

    def indexed(self):
        return self.active().filter(index_status="INDEXED")


class TenantAwareManager(models.Manager):
    """
    Default manager for Document. Auto-filters by the thread-local tenant set
    by TenantMiddleware. Falls back to unscoped in non-request contexts
    (Celery, shell) — use Document.all_objects there instead.
    """

    def get_queryset(self) -> TenantAwareQuerySet:
        from .tenant_context import get_current_tenant

        qs = TenantAwareQuerySet(self.model, using=self._db)
        tenant = get_current_tenant()
        if tenant is not None:
            return qs.filter(tenant=tenant)
        return qs

    def get_by_natural_key(self, pk):
        from .tenant_context import get_current_tenant

        tenant = get_current_tenant()
        if tenant is None:
            raise ValueError("No tenant context.")
        return self.get(pk=pk, tenant=tenant)
