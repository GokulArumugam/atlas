"""Small, in-memory caches for hot analyst paths.

We keep this deliberately simple: an LRU with TTL, thread-safe, no eviction
callback. Anything more (Redis, memcached) is a deploy-time choice, so the
interface is just `.get(key) -> value | None` and `.set(key, value)`.
"""

from atlas.cache.lru import TTLLRUCache

__all__ = ["TTLLRUCache"]
