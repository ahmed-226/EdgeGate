from __future__ import annotations
import abc
from .upstream import Backend


class LoadBalancer(abc.ABC):
    """pick() → the backend for the NEXT request, or None if none are usable."""

    def __init__(self, backends:list[Backend])->None:
        self.backends = backends

    def _usable(self) -> list[Backend]:
        # The health rule that applies to EVERY strategy :
        # only healthy backends enter the selection set.
        return [b for b in self.backends if b.healthy]

    @abc.abstractmethod
    def pick(self) -> Backend | None:
        """Subclasses implement the selection math."""

    def acquire(self, backend: Backend) -> None:
        """Called BEFORE a request is forwarded to `backend`. Default: no-op —
        only LeastConnectionsBalancer needs it (increment live)."""
        return

    def release(self, backend: Backend) -> None:
        """Called after a request completes. Default: no-op — only
        LeastConnectionsBalancer needs it (decrement live)."""
        return


class RoundRobinBalancer(LoadBalancer):
    """Fair turns across equal-capacity backends. Index DRIFTS-proof: we never
    use % len(backends) — the pool can change (health flips) between requests,
    so the index advances over the *current usable* snapshot each time."""

    def __init__(self, backends: list[Backend]) -> None:
        super().__init__(backends)
        self._index = -1

    def pick(self) -> Backend | None:
        usable = self._usable()
        if not usable: # caller will 503
            return None  
        self._index = (self._index + 1) % len(usable)
        return usable[self._index]

class LeastConnectionsBalancer(LoadBalancer):
    """Pick the usable backend with the fewest in-flight requests. A slow backend
    accumulates `live` and sheds load by itself"""

    def pick(self) -> Backend | None:
        usable = self._usable()
        if not usable:
            return None
        return min(usable, key=lambda b: b.live)

    def release(self, backend: Backend) -> None:
        if backend.live > 0:
            backend.live -= 1

    def acquire(self, backend: Backend) -> None:
        backend.live += 1                        

