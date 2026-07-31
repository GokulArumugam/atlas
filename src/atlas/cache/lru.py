"""Thread-safe LRU cache with per-entry TTL."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLLRUCache(Generic[K, V]):
    """LRU with TTL. All operations are O(1) except eviction sweep on set."""

    def __init__(self, max_size: int = 512, ttl_seconds: float = 300.0) -> None:
        self._max_size = max(1, int(max_size))
        self._ttl = float(ttl_seconds)
        self._items: OrderedDict[K, tuple[V, float]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: K) -> V | None:
        now = time.monotonic()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                self.misses += 1
                return None
            value, expires_at = entry
            if expires_at < now:
                self._items.pop(key, None)
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: K, value: V) -> None:
        expires_at = time.monotonic() + self._ttl
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
            self._items[key] = (value, expires_at)
            while len(self._items) > self._max_size:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._items),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl,
                "hits": self.hits,
                "misses": self.misses,
            }
