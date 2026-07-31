"""Small thread-safe runtime primitives used by the web process."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator, MutableMapping
import threading
from typing import Generic, TypeVar


K = TypeVar("K")
V = TypeVar("V")


class BoundedCache(MutableMapping[K, V], Generic[K, V]):
    """A locked LRU mapping with a hard entry limit.

    Callers retain ownership of TTL policy, which keeps existing per-cache
    expiration semantics intact while preventing process-lifetime growth.
    """

    def __init__(self, max_entries: int) -> None:
        self.max_entries = max(1, int(max_entries))
        self._rows: OrderedDict[K, V] = OrderedDict()
        self._lock = threading.RLock()

    def __getitem__(self, key: K) -> V:
        with self._lock:
            value = self._rows[key]
            self._rows.move_to_end(key)
            return value

    def __setitem__(self, key: K, value: V) -> None:
        with self._lock:
            self._rows[key] = value
            self._rows.move_to_end(key)
            while len(self._rows) > self.max_entries:
                self._rows.popitem(last=False)

    def __delitem__(self, key: K) -> None:
        with self._lock:
            del self._rows[key]

    def __iter__(self) -> Iterator[K]:
        with self._lock:
            return iter(tuple(self._rows))

    def __len__(self) -> int:
        with self._lock:
            return len(self._rows)

    def get(self, key: K, default: V | None = None):  # type: ignore[override]
        with self._lock:
            if key not in self._rows:
                return default
            self._rows.move_to_end(key)
            return self._rows[key]

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()
