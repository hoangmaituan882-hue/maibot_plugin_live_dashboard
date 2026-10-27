"""One configuration generation and its resources, including in-flight query leases."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from .services.dashboard_service import DashboardService
from .services.friend_service import FriendService
from .services.health_service import HealthService
from .services.system_status_service import SystemStatusService
from .services.telemetry_service import TelemetryManager
from .services.timeline_service import TimelineService
from .utils.log import logger
from .utils.version import get_plugin_version


class QueryRuntime:
    def __init__(self, config: dict[str, Any], data_dir: Path):
        config = self.config = deepcopy(config)
        self.dashboard = DashboardService(config)
        self.timeline = TimelineService(config)
        self.health = HealthService(config)
        self.friends = FriendService(config)
        self.system = SystemStatusService(config)
        self.services = (self.dashboard, self.timeline, self.health, self.friends, self.system)
        self.telemetry = TelemetryManager(config, get_plugin_version(), data_dir=data_dir)
        for service in self.services:
            service.set_telemetry(self.telemetry)
        self._idle = asyncio.Event()
        self._idle.set()
        self._active = 0
        self._started_at = time.monotonic()
        self._background: asyncio.Task | None = None
        self._closed = False

    def start(self) -> None:
        if self.telemetry.enabled:
            self._background = asyncio.create_task(self._telemetry_lifecycle())

    async def _telemetry_lifecycle(self) -> None:
        try:
            await self.telemetry.track_startup()
            await self.telemetry.track_config(self.config)
            while True:
                await asyncio.sleep(43200)
                await self.telemetry.track_heartbeat(time.monotonic() - self._started_at)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("匿名统计后台任务失败")

    @asynccontextmanager
    async def lease(self):
        # There is no await between checking the generation and incrementing its leases.
        if self._closed:
            raise RuntimeError("插件已停止")
        self._active += 1
        self._idle.clear()
        try:
            yield self
        finally:
            self._active -= 1
            if not self._active:
                self._idle.set()

    async def close(self) -> None:
        self._closed = True
        await self._idle.wait()
        if self._background is not None:
            self._background.cancel()
            await asyncio.gather(self._background, return_exceptions=True)
            self._background = None
        try:
            if self.telemetry.enabled:
                await self.telemetry.track_shutdown(
                    runtime_seconds=time.monotonic() - self._started_at
                )
        finally:
            results = await asyncio.gather(
                *(service.close() for service in self.services),
                self.telemetry.close(),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    logger.error("释放插件资源失败：%s", type(result).__name__)
