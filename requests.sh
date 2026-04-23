#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Distributed Document Search Service — Sample API Requests
# ─────────────────────────────────────────────────────────────────────────────
# Usage:
#   1. Start the stack: docker compose up -d
#   2. Create a tenant (see README) and set TENANT_UUID below
#   3. chmod +x requests.sh && ./requests.sh
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL="http://localhost:8000"
TENANT_UUID=""  # ← Paste your tenant UUID here

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

check_tenant() {
  if [ -z "$TENANT_UUID" ]; then
    echo -e "${YELLOW}⚠ TENANT_UUID is not set. Create a tenant first:${NC}"
    echo "  docker compose exec web python manage.py shell -c \""
    echo "  from documents.models import Tenant"
    echo "  t = Tenant.objects.create(name='Demo Corp', slug='demo', rate_limit=200)"
    echo "  print('Tenant ID:', t.id)"
    echo "  \""
    exit 1
  fi
}

separator() {
  echo ""
  echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${BLUE}  $1${NC}"
  echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""
}

# ─────────────────────────────────────────────────────────────────────────────
separator "1. Health Check (no tenant required)"
curl -s "$BASE_URL/health" | python3 -m json.tool
echo -e "\n${GREEN}✓ Health check complete${NC}"

# ─────────────────────────────────────────────────────────────────────────────
check_tenant

separator "2. POST /api/v1/documents — Index document 1"
DOC1_RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/documents" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $TENANT_UUID" \
  -d '{
    "title": "Introduction to Machine Learning",
    "content": "Machine learning is a subset of artificial intelligence that enables systems to learn from data and make decisions with minimal human intervention. Key techniques include supervised learning, unsupervised learning, and reinforcement learning.",
    "metadata": {
      "author": "Jane Doe",
      "tags": ["machine-learning", "ai", "data-science"],
      "source": "internal-wiki",
      "department": "Engineering"
    }
  }')
echo "$DOC1_RESPONSE" | python3 -m json.tool
DOC1_ID=$(echo "$DOC1_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)
echo -e "\n${GREEN}✓ Document 1 ID: $DOC1_ID${NC}"

separator "3. POST /api/v1/documents — Index document 2"
DOC2_RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/documents" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $TENANT_UUID" \
  -d '{
    "title": "Distributed Systems: CAP Theorem Explained",
    "content": "The CAP theorem states that a distributed system cannot simultaneously provide more than two of the following guarantees: Consistency, Availability, and Partition tolerance. Understanding CAP is essential for designing fault-tolerant microservices architectures.",
    "metadata": {
      "author": "John Smith",
      "tags": ["distributed-systems", "cap-theorem", "architecture"],
      "source": "engineering-blog"
    }
  }')
echo "$DOC2_RESPONSE" | python3 -m json.tool
DOC2_ID=$(echo "$DOC2_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)
echo -e "\n${GREEN}✓ Document 2 ID: $DOC2_ID${NC}"

separator "4. POST /api/v1/documents — Index document 3"
curl -s -X POST "$BASE_URL/api/v1/documents" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $TENANT_UUID" \
  -d '{
    "title": "Redis as a Cache and Rate Limiter",
    "content": "Redis is an in-memory data structure store used as a database, cache, and message broker. Its atomic operations make it ideal for implementing rate limiters using sliding window counters or token bucket algorithms.",
    "metadata": {
      "author": "Alice Chen",
      "tags": ["redis", "caching", "rate-limiting", "performance"],
      "source": "tech-docs"
    }
  }' | python3 -m json.tool
echo -e "\n${GREEN}✓ Document 3 indexed${NC}"

echo ""
echo -e "${YELLOW}⏳ Waiting 4 seconds for Elasticsearch indexing...${NC}"
sleep 4

# ─────────────────────────────────────────────────────────────────────────────
separator "5. GET /api/v1/search — Basic search"
curl -s "$BASE_URL/api/v1/search?q=machine+learning" \
  -H "X-Tenant-ID: $TENANT_UUID" | python3 -m json.tool

# ─────────────────────────────────────────────────────────────────────────────
separator "6. GET /api/v1/search — Cache HIT (same query, should show cached: true)"
curl -s "$BASE_URL/api/v1/search?q=machine+learning" \
  -H "X-Tenant-ID: $TENANT_UUID" | python3 -m json.tool

# ─────────────────────────────────────────────────────────────────────────────
separator "7. GET /api/v1/search — Multi-term search with highlighting"
curl -s "$BASE_URL/api/v1/search?q=distributed+systems+fault+tolerance" \
  -H "X-Tenant-ID: $TENANT_UUID" | python3 -m json.tool

# ─────────────────────────────────────────────────────────────────────────────
separator "8. GET /api/v1/search — Fuzzy search (handles typos)"
curl -s "$BASE_URL/api/v1/search?q=machne+lerning&fuzzy=true" \
  -H "X-Tenant-ID: $TENANT_UUID" | python3 -m json.tool

# ─────────────────────────────────────────────────────────────────────────────
separator "9. GET /api/v1/search — Pagination"
curl -s "$BASE_URL/api/v1/search?q=a&page=1&size=2" \
  -H "X-Tenant-ID: $TENANT_UUID" | python3 -m json.tool

# ─────────────────────────────────────────────────────────────────────────────
separator "10. GET /api/v1/documents/{id} — Retrieve document (cache MISS)"
curl -sv "$BASE_URL/api/v1/documents/$DOC1_ID" \
  -H "X-Tenant-ID: $TENANT_UUID" 2>&1 | grep -E "(X-Cache|{|title|id|status)"

# ─────────────────────────────────────────────────────────────────────────────
separator "11. GET /api/v1/documents/{id} — Same document (cache HIT)"
curl -sv "$BASE_URL/api/v1/documents/$DOC1_ID" \
  -H "X-Tenant-ID: $TENANT_UUID" 2>&1 | grep -E "(X-Cache|{|title|id|status)"

# ─────────────────────────────────────────────────────────────────────────────
separator "12. Rate Limit Headers Demo"
echo "Response headers from a search request:"
curl -sI "$BASE_URL/api/v1/search?q=redis" \
  -H "X-Tenant-ID: $TENANT_UUID" | grep -i "x-ratelimit"

# ─────────────────────────────────────────────────────────────────────────────
separator "13. Error Handling — Missing tenant header"
curl -s "$BASE_URL/api/v1/search?q=test" | python3 -m json.tool

# ─────────────────────────────────────────────────────────────────────────────
separator "14. Error Handling — Invalid UUID"
curl -s "$BASE_URL/api/v1/documents/not-a-valid-uuid" \
  -H "X-Tenant-ID: $TENANT_UUID" | python3 -m json.tool

# ─────────────────────────────────────────────────────────────────────────────
separator "15. DELETE /api/v1/documents/{id} — Delete document 2"
curl -s -X DELETE "$BASE_URL/api/v1/documents/$DOC2_ID" \
  -H "X-Tenant-ID: $TENANT_UUID" | python3 -m json.tool
echo -e "\n${GREEN}✓ Document deleted${NC}"

echo ""
echo -e "${YELLOW}⏳ Waiting 3 seconds for ES removal...${NC}"
sleep 3

separator "16. Verify deleted document is removed from search"
curl -s "$BASE_URL/api/v1/search?q=CAP+theorem" \
  -H "X-Tenant-ID: $TENANT_UUID" | python3 -m json.tool
echo -e "(Expect total: 0 for CAP theorem after deletion)"

# ─────────────────────────────────────────────────────────────────────────────
separator "17. Error Handling — 404 for deleted document"
curl -s "$BASE_URL/api/v1/documents/$DOC2_ID" \
  -H "X-Tenant-ID: $TENANT_UUID" | python3 -m json.tool

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✓ All sample requests complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Monitoring UIs:"
echo "  • Flower (Celery):     http://localhost:5555"
echo "  • RabbitMQ:            http://localhost:15672  (guest/guest)"
echo "  • Swagger:             http://localhost:8000/api/docs/"
echo "  • Elasticsearch:       http://localhost:9200/_cluster/health"
echo ""
