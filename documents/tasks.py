import logging
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


@shared_task(
    bind=True,
    name="documents.tasks.index_document",
    queue="document_index",
    acks_late=True,
    max_retries=3,
    default_retry_delay=30,
)
def index_document(self, doc_id: str, job_id: str | None = None):
    from .models import Document, DocumentStatus, IndexJob, IndexJobStatus
    from .es_service import index_document_in_es

    logger.info("index_document: doc_id=%s task_id=%s", doc_id, self.request.id)

    if job_id:
        IndexJob.objects.filter(id=job_id).update(
            status=IndexJobStatus.RUNNING,
            celery_task_id=self.request.id,
        )

    try:
        doc = Document.all_objects.select_related("tenant").get(id=doc_id)

        if doc.is_deleted:
            return

        index_document_in_es(doc)

        Document.all_objects.filter(id=doc_id).update(index_status=DocumentStatus.INDEXED, index_error="")

        if job_id:
            IndexJob.objects.filter(id=job_id).update(status=IndexJobStatus.SUCCESS)

        logger.info("Indexed document: %s", doc_id)

    except Document.DoesNotExist:
        logger.error("Document not found: %s", doc_id)
        if job_id:
            IndexJob.objects.filter(id=job_id).update(
                status=IndexJobStatus.FAILED,
                error_message=f"Document {doc_id} not found.",
            )

    except Exception as exc:
        logger.exception("Failed to index document %s: %s", doc_id, exc)

        if job_id:
            IndexJob.objects.filter(id=job_id).update(
                status=IndexJobStatus.RETRYING,
                retry_count=self.request.retries + 1,
                error_message=str(exc),
            )

        Document.all_objects.filter(id=doc_id).update(
            index_status=DocumentStatus.FAILED,
            index_error=str(exc),
        )

        try:
            raise self.retry(exc=exc, countdown=30 * (3 ** self.request.retries))
        except MaxRetriesExceededError:
            logger.error("Max retries exceeded for document %s", doc_id)
            if job_id:
                IndexJob.objects.filter(id=job_id).update(
                    status=IndexJobStatus.FAILED,
                    error_message=f"Max retries exceeded. Last error: {exc}",
                )


@shared_task(
    bind=True,
    name="documents.tasks.delete_document_from_index",
    queue="document_delete",
    acks_late=True,
    max_retries=3,
    default_retry_delay=15,
)
def delete_document_from_index(self, doc_id: str, tenant_slug: str, job_id: str | None = None):
    from .models import IndexJob, IndexJobStatus
    from .es_service import delete_document_from_es

    logger.info("delete_document_from_index: doc_id=%s tenant=%s", doc_id, tenant_slug)

    if job_id:
        IndexJob.objects.filter(id=job_id).update(
            status=IndexJobStatus.RUNNING,
            celery_task_id=self.request.id,
        )

    try:
        delete_document_from_es(doc_id, tenant_slug)

        if job_id:
            IndexJob.objects.filter(id=job_id).update(status=IndexJobStatus.SUCCESS)

        logger.info("Deleted document %s from ES (%s)", doc_id, tenant_slug)

    except Exception as exc:
        logger.exception("Failed to delete document %s from ES: %s", doc_id, exc)

        if job_id:
            IndexJob.objects.filter(id=job_id).update(
                status=IndexJobStatus.RETRYING,
                retry_count=self.request.retries + 1,
                error_message=str(exc),
            )

        try:
            raise self.retry(exc=exc, countdown=15 * (2 ** self.request.retries))
        except MaxRetriesExceededError:
            logger.error("Max retries exceeded deleting document %s from ES", doc_id)
            if job_id:
                IndexJob.objects.filter(id=job_id).update(
                    status=IndexJobStatus.FAILED,
                    error_message=f"Max retries exceeded. Last error: {exc}",
                )


@shared_task(
    bind=True,
    name="documents.tasks.bulk_reindex_tenant",
    queue="document_index",
    acks_late=True,
    max_retries=1,
)
def bulk_reindex_tenant(self, tenant_id: str):
    from .models import Document, DocumentStatus, Tenant
    from .es_service import bulk_index_documents, ensure_index_exists

    logger.info("bulk_reindex_tenant: %s", tenant_id)

    try:
        tenant = Tenant.objects.get(id=tenant_id)
    except Tenant.DoesNotExist:
        logger.error("Tenant not found: %s", tenant_id)
        return

    ensure_index_exists(tenant.slug)

    qs = Document.all_objects.filter(tenant=tenant, is_deleted=False).order_by("created_at")
    total = qs.count()
    success_total, error_total, offset = 0, 0, 0

    logger.info("bulk_reindex: %d documents for tenant %s", total, tenant.slug)

    while offset < total:
        batch = list(qs[offset: offset + BATCH_SIZE])
        if not batch:
            break

        success, errors = bulk_index_documents(batch, tenant.slug)
        success_total += success
        error_total += len(errors)

        Document.all_objects.filter(id__in=[doc.id for doc in batch]).update(
            index_status=DocumentStatus.INDEXED, index_error=""
        )

        offset += BATCH_SIZE
        logger.info("bulk_reindex progress: %d/%d", min(offset, total), total)

    logger.info("bulk_reindex done: %d ok, %d errors", success_total, error_total)


@shared_task(name="documents.tasks.retry_failed_index_jobs", queue="default")
def retry_failed_index_jobs():
    from .models import IndexJob, IndexJobStatus

    failed_jobs = IndexJob.objects.filter(
        status=IndexJobStatus.FAILED,
        retry_count__lt=3,
        action="index",
        document__isnull=False,
    ).select_related("document__tenant")[:100]

    requeued = 0
    for job in failed_jobs:
        doc = job.document
        if doc and not doc.is_deleted:
            index_document.delay(str(doc.id), str(job.id))
            IndexJob.objects.filter(id=job.id).update(
                status=IndexJobStatus.QUEUED,
                retry_count=job.retry_count + 1,
            )
            requeued += 1

    logger.info("retry_failed_index_jobs: requeued %d", requeued)
    return {"requeued": requeued}
