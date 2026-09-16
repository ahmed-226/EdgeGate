from __future__ import annotations
import asyncio
import logging
from .config import HealthConfig
from .upstream import Backend

log = logging.getLogger("edgegate.health")

class HealthProbe:
    """Runs ONE liveness check: connect → GET path → 2xx is healthy.

    Separated from HealthChecker so the probe logic is independently testable
    without a running event loop scheduler."""
    def __init__(self, config:HealthConfig):
        self.config =config

    async def run(self,backend:Backend)->bool:
        try:
            async with asyncio.timeout(self.config.timeout_s):
                reader, writer = await asyncio.open_connection(backend.host,backend.port)
                request = (f"GET {self.config.path} HTTP/1.1\r\n"
                           f"Host: {backend.address}\r\n"
                           f"Connection: close\r\n\r\n").encode("ascii")
                writer.write(request)
                await writer.drain()
                status_line = await reader.readuntil(b"\r\n")
                writer.close()
                await writer.wait_closed()
            code = int(status_line.split(b" ",2)[1])
            return 200 <= code < 300
        except (TimeoutError, ConnectionError, OSError, ValueError):
            return False


class HealthChecker:
    """Owns the loop + the debounce state per backend."""

    def __init__(self, config: HealthConfig, backends: list[Backend]) -> None:
        self.config = config
        self.probe = HealthProbe(config)
        self.fail_streak: dict[int, int] = {}
        self._backends = backends

    async def run(self) -> None:
        while True:
            await self.check_all()
            await asyncio.sleep(self.config.interval_s)   # NEVER time.sleep (tutorial 04)

    async def check_all(self) -> None:
        """Probe all backends CONCURRENTLY: create a task per backend and gather.
        If we instead probed sequentially, one slow backend would delay every
        other backend's result every cycle (tutorial 11, detail #3)."""
        tasks = [asyncio.create_task(self._probe_managed(b)) for b in self._backends]
        if tasks:
            await asyncio.gather(*tasks)          # wait for all probing to finish

    async def _probe_managed(self, backend: Backend) -> None:
        """Wrap one probe: update the debounce streak and (if crossed) the flag."""
        ok = await self.probe.run(backend)
        key = id(backend)

        if ok:
            self.fail_streak.pop(key, None)
            if not backend.healthy:
                log.info("backend %s recovered, marking healthy", backend.address)
                backend.healthy = True
            return

        streak = self.fail_streak.get(key, 0) + 1
        self.fail_streak[key] = streak
        if streak >= self.config.unhealthy_threshold:
            if backend.healthy:
                log.warning("backend %s unhealthy (%d consecutive failures)",
                            backend.address, streak)
                backend.healthy = False

