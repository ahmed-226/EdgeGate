from __future__ import annotations
import json
import sys
import time
from .http import Request, Response


class MetricsRegistry:
    """Flat counter store. Thread-safety is NOT needed: we are single-loop."""

    def __init__(self) -> None:
        self.counters : dict[str, int]={}

    def incr(self, name:str,by:int=1) -> None:
        self.counters[name] = self.counters.get(name,0) + by

    def get(self, name:str) -> int:
        return self.counters.get(name,0)

    def render_metrics(self) ->str:
        return "".join(f"edgegate_{k} {v}\n" for k,v in sorted(self.counters.items()))

class AccessLogger:
    """One JSON line per request → stdout (in Docker, captured by the log driver)."""
    def __init__(self,metrics:MetricsRegistry,stream=sys.stdout) -> None:
        self.metrics = metrics
        self.stream = stream


    def log(self, request: Request, response: Response | None, *, client_ip: str,
            upstream: str, started_at: float, bytes_in: int = 0, bytes_out: int = 0) -> None:
        
        latency_ms = (time.perf_counter() - started_at) *1000.0

        entry = {
            "ts": time.time(),
            "client_ip": client_ip,
            "method": request.method,
            "path": request.target.partition("?")[0],
            "status": response.status_code if response else 0,
            "upstream": upstream,
            "latency_ms": round(latency_ms, 2),
            "bytes_in": bytes_in,
            "bytes_out": bytes_out,
        }
        self.stream.write(json.dumps(entry) + "\n")
        self.stream.flush()

        self.metrics.incr("total_requests")
        self.metrics.incr(f"status.{entry['status']}")


