from __future__ import annotations
from .config import RateLimitConfig

class TokenBucket:
    """One client's allowance. Refilled lazily: on ACCESS, not by a timer.

    `now` is injected (float seconds, monotonic) instead of called internally —
    that's what makes unit tests trivial and dependency-free at the same time."""
    def __init__(self, capacity: float, refill_per_sec: float) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self.tokens = capacity          
        self.last_refill = 0.0          

    def try_take(self, now: float) -> bool:
        if self.last_refill == 0.0:     
            self.last_refill = now
        
        self.tokens = min(self.capacity,
                          self.tokens + (now - self.last_refill) * self.refill_per_sec)
        self.last_refill = now
        if self.tokens >= 1.0:          
            self.tokens -= 1.0
            return True
        return False


class RateLimiter:
    """State map: (route_id, ip) → TokenBucket.

    SCOPE note (tutorial 13 §7): this is per-PROCESS state. With N proxy
    replicas each allows N× the limit. Honest and correct per instance."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], TokenBucket] = {}

    def _bucket_for(self, route_id: str, ip: str, cfg: RateLimitConfig) -> TokenBucket:
        key = (route_id, ip)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(capacity=cfg.burst,
                                 refill_per_sec=cfg.per_ip / cfg.per_seconds)
            self._buckets[key] = bucket
        return bucket

    def try_acquire(self, route_id: str, ip: str, cfg: RateLimitConfig, now: float) -> bool:
        """True = allowed (a token was spent). False = 429 for this request."""
        return self._bucket_for(route_id, ip, cfg).try_take(now)

    def prune_stale(self, now: float, older_than_s: float = 900.0) -> None:
        """Memory hygiene: drop buckets untouched for 15 min (default). A scraper
        rotating IPs must not grow our dict forever. Called periodically by the
        main task loop (m9 wires a timer)."""
        stale = [k for k, b in self._buckets.items()
                 if now - b.last_refill > older_than_s]
        for k in stale:
            del self._buckets[k]
