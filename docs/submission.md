# Distributed Document Search Service
### Technical Submission

---

## Table of Contents

1. [Architecture Design](#1-architecture-design)
2. [Production Readiness Analysis](#2-production-readiness-analysis)
3. [Enterprise Experience Showcase](#3-enterprise-experience-showcase)
4. [AI Tool Usage](#4-ai-tool-usage)

---

# 1. Architecture Design

## 1.1 High-Level System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Client Layer                                    │
│                    (Web Apps, Mobile Clients, API Consumers)                 │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ HTTPS
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           API Gateway / Load Balancer                        │
│                     (Nginx / AWS ALB — Rate Limit Headers)                   │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
         ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
         │  Django App  │  │  Django App  │  │  Django App  │  (Horizontal replicas)
         │  Instance 1  │  │  Instance 2  │  │  Instance N  │
         └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
                │                 │                 │
        ┌───────┴─────────────────┴─────────────────┴──────────┐
        │                   Internal Services                    │
        │                                                        │
        │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │
        │  │  PostgreSQL  │  │    Redis      │  │ RabbitMQ  │  │
        │  │  (Primary +  │  │  (Cache +     │  │ (Message  │  │
        │  │   Replicas)  │  │  Rate Limit)  │  │  Broker)  │  │
        │  └──────────────┘  └──────────────┘  └─────┬──────┘  │
        │                                             │         │
        │                                   ┌─────────┴──────┐  │
        │                                   │  Celery Workers │  │
        │                                   │  (Async Index)  │  │
        │                                   └─────────┬──────┘  │
        │                                             │         │
        │                              ┌──────────────▼──────┐  │
        │                              │   Elasticsearch      │  │
        │                              │   (3-node cluster)   │  │
        │                              └─────────────────────┘  │
        └────────────────────────────────────────────────────────┘
```

---

## 1.2 Data Flow Diagrams

### Write Path (Document Indexing)

```
Client
  │
  │  POST /documents  { title, content, metadata }
  │  Header: X-Tenant-ID: <tenant_uuid>
  ▼
Django API Layer
  │
  ├─► [1] Validate request & authenticate tenant
  │
  ├─► [2] Write document to PostgreSQL (source of truth)
  │        documents table  →  status: PENDING_INDEX
  │
  ├─► [3] Publish event to RabbitMQ
  │        Exchange: document.events
  │        Routing Key: document.index
  │        Payload: { doc_id, tenant_id, action: "create" }
  │
  └─► [4] Return 202 Accepted to client immediately
            { id, status: "queued" }

                    │  (async, decoupled)
                    ▼
            Celery Worker
              │
              ├─► [5] Consume message from RabbitMQ
              │
              ├─► [6] Fetch full document from PostgreSQL
              │
              ├─► [7] Index document into Elasticsearch
              │        Index: documents-{tenant_slug}
              │
              └─► [8] Update PostgreSQL status: INDEXED
                        Invalidate Redis cache key for doc
```

### Read Path (Search)

```
Client
  │
  │  GET /search?q=machine+learning
  │  Header: X-Tenant-ID: <tenant_uuid>
  ▼
Django API Layer
  │
  ├─► [1] Extract & validate tenant from header
  │
  ├─► [2] Check Redis rate limit counter
  │        Algorithm: Sliding Window Counter
  │        → 429 if limit exceeded
  │
  ├─► [3] Build cache key: search:{tenant_id}:hash(q+filters)
  │
  ├─► [4] Redis Cache HIT?
  │         YES → Return cached results (TTL: 60s)
  │         NO  → Continue to step 5
  │
  ├─► [5] Execute Elasticsearch query
  │        • Multi-match on title (3×), content, metadata
  │        • Mandatory term filter: tenant_id            ← Isolation
  │        • Highlighting enabled, BM25 scoring
  │
  ├─► [6] Store results in Redis (TTL: 60s)
  │
  └─► [7] Return results to client
           { hits, total, query_time_ms }
```

---

## 1.3 Database & Storage Strategy

### PostgreSQL — Source of Truth
- ACID compliance ensures no document is lost during partial failures.
- Acts as the authoritative record; Elasticsearch is a derived, eventually-consistent view.
- Supports complex relational queries (tenant management, audit logs, permissions).
- Row-Level Security (RLS) can enforce tenant isolation at the DB engine level as a backstop.

**Schema:** `tenants`, `documents`, `index_jobs` (Celery task audit log)

### Elasticsearch — Search Engine
- Purpose-built for full-text search with BM25 relevance ranking.
- Sub-100ms query times on millions of documents with proper index sharding.
- Native support for highlighting, fuzzy matching, and faceted search.
- Per-tenant index (`documents-{tenant_slug}`) for physical data isolation and independent scaling.

**Index strategy:** One index per tenant with ILM for hot/warm/cold archival tiers.

### Redis — Cache & Rate Limiter

| Layer | Key Pattern | TTL | Purpose |
|-------|-------------|-----|---------|
| Search results | `search:{tenant}:{query_hash}` | 60s | Avoid ES round-trips for hot queries |
| Document detail | `doc:{tenant}:{doc_id}` | 300s | Avoid PG reads for frequent doc fetches |
| Tenant config | `tenant:{tenant_id}:config` | 600s | Avoid PG reads for tenant metadata |

### RabbitMQ — Message Broker
- Durable queues ensure no indexing job is lost if a worker crashes.
- Dead-letter queues handle failed indexing with automatic retry.
- Topic exchanges allow fine-grained routing (index vs. delete vs. bulk-reindex).
- Message acknowledgement guarantees at-least-once delivery.

---

## 1.4 API Design

**Base URL:** `/api/v1/` | **Required Header:** `X-Tenant-ID: <uuid>`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/documents` | Index a new document (async, 202) |
| `GET` | `/search` | Full-text search with filters and highlights |
| `GET` | `/documents/{id}` | Retrieve document detail (Redis-cached) |
| `DELETE` | `/documents/{id}` | Soft-delete + async ES removal |
| `GET` | `/health` | Health check with dependency status |

**POST /documents — Request / Response**
```json
// Request
{
  "title": "Introduction to Machine Learning",
  "content": "Machine learning is a subset of artificial intelligence...",
  "metadata": { "author": "Jane Doe", "tags": ["ml", "ai"] }
}

// Response 202
{
  "id": "d8f3a1b2-...",
  "tenant_id": "acme-corp-uuid",
  "status": "PENDING",
  "message": "Document accepted for indexing."
}
```

**GET /search?q=machine+learning — Response**
```json
{
  "query": "machine learning",
  "total": 1543,
  "page": 1,
  "size": 10,
  "query_time_ms": 47,
  "cached": false,
  "hits": [
    {
      "id": "d8f3a1b2-...",
      "title": "Introduction to <em>Machine Learning</em>",
      "snippet": "...a subset of <em>machine learning</em> techniques...",
      "score": 9.87,
      "metadata": { "author": "Jane Doe", "tags": ["ml", "ai"] }
    }
  ]
}
```

**GET /health — Response**
```json
{
  "status": "healthy",
  "timestamp": "2026-04-24T00:00:00Z",
  "dependencies": {
    "postgresql":    { "status": "up", "latency_ms": 2 },
    "elasticsearch": { "status": "up", "latency_ms": 8 },
    "redis":         { "status": "up", "latency_ms": 1 },
    "rabbitmq":      { "status": "up", "latency_ms": 3 }
  }
}
```

---

## 1.5 Consistency Model & Trade-offs

| Dimension | Decision | Trade-off |
|-----------|----------|-----------|
| Write consistency | PG written synchronously; ES eventually consistent via Celery | Documents searchable ~1–3s after creation |
| Read consistency | Search from ES; detail from PG | Detail is always fresh; search may lag |
| Cache invalidation | Delete-on-write for doc cache; TTL for search cache | Stale search results possible up to 60s after update |
| Indexing failure | DLQ captures failures; exponential backoff retry | Max 3 retries; after that, manual reindex needed |

---

## 1.6 Multi-Tenancy — Defence-in-Depth

Tenant isolation is enforced at three independent layers so that a bypass at one layer is caught by the next:

1. **API layer**: `TenantMiddleware` resolves `X-Tenant-ID` header and injects `request.tenant`.
2. **ORM layer**: `TenantAwareManager` auto-injects `WHERE tenant_id = ?` on every query.
3. **ES layer**: Every Elasticsearch query includes a mandatory `term` filter on `tenant_id`, injected in the service layer — not left to the caller.
4. **Storage layer**: Per-tenant ES indices (`documents-{tenant_slug}`) physically separate data.
5. **Rate limiting**: Per-tenant counters in Redis; limits configurable per tenant record.

---

# 2. Production Readiness Analysis

> What is required to evolve this prototype into a production-grade service capable of
> handling 10M+ documents, 1,000 RPS, and 99.95% availability.

---

## 2.1 Scalability

### Current State (Prototype)
- Single Django container, single Celery worker, single-node Elasticsearch.
- No horizontal auto-scaling; no read replicas.

### Path to Production Scale

**Compute Layer**

| Component | Scale Strategy |
|-----------|---------------|
| Django API | Stateless WSGI → Kubernetes HPA on CPU/RPS. Target: 50–200 pods. |
| Celery Workers | Separate Deployment per queue. HPA on RabbitMQ queue depth via KEDA. |
| Celery Beat | Single-replica with DB-based leader election (`django-celery-beat`). |

**PostgreSQL**
- Primary with synchronous streaming replication to 1 hot standby. Patroni for failover.
- 2–3 read replicas behind PgBouncer for SELECT-heavy paths.
- Partition `documents` table by `tenant_id` hash (16 partitions) to cap B-tree index size.
- PgBouncer in transaction mode: 100 pods × 4 threads → 20 actual Postgres connections.

**Elasticsearch**
- 3 dedicated master nodes + 6 data nodes, scalable to 30+.
- ILM tiers: Hot (90 days, replicas=1) → Warm (force-merged, replicas=0) → Cold/Frozen (S3 searchable snapshots).
- Target 30–50 GB per shard. ILM rollover when shard exceeds 40 GB.
- Use `routing=tenant_id` on all index/search requests to eliminate scatter-gather fan-out.

**Redis**
- Redis Cluster (3 primary + 3 replica). Separate instances for cache and Celery result backend.

**RabbitMQ**
- 3-node cluster with quorum queues. Dead-letter exchange with 7-day TTL.

---

## 2.2 Resilience

### Circuit Breakers

```
┌─────────────┐      ┌─────────────────────┐      ┌───────────────┐
│  Django API │ ───► │   Circuit Breaker   │ ───► │ Elasticsearch │
│             │      │  CLOSED / OPEN /    │      │               │
│             │ ◄─── │  HALF-OPEN states   │ ◄─── │               │
└─────────────┘      └─────────────────────┘      └───────────────┘
```

- **ES CB**: 5 failures in 10s → OPEN. Recovery probe after 30s. Fallback: 503 with `Retry-After`.
- **Postgres CB**: On OPEN, serve document detail from Redis cache (stale-while-revalidate).

### Retry Strategy

| Dependency | Strategy | Max Attempts | Backoff |
|------------|----------|-------------|---------|
| ES indexing (Celery) | Exponential | 3 | 30s, 90s, 270s |
| ES search (request) | Fast fail | 2 | 100ms |
| Postgres (request) | None | 1 | — |
| Redis (request) | 1 retry | 1 | 50ms |
| RabbitMQ publish | Exponential | 5 | 1s, 2s, 4s, 8s, 16s |

### Graceful Degradation

| Scenario | Degraded Behaviour |
|----------|--------------------|
| ES down | 503 on search; ingest continues to Postgres; workers retry on recovery |
| Redis down | Bypass cache; serve from PG/ES; rate limiting falls back to in-memory |
| RabbitMQ down | Synchronous ES indexing fallback (best-effort) |
| Postgres down | Read-only mode: serve cached documents; reject writes with 503 |

---

## 2.3 Security

**Authentication**
- Prototype: Header-based tenant ID (suitable for internal/demo).
- Production: OAuth2/JWT via IdP (Auth0, Okta). JWT carries `tenant_id` + scopes claims. API Gateway handles token introspection at the edge.
- Service-to-service: mTLS via SPIFFE/SPIRE.

**Tenant Data Isolation**

| Layer | Mechanism |
|-------|-----------|
| API | JWT tenant claim validated by middleware |
| ORM | `TenantAwareManager` — `WHERE tenant_id = ?` on every query |
| Elasticsearch | Per-tenant index + mandatory `term` filter in every query |
| PostgreSQL | Row-Level Security as backstop (`CREATE POLICY tenant_isolation ON documents USING (tenant_id = current_setting(...))`) |

**Additional**
- Secrets via HashiCorp Vault or AWS Secrets Manager — not environment variables.
- TLS 1.3 on all external endpoints; Istio/Linkerd encrypts inter-pod traffic.
- Structured audit logging: every API call logged with `tenant_id`, endpoint, response code, latency → SIEM.

---

## 2.4 Observability

**Key Metrics (Prometheus + Grafana)**

| Metric | Alert Threshold |
|--------|----------------|
| `api_request_duration_seconds` p95 | > 500ms |
| `search_query_duration_seconds` p95 | > 200ms |
| `document_index_queue_depth` | > 10,000 |
| `document_index_failure_total` rate | > 1/min |
| `celery_task_duration_seconds` p95 | > 5s |

**Logging:** Structured JSON with `trace_id`, `tenant_id`, `request_id` on every line. Fluentbit → Elasticsearch (separate cluster) → Kibana. 30-day hot / 90-day cold retention.

**Distributed Tracing:** OpenTelemetry auto-instrumentation (Django, Celery, Redis, PG, ES). W3C `traceparent` propagation. 100% sampling on errors, 1% on success.

**Alerting:** PagerDuty P1 on 503 > 2 min; P2 on p95 > 500ms for 5 min; Slack on queue depth > 10k or task failure rate > 1%.

---

## 2.5 Performance Optimisation

**PostgreSQL**
- Partial index: `CREATE INDEX ON documents (tenant_id) WHERE is_deleted = false` — covers 99% of query patterns at a fraction of the size.
- Keyset (cursor-based) pagination in bulk jobs: `WHERE id > last_seen_id ORDER BY id LIMIT N` — constant-time vs. O(offset) for `OFFSET/LIMIT`.
- PgBouncer transaction mode: reduces per-request connection overhead from ~3ms to < 0.1ms.
- Aggressive autovacuum on `documents`: `autovacuum_vacuum_scale_factor = 0.01`.

**Elasticsearch**
- Set `refresh_interval: 30s` during bulk ingest; restore to `1s` after — reduces refresh overhead by ~60%.
- Disable replicas during initial load; re-enable after push completes.
- Batch 500 documents per bulk request. Target 20–40 MB payload.
- `search_after` keyset pagination for deep result pages (ES `from/size` limited to 10,000).

**Caching**
- Probabilistic cache refresh (XFetch) to prevent cache stampede on popular queries.
- Cache key versioning (`v2:search:...`) for zero-downtime schema invalidation.

---

## 2.6 Operations

**Blue/Green Deployment**
```
                     Load Balancer
                          │
         ┌────────────────┴──────────────────┐
         │                                   │
    Blue (v1.2) ← 100% traffic         Green (v1.3) ← standby
         │                                   │
         └── smoke test Green → 10% → validate → 100% → Blue becomes standby
```

**Zero-downtime DB migrations:** Expand phase (nullable columns) → deploy dual-compatible code → backfill → contract phase (constraints).

**Zero-downtime ES reindex:** Create `v2` index → dual-write → backfill via `bulk_reindex_tenant` → atomic alias cutover → drop `v1`.

**Backup & Recovery**

| Data | Method | Frequency | RTO | RPO |
|------|--------|-----------|-----|-----|
| PostgreSQL | pg_basebackup + WAL → S3 | Continuous WAL | 15 min | 0–5 min |
| Elasticsearch | Snapshot API → S3 | Hourly | 30 min | 1 hour |
| Redis | RDB + AOF → S3 | Every 60s | 5 min | 60s |
| RabbitMQ | Quorum queue replication | Real-time | Instant | 0 |

---

## 2.7 SLA — Achieving 99.95% Availability

**99.95% = 21.9 minutes downtime/month**

| Component | Target | Mechanism |
|-----------|--------|-----------|
| API (Django) | 99.99% | Multi-AZ, HPA, readiness probes |
| PostgreSQL | 99.99% | Patroni HA, < 30s failover |
| Elasticsearch | 99.95% | 3-node cluster, shard replicas |
| Redis | 99.99% | Redis Cluster 3+3 |
| RabbitMQ | 99.99% | Quorum queues |

**SLO stack:**
```
SLA (contractual): 99.95%
  └─ SLO (internal): 99.97%  ← error budget buffer
       └─ SLI: success rate of /search and /documents
                (excluding 429 and 400 — client errors)
```

**Error budget policy:** > 50% remaining → normal cadence. 10–50% → freeze non-critical deploys. < 10% → freeze all, focus on reliability.

---

## 2.8 Cost Optimisation

| Strategy | Savings |
|----------|---------|
| Spot instances for Celery workers | 60–70% compute cost reduction |
| ES warm/cold tiers + S3 searchable snapshots | 80% storage cost for old data |
| Reserved instances for PG primary + ES master nodes | 30–40% vs on-demand |
| Right-sizing via Prometheus metrics | 20–30% of initial over-provisioning |

---

# 3. Enterprise Experience Showcase

---

## 3.1 A Similar Distributed System I've Built

At my current role, I worked on a data ingestion and search pipeline that processed and indexed approximately **500 million documents into Elasticsearch** to power filtering and faceted search across the product. The data originated in PostgreSQL — structured records across multiple tables — and needed to be denormalized, enriched, and pushed into ES to serve sub-second filter queries at scale.

The core pipeline was: Postgres → Celery workers (via RabbitMQ) → Elasticsearch. Each Celery task would pull a batch of records from Postgres using cursor-based pagination, build the denormalized ES document, and bulk-index it. At 500M records, a naïve single-worker approach would have taken days. The key lever was **dynamic worker scaling**: we monitored the Celery queue depth in RabbitMQ via the management API and automatically scaled the number of workers up during an active push job and back down to baseline after the queue drained. This let us push at maximum throughput (20–30 Celery workers during peak ingest) without burning resources 24/7.

On the ES side, we tuned `refresh_interval` to `30s` during bulk ingest (vs the default `1s`) and disabled replicas while loading, then re-enabled them after the push completed — this alone cut ingest time by ~60%. Shard sizing was calibrated to keep each shard under 40 GB, and we used routing keys based on the primary entity to ensure related documents landed on the same shard and avoided scatter-gather on filter queries.

---

## 3.2 A Performance Optimization That Resulted in Significant Improvements

The original ingest pipeline read records from Postgres using `OFFSET/LIMIT` pagination across large tables. At small scales this was fine, but past 10M rows, each page fetch was doing a full sequential scan up to the offset — query time for page 500,000 was over 8 seconds and growing linearly. The Celery tasks were spending more time waiting on Postgres than on ES indexing.

I replaced the `OFFSET/LIMIT` approach with **keyset (cursor-based) pagination**: instead of `OFFSET N`, each batch query used `WHERE id > last_seen_id ORDER BY id LIMIT batch_size`. This gave constant-time page fetches regardless of how deep into the table we were — query time went from 8s at page 500k to under 50ms consistently across the full 500M row dataset.

The second optimization was batching: rather than one Celery task per document, each task processed 500 records and issued a single ES `bulk` API call. This reduced Celery task overhead (dispatch, ack, result write) by 500× and cut RabbitMQ message volume from 500M to 1M messages for a full reindex. Combined, these two changes reduced a full 500M-document reindex from an estimated 4+ days to just under 18 hours with 20 workers running in parallel.

---

## 3.3 A Critical Production Incident I Resolved

During one of the larger scheduled push jobs (roughly 80M documents), the Celery workers started silently stalling — tasks were being picked up but not completing. RabbitMQ showed messages being acknowledged but the queue depth wasn't decreasing, and Elasticsearch cluster health had flipped to YELLOW. No errors in Celery logs.

After about 20 minutes of investigation, the root cause was a combination of two things: (1) the ES bulk thread pool queue had filled up — too many concurrent bulk requests from 25 workers hitting a 3-node cluster — causing ES to start returning `429 Too Many Requests`, and (2) our Celery task had no retry on `429`, so it was silently completing with a failed bulk response that we weren't checking. Documents were being "indexed" but nothing was landing in ES.

The immediate fix was to reduce active workers from 25 to 10, add explicit `429` detection on the bulk response errors list, and retry failed items with a short sleep. Longer term, we added a check on `errors=True` in the bulk API response (which we had previously ignored by looking only at the HTTP status), wired failed document IDs into a DLQ for reprocessing, and tuned the ES `thread_pool.write.queue_size` setting to better match our ingest concurrency. We also added a Grafana alert on ES `bulk rejections` metric, which would have caught this within 2 minutes rather than 20.

---

## 3.4 An Architectural Decision That Balanced Competing Concerns

The biggest design tension was around **when to scale Celery workers** during a push job. Two options were on the table:

- **Static fleet**: Run a fixed number of workers (say, 10) permanently. Simple, predictable cost, but 10× slower during a large push and wasteful when idle.
- **Dynamic scaling**: Scale workers up automatically when a push job starts, down when the queue drains.

We went with dynamic scaling, but the challenge was *what signal to scale on*. CPU and memory on the worker nodes lagged the actual queue state by several minutes. We ended up scaling directly off **RabbitMQ queue depth** polled via the management HTTP API: if `messages_ready > 50,000`, spin up additional workers; if `messages_ready < 1,000`, scale back down. The threshold was tuned empirically over a few push runs.

The trade-off we accepted was cold-start latency — new worker containers took 20–30 seconds to come up, so the first ~50k tasks were always processed at baseline capacity before the extra workers joined. For our workload (push jobs measured in hours) this was acceptable. The benefit was a 3–4× cost reduction versus running a large static fleet permanently, and no operational burden of manually adjusting workers before each push.

---

# 4. AI Tool Usage

- **GitHub Copilot**: Used for boilerplate acceleration — serializer fields, migration scaffolding, and repetitive patterns.
- **Claude (Anthropic)**: Used to structure and refine language in written documents, and to validate architectural trade-off reasoning.
- All design decisions, system architecture, code structure, incident narratives, and production analysis are original work.
