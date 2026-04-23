import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Create Elasticsearch indices for all active tenants."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-slug",
            type=str,
            default=None,
            help="Create/verify index for a specific tenant slug.",
        )

    def handle(self, *args, **options):
        from documents.es_service import ensure_index_exists
        from documents.models import Tenant

        target_slug = options.get("tenant_slug")

        qs = Tenant.objects.filter(is_active=True)
        if target_slug:
            qs = qs.filter(slug=target_slug)

        if not qs.exists():
            self.stdout.write(self.style.WARNING("No active tenants found."))
            return

        for tenant in qs:
            try:
                created = ensure_index_exists(tenant.slug)
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(f"  Created ES index: {tenant.es_index_name}")
                    )
                else:
                    self.stdout.write(f"  Index already exists: {tenant.es_index_name}")
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"  Failed to create index for {tenant.slug}: {exc}")
                )
