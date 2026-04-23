# Distributed Document Search Service

A production-prototype of a distributed document search service demonstrating enterprise-grade
architectural patterns: multi-tenancy, fault tolerance, async indexing, and horizontal scalability.

---

## Architecture at a Glance

```
Client → Nginx → Django API ─┬─► PostgreSQL  (source of truth)
                              ├─► Redis       (cache + rate limiting)
                              └─► RabbitMQ ──► Celery Worker ──► Elasticsearch
```

| Component | Technology | Role |
|-----------|-----------|------|
| API | Django 5 + DRF | REST endpoints, tenant middleware |
| Source of Truth | PostgreSQL 16 | Canonical document store, ACID writes |
| Search Engine | Elasticsearch 8 | Full-text search, BM25 relevance |
| Cache + Rate Limiter | Redis 7 | Search/doc cache, sliding-window rate limiter |
| Task Queue | RabbitMQ + Celery | Async ES indexing, retry pipeline |
| Task Monitor | Flower | Celery task visibility (dev) |

Full design rationale → [`docs/architecture.md`](docs/architecture.md)

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) ≥ 4.x
- [Docker Compose](https://docs.docker.com/compose/) ≥ v2 (included with Docker Desktop)
- 4 GB RAM allocated to Docker (Elasticsearch requires at least 1.5 GB)

---

## Quick Start

### 1. Clone & configure

```bash
git clone <your-repo-url>
cd distributed-search

# The .env file ships with safe development defaults — no changes needed for local dev
cp .env .env.local   # optional: override values for your environment
```

### 2. Start all services

```bash
docker compose up --build -d
```

The startup sequence is:
1. PostgreSQL, Redis, RabbitMQ, Elasticsearch boot first (health-checked)
2. Django web container waits for all four to be healthy, then runs `migrate` and `create_es_indices`
3. Celery worker + beat start after the web container is healthy

Check everything is up:
```bash
docker compose ps
```

Expected output — all services should show `running` or `healthy`:
```
NAME                    STATUS
search_postgres         running (healthy)
search_elasticsearch    running (healthy)
search_redis            running (healthy)
search_rabbitmq         running (healthy)
search_web              running
search_celery_worker    running
search_celery_beat      running
search_flower           running
```

### 3. Verify the health endpoint

```bash
curl http://localhost:8000/health | python3 -m json.tool
```

Expected:
```json
{
  "status": "healthy",
  "dependencies": {
    "postgresql":     { "status": "up", "latency_ms": 2 },
    "elasticsearch":  { "status": "up", "latency_ms": 8 },
    "redis":          { "status": "up", "latency_ms": 1 },
    "rabbitmq":       { "status": "up", "latency_ms": 3 }
  }
}
```

---

## Create Your First Tenant

The system requires a tenant before any API calls (except `/health`).
Create one via the Django shell:

```bash
docker compose exec web python manage.py shell -c "
from documents.models import Tenant
t = Tenant.objects.create(name='Acme Corp', slug='acme', rate_limit=200)
print('Tenant ID:', t.id)
print('Tenant slug:', t.slug)
"
```

**Save the Tenant ID** — you'll use it as the `X-Tenant-ID` header.

---

## API Reference & Sample Requests

Replace `<TENANT_UUID>` with the UUID printed above in every request.

### POST `/api/v1/documents` — Index a document

```bash
curl -s -X POST http://localhost:8000/api/v1/documents \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: <TENANT_UUID>" \
  -d '{
    "title": "Introduction to Machine Learning",
    "content": "Machine learning is a subset of artificial intelligence that enables systems to learn from data and make decisions with minimal human intervention.",
    "metadata": {
      "author": "Jane Doe",
      "tags": ["ml", "ai", "data-science"],
      "source": "internal-wiki"
    }
  }' | python3 -m json.tool
```

**Response (202 Accepted):**
```json
{
  "id": "d8f3a1b2-...",
  "tenant_id": "<TENANT_UUID>",
  "title": "Introduction to Machine Learning",
  "status": "PENDING",
  "message": "Document accepted for indexing.",
  "created_at": "2026-04-24T00:00:00Z"
}
```

> The document is written to PostgreSQL immediately. Elasticsearch indexing happens
> asynchronously within ~1–3 seconds via Celery.

---

### GET `/api/v1/search` — Full-text search

Wait ~3 seconds after indexing, then search:

```bash
# Basic search
curl -s "http://localhost:8000/api/v1/search?q=machine+learning" \
  -H "X-Tenant-ID: <TENANT_UUID>" | python3 -m json.tool

# With pagination
curl -s "http://localhost:8000/api/v1/search?q=machine+learning&page=1&size=5" \
  -H "X-Tenant-ID: <TENANT_UUID>" | python3 -m json.tool

# With fuzzy matching (handles typos)
curl -s "http://localhost:8000/api/v1/search?q=machne+lerning&fuzzy=true" \
  -H "X-Tenant-ID: <TENANT_UUID>" | python3 -m json.tool
```

**Response (200 OK):**
```json
{
  "query": "machine learning",
  "total": 1,
  "page": 1,
  "size": 10,
  "query_time_ms": 47,
  "cached": false,
  "hits": [
    {
      "id": "d8f3a1b2-...",
      "title": "Introduction to <em>Machine Learning</em>",
      "snippet": "...<em>Machine learning</em> is a subset of artificial intelligence...",
      "score": 9.87,
      "metadata": { "author": "Jane Doe", "tags": ["ml", "ai"] },
      "highlights": {
        "title": ["Introduction to <em>Machine Learning</em>"],
        "content": ["<em>Machine learning</em> is a subset..."]
      }
    }
  ]
}
```

> **Cache behaviour:** Run the same query twice — the second response will show
> `"cached": true` and have `X-Cache: HIT` in the response headers.

---

### GET `/api/v1/documents/{id}` — Retrieve document detail

```bash
curl -s "http://localhost:8000/api/v1/documents/<DOC_UUID>" \
  -H "X-Tenant-ID: <TENANT_UUID>" | python3 -m json.tool
```

Check Redis caching with headers:
```bash
curl -sv "http://localhost:8000/api/v1/documents/<DOC_UUID>" \
  -H "X-Tenant-ID: <TENANT_UUID>" 2>&1 | grep "X-Cache"
# First call:  X-Cache: MISS
# Second call: X-Cache: HIT
```

---

### DELETE `/api/v1/documents/{id}` — Remove a document

```bash
curl -s -X DELETE "http://localhost:8000/api/v1/documents/<DOC_UUID>" \
  -H "X-Tenant-ID: <TENANT_UUID>" | python3 -m json.tool
```

**Response (200 OK):**
```json
{
  "message": "Document deleted.",
  "id": "<DOC_UUID>"
}
```

> Soft-deletes the Postgres record instantly. Celery worker removes it from
> Elasticsearch asynchronously within seconds.

---

### Rate Limiting

Every response includes rate-limit headers:
```
X-RateLimit-Limit:     200
X-RateLimit-Remaining: 198
```

When exceeded, you'll receive `429 Too Many Requests`:
```json
{
  "error": "Rate limit exceeded. Please slow down.",
  "code": "RATE_LIMIT_EXCEEDED",
  "retry_after_seconds": 42
}
```

---

## Bulk Load (for testing search at scale)

Use this shell script to seed 100 documents:

```bash
TENANT_UUID="<YOUR_TENANT_UUID>"

for i in $(seq 1 100); do
  curl -s -X POST http://localhost:8000/api/v1/documents \
    -H "Content-Type: application/json" \
    -H "X-Tenant-ID: $TENANT_UUID" \
    -d "{
      \"title\": \"Document $i: $(shuf -n3 /usr/share/dict/words 2>/dev/null | tr '\n' ' ' || echo 'sample title')\",
      \"content\": \"This is the content of document number $i covering topics in technology, engineering, and architecture.\",
      \"metadata\": {\"index\": $i, \"batch\": \"seed\"}
    }" > /dev/null
  echo "Indexed document $i"
done

echo "Done. Waiting for ES indexing..."
sleep 5

curl -s "http://localhost:8000/api/v1/search?q=technology&size=5" \
  -H "X-Tenant-ID: $TENANT_UUID" | python3 -m json.tool
```

---

## Monitoring UIs

| UI | URL | Credentials |
|----|-----|-------------|
| Flower (Celery) | http://localhost:5555 | none (dev) |
| RabbitMQ Management | http://localhost:15672 | guest / guest |
| Elasticsearch | http://localhost:9200/_cluster/health | none (dev) |
| Django Admin | http://localhost:8000/admin | create via `createsuperuser` |
| Swagger / OpenAPI | http://localhost:8000/api/docs/ | none |

Create a Django superuser:
```bash
docker compose exec web python manage.py createsuperuser
```

---

## Multi-Tenant Isolation Demo

Demonstrate that tenant A cannot see tenant B's documents:

```bash
# Create a second tenant
docker compose exec web python manage.py shell -c "
from documents.models import Tenant
t = Tenant.objects.create(name='Beta Corp', slug='beta', rate_limit=50)
print('Tenant B ID:', t.id)
"

# Index a document under Tenant B
curl -s -X POST http://localhost:8000/api/v1/documents \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: <TENANT_B_UUID>" \
  -d '{"title": "Beta Corp Secret Document", "content": "Confidential beta content."}'

# Search as Tenant A — should return 0 results for "beta"
curl -s "http://localhost:8000/api/v1/search?q=beta+secret" \
  -H "X-Tenant-ID: <TENANT_A_UUID>" | python3 -m json.tool
# Expected: "total": 0
```

---

## Stopping & Cleanup

```bash
# Stop all containers (preserves volumes / data)
docker compose down

# Stop and remove all data (full reset)
docker compose down -v
```

---

## Project Structure

```
distributed-search/
├── docker-compose.yml              # All 7 services
├── Dockerfile                      # Multi-stage (dev + prod)
├── requirements.txt
├── .env                            # Dev environment variables
├── manage.py
├── docs/
│   ├── architecture.md             # Deliverable 1: Architecture Design
│   ├── production_readiness.md     # Deliverable 3: Production Readiness
│   └── experience_showcase.md      # Deliverable 4: Enterprise Experience
├── search_service/
│   ├── settings.py                 # Env-driven configuration
│   ├── celery.py                   # Celery application
│   ├── urls.py                     # Root URL configuration
│   └── management/commands/
│       └── wait_for_db.py          # Docker startup sequencing
└── documents/
    ├── models.py                   # Tenant, Document, IndexJob
    ├── managers.py                 # TenantAwareQuerySet (auto-isolation)
    ├── tenant_context.py           # Thread-local tenant store
    ├── middleware.py               # TenantMiddleware (Redis-cached resolution)
    ├── es_service.py               # Elasticsearch client + search service
    ├── tasks.py                    # Celery tasks (index, delete, bulk, retry)
    ├── rate_limiter.py             # Sliding-window counter rate limiter
    ├── views.py                    # All API endpoints
    ├── serializers.py              # DRF serializers
    ├── exceptions.py               # Custom exception handler
    ├── urls.py                     # API URL routing
    └── migrations/
        └── 0001_initial.py
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| PostgreSQL as source of truth | ACID guarantees; ES is a derived view that can be rebuilt |
| Per-tenant ES indices | Physical data isolation; independent scaling per tenant |
| Async indexing via Celery | Write path decoupled from ES latency; 202 response in <50ms |
| Thread-local tenant context | Enforces isolation at the ORM layer without passing tenant everywhere |
| Sliding-window rate limiter | 2 Redis ops per request; no sorted sets; fair and accurate |
| Exponential backoff on index failure | Prevents thundering herd on ES recovery; DLQ via IndexJob audit |
| Redis-cached tenant resolution | Avoids per-request DB hit for tenant config lookup |

Full trade-off analysis → [`docs/architecture.md`](docs/architecture.md)

---

## Running Tests

```bash
# Run the Django test suite
docker compose exec web python manage.py test documents --verbosity=2
```

*(Unit tests are a Phase 5 extension — integration tests via curl above serve as functional verification for this prototype.)*

---

## AI Tool Usage

- **GitHub Copilot**: Boilerplate acceleration for serializer fields, migration and repetitive patterns.
- **Claude (Anthropic)**: Used to validate architectural trade-off reasoning and review the multi-tenancy isolation strategy for correctness.
- All design decisions, system architecture, code structure, and production analysis are original work.
