import uuid

from django.db import models

from .managers import TenantAwareManager


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    rate_limit = models.PositiveIntegerField(default=100, help_text="Requests per minute.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenants"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"], name="tenants_slug_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.slug})"

    @property
    def es_index_name(self) -> str:
        return f"documents-{self.slug}"


class DocumentStatus(models.TextChoices):
    PENDING = "PENDING", "Pending Indexing"
    INDEXED = "INDEXED", "Indexed"
    FAILED = "FAILED", "Indexing Failed"
    DELETED = "DELETED", "Deleted"


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="documents", db_index=True)
    title = models.CharField(max_length=1024)
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    index_status = models.CharField(
        max_length=20, choices=DocumentStatus.choices, default=DocumentStatus.PENDING, db_index=True
    )
    index_error = models.TextField(blank=True, default="")
    is_deleted = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantAwareManager()
    all_objects = models.Manager()

    class Meta:
        db_table = "documents"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "index_status"], name="documents_tenant_status_idx"),
            models.Index(fields=["tenant", "is_deleted"], name="documents_tenant_deleted_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["id", "tenant"], name="documents_id_tenant_unique"),
        ]

    def __str__(self):
        return f"[{self.tenant.slug}] {self.title[:60]}"


class IndexJobStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    SUCCESS = "SUCCESS", "Success"
    FAILED = "FAILED", "Failed"
    RETRYING = "RETRYING", "Retrying"


class IndexJob(models.Model):
    """Audit model for async indexing tasks."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="index_jobs", null=True, blank=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="index_jobs")
    celery_task_id = models.CharField(max_length=255, blank=True, db_index=True)
    action = models.CharField(
        max_length=20,
        choices=[("index", "Index"), ("delete", "Delete"), ("bulk_reindex", "Bulk Reindex")],
    )
    status = models.CharField(
        max_length=20, choices=IndexJobStatus.choices, default=IndexJobStatus.QUEUED, db_index=True
    )
    error_message = models.TextField(blank=True, default="")
    retry_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = models.Manager()

    class Meta:
        db_table = "index_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status"], name="indexjobs_tenant_status_idx"),
        ]

    def __str__(self):
        return f"IndexJob({self.action}, {self.status}, doc={self.document_id})"
