import logging
import time
from typing import Any

from django.conf import settings
from elasticsearch import Elasticsearch, NotFoundError
from elasticsearch.helpers import bulk

logger = logging.getLogger(__name__)

_es_client: Elasticsearch | None = None


def get_es_client() -> Elasticsearch:
    global _es_client
    if _es_client is None:
        _es_client = Elasticsearch(
            hosts=[settings.ELASTICSEARCH_URL],
            request_timeout=settings.ELASTICSEARCH_TIMEOUT,
            max_retries=settings.ELASTICSEARCH_MAX_RETRIES,
            retry_on_timeout=True,
            sniff_on_start=False,
        )
    return _es_client


INDEX_SETTINGS = {
    "number_of_shards": 1,
    "number_of_replicas": 0,
    "refresh_interval": "1s",
    "analysis": {
        "analyzer": {
            "search_analyzer": {
                "type": "custom",
                "tokenizer": "standard",
                "filter": ["lowercase", "asciifolding", "stop", "snowball"],
            },
            "autocomplete_analyzer": {
                "type": "custom",
                "tokenizer": "standard",
                "filter": ["lowercase", "asciifolding", "edge_ngram_filter"],
            },
        },
        "filter": {
            "edge_ngram_filter": {"type": "edge_ngram", "min_gram": 2, "max_gram": 20},
        },
    },
}

INDEX_MAPPINGS = {
    "properties": {
        "tenant_id": {"type": "keyword"},
        "title": {
            "type": "text",
            "analyzer": "search_analyzer",
            "index_options": "offsets",
            "fields": {
                "keyword": {"type": "keyword", "ignore_above": 512},
                "autocomplete": {"type": "text", "analyzer": "autocomplete_analyzer"},
            },
        },
        "content": {"type": "text", "analyzer": "search_analyzer", "index_options": "offsets"},
        "metadata": {"type": "object", "dynamic": True},
        "created_at": {"type": "date"},
        "updated_at": {"type": "date"},
        "is_deleted": {"type": "boolean"},
    }
}


def get_index_name(tenant_slug: str) -> str:
    return f"documents-{tenant_slug}"


def ensure_index_exists(tenant_slug: str) -> bool:
    es = get_es_client()
    index_name = get_index_name(tenant_slug)
    if es.indices.exists(index=index_name):
        return False
    es.indices.create(index=index_name, body={"settings": INDEX_SETTINGS, "mappings": INDEX_MAPPINGS})
    logger.info("Created ES index: %s", index_name)
    return True


def delete_index(tenant_slug: str) -> None:
    es = get_es_client()
    index_name = get_index_name(tenant_slug)
    try:
        es.indices.delete(index=index_name)
        logger.info("Deleted ES index: %s", index_name)
    except NotFoundError:
        logger.warning("ES index not found on delete: %s", index_name)


def index_document_in_es(document) -> None:
    es = get_es_client()
    index_name = get_index_name(document.tenant.slug)
    ensure_index_exists(document.tenant.slug)

    body = {
        "tenant_id": str(document.tenant_id),
        "title": document.title,
        "content": document.content,
        "metadata": document.metadata,
        "created_at": document.created_at.isoformat(),
        "updated_at": document.updated_at.isoformat(),
        "is_deleted": document.is_deleted,
    }

    es.index(index=index_name, id=str(document.id), document=body, refresh="wait_for")
    logger.info("Indexed document %s into %s", document.id, index_name)


def delete_document_from_es(doc_id: str, tenant_slug: str) -> None:
    es = get_es_client()
    index_name = get_index_name(tenant_slug)
    try:
        es.delete(index=index_name, id=doc_id, refresh="wait_for")
        logger.info("Deleted document %s from %s", doc_id, index_name)
    except NotFoundError:
        logger.warning("Document %s not found in ES on delete", doc_id)


def bulk_index_documents(documents: list, tenant_slug: str) -> tuple[int, list]:
    es = get_es_client()
    index_name = get_index_name(tenant_slug)
    ensure_index_exists(tenant_slug)

    actions = [
        {
            "_index": index_name,
            "_id": str(doc.id),
            "_source": {
                "tenant_id": str(doc.tenant_id),
                "title": doc.title,
                "content": doc.content,
                "metadata": doc.metadata,
                "created_at": doc.created_at.isoformat(),
                "updated_at": doc.updated_at.isoformat(),
                "is_deleted": doc.is_deleted,
            },
        }
        for doc in documents
    ]

    success, errors = bulk(es, actions, stats_only=False, raise_on_error=False)
    logger.info("Bulk indexed %d/%d into %s", success, len(documents), index_name)
    return success, errors


def search_documents(
    tenant_slug: str,
    query: str,
    page: int = 1,
    size: int = 10,
    fuzzy: bool = False,
) -> dict[str, Any]:
    es = get_es_client()
    index_name = get_index_name(tenant_slug)
    from_offset = (page - 1) * size

    if fuzzy:
        text_query = {
            "multi_match": {
                "query": query,
                "fields": ["title^3", "content", "metadata.*"],
                "type": "best_fields",
                "fuzziness": "AUTO",
                "prefix_length": 2,
            }
        }
    else:
        text_query = {
            "multi_match": {
                "query": query,
                "fields": ["title^3", "content", "metadata.*"],
                "type": "best_fields",
            }
        }

    es_query = {
        "query": {
            "bool": {
                "must": [text_query],
                "filter": [
                    {"term": {"is_deleted": False}},
                ],
            }
        },
        "highlight": {
            "fields": {
                "title": {"number_of_fragments": 0},
                "content": {"fragment_size": 200, "number_of_fragments": 3},
            },
            "pre_tags": ["<em>"],
            "post_tags": ["</em>"],
        },
        "from": from_offset,
        "size": size,
        "_source": ["title", "metadata", "created_at"],
    }

    start = time.monotonic()
    response = es.search(index=index_name, body=es_query)
    elapsed_ms = int((time.monotonic() - start) * 1000)

    hits = []
    for hit in response["hits"]["hits"]:
        source = hit["_source"]
        highlight = hit.get("highlight", {})
        title_highlight = highlight.get("title", [source.get("title", "")])[0]
        snippet = " ... ".join(highlight.get("content", []))

        hits.append({
            "id": hit["_id"],
            "title": title_highlight,
            "snippet": snippet,
            "score": round(hit["_score"], 4),
            "metadata": source.get("metadata", {}),
            "highlights": {
                "title": highlight.get("title", []),
                "content": highlight.get("content", []),
            },
        })

    return {
        "total": response["hits"]["total"]["value"],
        "hits": hits,
        "query_time_ms": elapsed_ms,
    }


def check_es_health() -> dict[str, Any]:
    es = get_es_client()
    start = time.monotonic()
    try:
        info = es.cluster.health(timeout="5s")
        return {"status": "up", "cluster_status": info["status"], "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as exc:
        return {"status": "down", "error": str(exc)}
