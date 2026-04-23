"""
Initial migration: creates tenants, documents, and index_jobs tables.

Notes:
  - UUIDs as PKs (no sequential enumeration).
  - documents.tenant FK + compound indexes enforce isolation at the DB level.
  - index_jobs provides an audit trail independent of Celery's result backend.
"""

import django.db.models.deletion
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Tenant",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255, unique=True)),
                ("slug", models.SlugField(max_length=100, unique=True, help_text="URL-safe identifier; also used as the Elasticsearch index name suffix.")),
                ("is_active", models.BooleanField(default=True)),
                ("rate_limit", models.PositiveIntegerField(default=100, help_text="Max API requests per minute for this tenant.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "tenants", "ordering": ["name"]},
        ),
        migrations.AddIndex(
            model_name="Tenant",
            index=models.Index(fields=["slug"], name="tenants_slug_idx"),
        ),

        # ─── documents ───────────────────────────────────────────────────────
        migrations.CreateModel(
            name="Document",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tenant", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="documents",
                    to="documents.tenant",
                    db_index=True,
                )),
                ("title", models.CharField(max_length=1024)),
                ("content", models.TextField()),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("index_status", models.CharField(
                    choices=[
                        ("PENDING", "Pending Indexing"),
                        ("INDEXED", "Indexed"),
                        ("FAILED", "Indexing Failed"),
                        ("DELETED", "Deleted"),
                    ],
                    default="PENDING",
                    max_length=20,
                    db_index=True,
                )),
                ("index_error", models.TextField(blank=True, default="")),
                ("is_deleted", models.BooleanField(default=False, db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "documents", "ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="Document",
            index=models.Index(fields=["tenant", "index_status"], name="documents_tenant_status_idx"),
        ),
        migrations.AddIndex(
            model_name="Document",
            index=models.Index(fields=["tenant", "is_deleted"], name="documents_tenant_deleted_idx"),
        ),
        migrations.AddConstraint(
            model_name="Document",
            constraint=models.UniqueConstraint(fields=["id", "tenant"], name="documents_id_tenant_unique"),
        ),

        migrations.CreateModel(
            name="IndexJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("document", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="index_jobs",
                    to="documents.document",
                )),
                ("tenant", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="index_jobs",
                    to="documents.tenant",
                )),
                ("celery_task_id", models.CharField(blank=True, max_length=255, db_index=True)),
                ("action", models.CharField(
                    choices=[
                        ("index", "Index"),
                        ("delete", "Delete"),
                        ("bulk_reindex", "Bulk Reindex"),
                    ],
                    max_length=20,
                )),
                ("status", models.CharField(
                    choices=[
                        ("QUEUED", "Queued"),
                        ("RUNNING", "Running"),
                        ("SUCCESS", "Success"),
                        ("FAILED", "Failed"),
                        ("RETRYING", "Retrying"),
                    ],
                    default="QUEUED",
                    max_length=20,
                    db_index=True,
                )),
                ("error_message", models.TextField(blank=True, default="")),
                ("retry_count", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "index_jobs", "ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="IndexJob",
            index=models.Index(fields=["tenant", "status"], name="indexjobs_tenant_status_idx"),
        ),
    ]
