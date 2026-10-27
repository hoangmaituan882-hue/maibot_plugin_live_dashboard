"""Bounded, demand-driven JSON cache; failures and expired data are never reused."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

CacheKey = tuple[str, tuple[tuple[str, str], ...]]


class ApiPayload(dict[str, Any]):
    """Preserve the upstream dictionary while keeping local freshness metadata separate."""

    def __init__(self, data: dict[str, Any], cache_info: dict[str, Any]):
        super().__init__(data)
        self.cache_info = cache_info


@dataclass(frozen=True)
class _Entry:
    data: dict[str, Any]
    saved_at: float
    fetched_at: str
    ttl: int


class ResponseCache:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, max_entries: int = 32):
        self._clock = clock
        self._max_entries = max_entries
        self._entries: OrderedDict[CacheKey, _Entry] = OrderedDict()
        self._inflight: dict[CacheKey, asyncio.Task[_Entry]] = {}
        self._closed = False

    def _payload(self, entry: _Entry, *, hit: bool, coalesced: bool = False) -> ApiPayload:
        return ApiPayload(
            deepcopy(entry.data),
            {
                "hit": hit,
                "coalesced": coalesced,
                "fetched_at": entry.fetched_at,
                "age_seconds": round(max(0.0, self._clock() - entry.saved_at), 3),
                "ttl_seconds": entry.ttl,
            },
        )

    async def _load(self, key: CacheKey, ttl: int, loader: Callable[[], Awaitable[dict]]) -> _Entry:
        data = await loader()  # Exceptions propagate and never create a cache entry.
        entry = _Entry(deepcopy(data), self._clock(), datetime.now(timezone.utc).isoformat(), ttl)
        if ttl > 0 and not self._closed:
            now = self._clock()
            for stale in [k for k, v in self._entries.items() if now - v.saved_at >= v.ttl]:
                del self._entries[stale]
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return entry

    def _finished(self, key: CacheKey, task: asyncio.Task[_Entry]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        # A cancelled caller may have left a shielded fetch running without other waiters.
        if not task.cancelled():
            task.exception()

    async def get(
        self, key: CacheKey, ttl: int, loader: Callable[[], Awaitable[dict]]
    ) -> ApiPayload:
        if self._closed:
            raise RuntimeError("查询缓存已关闭")
        if ttl <= 0:
            return self._payload(await self._load(key, 0, loader), hit=False)
        entry = self._entries.get(key)
        if entry is not None:
            if self._clock() - entry.saved_at < entry.ttl:
                self._entries.move_to_end(key)
                return self._payload(entry, hit=True)
            del self._entries[key]
        task = self._inflight.get(key)
        coalesced = task is not None
        if task is None:
            task = asyncio.create_task(self._load(key, ttl, loader))
            self._inflight[key] = task
            task.add_done_callback(lambda done: self._finished(key, done))
        # Cancellation of one query must not cancel another query's shared request.
        entry = await asyncio.shield(task)
        return self._payload(entry, hit=False, coalesced=coalesced)

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        self._entries.clear()
