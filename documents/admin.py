from django.contrib import admin

from .models import Document, IndexJob, Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "rate_limit", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "tenant", "index_status", "is_deleted", "created_at")
    list_filter = ("index_status", "is_deleted", "tenant")
    search_fields = ("title", "content")
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("tenant",)

    def get_queryset(self, request):
        # Admin uses unscoped manager to see all documents across tenants
        return Document.all_objects.all()


@admin.register(IndexJob)
class IndexJobAdmin(admin.ModelAdmin):
    list_display = ("id", "action", "status", "tenant", "document", "retry_count", "created_at")
    list_filter = ("action", "status")
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("document", "tenant")
