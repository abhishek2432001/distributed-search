import hashlib
import logging
import time
import uuid

from django.core.cache import cache
from django.http import JsonResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .es_service import check_es_health, search_documents
from .exceptions import ServiceUnavailableError
from .models import Document, DocumentStatus, IndexJob
from .rate_limiter import rate_limiter
from .serializers import DocumentCreateSerializer, DocumentDetailSerializer
from .tasks import delete_document_from_index, index_document

logger = logging.getLogger(__name__)

DOCUMENT_CACHE_TTL = 300
SEARCH_CACHE_TTL = 60


def apply_rate_limit(request) -> Response | None:
    tenant = request.tenant
    allowed, remaining, retry_after = rate_limiter.check(tenant)
    request._rl_headers = rate_limiter.get_headers(tenant, allowed, remaining, retry_after)

    if not allowed:
        resp = Response(
            {"error": "Rate limit exceeded.", "code": "RATE_LIMIT_EXCEEDED", "retry_after_seconds": retry_after},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
        for k, v in request._rl_headers.items():
            resp[k] = v
        return resp

    return None


def _doc_cache_key(tenant_id, doc_id: str) -> str:
    return f"doc:{tenant_id}:{doc_id}"


def _search_cache_key(tenant_id, q: str, page: int, size: int, fuzzy: bool) -> str:
    digest = hashlib.md5(f"{q}:{page}:{size}:{fuzzy}".encode()).hexdigest()
    return f"search:{tenant_id}:{digest}"


def _add_rl_headers(response: Response, request) -> Response:
    for k, v in getattr(request, "_rl_headers", {}).items():
        response[k] = v
    return response


class DocumentCreateView(APIView):
    def post(self, request):
        rl_resp = apply_rate_limit(request)
        if rl_resp:
            return rl_resp

        serializer = DocumentCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed.", "code": "VALIDATION_ERROR", "detail": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        tenant = request.tenant

        doc = Document.all_objects.create(
            tenant_id=tenant.id,
            title=data["title"],
            content=data["content"],
            metadata=data.get("metadata", {}),
            index_status=DocumentStatus.PENDING,
        )

        job = IndexJob.objects.create(document=doc, tenant_id=tenant.id, action="index")
        index_document.delay(str(doc.id), str(job.id))

        logger.info("Document created: id=%s tenant=%s", doc.id, tenant.slug)

        resp = Response(
            {
                "id": str(doc.id),
                "tenant_id": str(doc.tenant_id),
                "title": doc.title,
                "status": doc.index_status,
                "message": "Document accepted for indexing.",
                "created_at": doc.created_at.isoformat(),
            },
            status=status.HTTP_202_ACCEPTED,
        )
        return _add_rl_headers(resp, request)


class DocumentDetailView(APIView):
    def get(self, request, doc_id: str):
        rl_resp = apply_rate_limit(request)
        if rl_resp:
            return rl_resp

        try:
            uuid.UUID(doc_id)
        except ValueError:
            return Response({"error": "Invalid document ID.", "code": "INVALID_ID"}, status=status.HTTP_400_BAD_REQUEST)

        tenant = request.tenant
        cache_key = _doc_cache_key(tenant.id, doc_id)

        cached = cache.get(cache_key)
        if cached is not None:
            resp = Response(cached, status=status.HTTP_200_OK)
            resp["X-Cache"] = "HIT"
            return _add_rl_headers(resp, request)

        try:
            doc = Document.objects.select_related("tenant").get(id=doc_id, is_deleted=False)
        except Document.DoesNotExist:
            return Response({"error": "Document not found.", "code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        data = DocumentDetailSerializer(doc).data
        cache.set(cache_key, data, timeout=DOCUMENT_CACHE_TTL)

        resp = Response(data, status=status.HTTP_200_OK)
        resp["X-Cache"] = "MISS"
        return _add_rl_headers(resp, request)

    def delete(self, request, doc_id: str):
        rl_resp = apply_rate_limit(request)
        if rl_resp:
            return rl_resp

        try:
            uuid.UUID(doc_id)
        except ValueError:
            return Response({"error": "Invalid document ID.", "code": "INVALID_ID"}, status=status.HTTP_400_BAD_REQUEST)

        tenant = request.tenant

        try:
            doc = Document.objects.get(id=doc_id, is_deleted=False)
        except Document.DoesNotExist:
            return Response({"error": "Document not found.", "code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        doc.is_deleted = True
        doc.index_status = DocumentStatus.DELETED
        doc.save(update_fields=["is_deleted", "index_status", "updated_at"])

        cache.delete(_doc_cache_key(tenant.id, doc_id))

        job = IndexJob.objects.create(document=doc, tenant_id=tenant.id, action="delete")
        delete_document_from_index.delay(doc_id, tenant.slug, str(job.id))

        logger.info("Document deleted: id=%s tenant=%s", doc_id, tenant.slug)

        resp = Response({"message": "Document deleted.", "id": doc_id}, status=status.HTTP_200_OK)
        return _add_rl_headers(resp, request)


class SearchView(APIView):
    def get(self, request):
        rl_resp = apply_rate_limit(request)
        if rl_resp:
            return rl_resp

        query = request.query_params.get("q", "").strip()
        if not query:
            return Response(
                {"error": "Query parameter 'q' is required.", "code": "MISSING_QUERY"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            page = max(1, int(request.query_params.get("page", 1)))
            size = min(100, max(1, int(request.query_params.get("size", 10))))
        except (TypeError, ValueError):
            return Response(
                {"error": "Invalid pagination parameters.", "code": "INVALID_PARAMS"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        fuzzy = request.query_params.get("fuzzy", "false").lower() == "true"
        tenant = request.tenant

        cache_key = _search_cache_key(tenant.id, query, page, size, fuzzy)
        cached = cache.get(cache_key)
        if cached is not None:
            resp = Response(cached, status=status.HTTP_200_OK)
            resp["X-Cache"] = "HIT"
            return _add_rl_headers(resp, request)

        try:
            result = search_documents(tenant_slug=tenant.slug, query=query, page=page, size=size, fuzzy=fuzzy)
        except Exception as exc:
            logger.exception("Search failed for tenant %s: %s", tenant.slug, exc)
            raise ServiceUnavailableError("Search service is temporarily unavailable.") from exc

        payload = {
            "query": query,
            "total": result["total"],
            "page": page,
            "size": size,
            "query_time_ms": result["query_time_ms"],
            "cached": False,
            "hits": result["hits"],
        }

        cache.set(cache_key, {**payload, "cached": True}, timeout=SEARCH_CACHE_TTL)

        resp = Response(payload, status=status.HTTP_200_OK)
        resp["X-Cache"] = "MISS"
        return _add_rl_headers(resp, request)


def health_check(request):
    results = {}
    healthy = True

    start = time.monotonic()
    try:
        from django.db import connection
        connection.ensure_connection()
        connection.cursor().execute("SELECT 1")
        results["postgresql"] = {"status": "up", "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as exc:
        results["postgresql"] = {"status": "down", "error": str(exc)}
        healthy = False

    start = time.monotonic()
    try:
        cache.set("health:ping", "pong", timeout=5)
        assert cache.get("health:ping") == "pong"
        results["redis"] = {"status": "up", "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as exc:
        results["redis"] = {"status": "down", "error": str(exc)}
        healthy = False

    es_health = check_es_health()
    results["elasticsearch"] = es_health
    if es_health["status"] != "up":
        healthy = False

    start = time.monotonic()
    try:
        from celery import current_app
        conn = current_app.connection_for_read()
        conn.ensure_connection(max_retries=1, timeout=3)
        conn.release()
        results["rabbitmq"] = {"status": "up", "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as exc:
        results["rabbitmq"] = {"status": "down", "error": str(exc)}
        healthy = False

    return JsonResponse(
        {
            "status": "healthy" if healthy else "degraded",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dependencies": results,
        },
        status=200 if healthy else 503,
    )
