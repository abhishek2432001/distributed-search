import time
from datetime import datetime, timezone

from django.conf import settings
from django.core.cache import cache


class RateLimiter:
    """Sliding window counter rate limiter. Redis key: ratelimit:{tenant_id}:{YYYY-MM-DDTHH:MM}"""

    def __init__(self):
        self.default_limit: int = getattr(settings, "DEFAULT_RATE_LIMIT", 100)

    def _bucket_key(self, tenant_id: str, minute_ts: str) -> str:
        return f"ratelimit:{tenant_id}:{minute_ts}"

    def _minute_str(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M")

    def check(self, tenant) -> tuple[bool, int, int]:
        """Returns (allowed, remaining, retry_after_seconds)."""
        limit = getattr(tenant, "rate_limit", None) or self.default_limit
        tenant_id = str(tenant.id)
        now = time.time()

        current_minute = self._minute_str(now)
        previous_minute = self._minute_str(now - 60)
        seconds_into_minute = now % 60

        pipe = cache.client.get_client().pipeline(transaction=False)
        pipe.incr(self._bucket_key(tenant_id, current_minute))
        pipe.expire(self._bucket_key(tenant_id, current_minute), 120)
        pipe.get(self._bucket_key(tenant_id, previous_minute))
        results = pipe.execute()

        current_count = int(results[0])
        previous_count = int(results[2]) if results[2] else 0
        weight = 1.0 - (seconds_into_minute / 60.0)
        effective_count = current_count + (previous_count * weight)

        if effective_count <= limit:
            return True, max(0, int(limit - effective_count)), 0
        return False, 0, int(60 - seconds_into_minute) + 1

    def get_headers(self, tenant, allowed: bool, remaining: int, retry_after: int) -> dict:
        limit = getattr(tenant, "rate_limit", None) or self.default_limit
        headers = {"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": str(remaining)}
        if not allowed:
            headers["Retry-After"] = str(retry_after)
        return headers


rate_limiter = RateLimiter()
