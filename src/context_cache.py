from __future__ import annotations

import logging
import time
from collections import deque

from .config import CacheConfig

logger = logging.getLogger("context_cache")

_GROUP_GLOBAL_MAXLEN = 50


class ContextCache:
    """
    Keep recent conversation turns per scope/user pair and a rolling
    group-level context feed for feedback analysis.
    """

    def __init__(self, config: CacheConfig) -> None:
        self._window_size = config.window_size
        self._ttl = config.ttl_seconds
        self._cache: dict[tuple[str, str], deque[dict[str, str]]] = {}
        self._last_update: dict[tuple[str, str], float] = {}
        self._group_global: dict[str, deque[dict[str, str]]] = {}

    def add_user_turn(self, scope_id: str, user_id: str, text: str) -> None:
        self._add_turn(scope_id, user_id, role="user", content=text)

    def add_assistant_turn(self, scope_id: str, user_id: str, text: str) -> None:
        self._add_turn(scope_id, user_id, role="assistant", content=text)

    def add(self, group_id: int | str, user_id: str, text: str) -> None:
        # Backward-compatible helper used by older callers.
        self.add_user_turn(str(group_id), user_id, text)

    def get_history(
        self,
        scope_id: int | str,
        user_id: str,
        limit: int | None = None,
    ) -> list[dict[str, str]]:
        key = (str(scope_id), str(user_id))
        if key not in self._cache:
            return []
        if self._is_expired(key):
            self._evict(key)
            return []

        history = list(self._cache[key])
        if limit is not None and limit > 0:
            history = history[-limit:]
        return history

    def clear(self, scope_id: int | str | None = None) -> int:
        if scope_id is None:
            count = len(self._cache)
            self._cache.clear()
            self._last_update.clear()
            return count

        scope = str(scope_id)
        keys = [key for key in self._cache if key[0] == scope]
        for key in keys:
            self._evict(key)
        return len(keys)

    def cleanup_expired(self) -> int:
        expired = [key for key in self._last_update if self._is_expired(key)]
        for key in expired:
            self._evict(key)
        if expired:
            logger.debug("Cleaned expired context cache entries: %s", len(expired))
        return len(expired)

    @property
    def size(self) -> int:
        return len(self._cache)

    def add_group_msg(self, group_id: int | str, user_id: str, text: str) -> None:
        gid = str(group_id)
        if gid not in self._group_global:
            self._group_global[gid] = deque(maxlen=_GROUP_GLOBAL_MAXLEN)
        self._group_global[gid].append({"user_id": str(user_id), "text": text})

    def get_group_history(self, group_id: int | str) -> list[dict[str, str]]:
        return list(self._group_global.get(str(group_id), []))

    def clear_group_global(self, group_id: int | str | None = None) -> int:
        if group_id is None:
            count = sum(len(items) for items in self._group_global.values())
            self._group_global.clear()
            return count

        gid = str(group_id)
        count = len(self._group_global.get(gid, []))
        self._group_global.pop(gid, None)
        return count

    def _add_turn(self, scope_id: str, user_id: str, *, role: str, content: str) -> None:
        key = (str(scope_id), str(user_id))
        if key not in self._cache:
            # Keep enough turns for user/assistant back-and-forth within the window.
            self._cache[key] = deque(maxlen=max(self._window_size * 2, 2))
        self._cache[key].append({"role": role, "content": content})
        self._last_update[key] = time.monotonic()

    def _is_expired(self, key: tuple[str, str]) -> bool:
        if self._ttl <= 0:
            return True
        return time.monotonic() - self._last_update.get(key, 0.0) > self._ttl

    def _evict(self, key: tuple[str, str]) -> None:
        self._cache.pop(key, None)
        self._last_update.pop(key, None)
