"""Circuit Breaker — finite state machine per route.

States:  CLOSED    → normal flow.
         OPEN      → instant 503, zero upstream traffic. Cooldown Q&A answered
                     by a timer: after `cooldown_s`, transition to HALF-OPEN.
         HALF_OPEN → a tiny PROBE budget lets exactly `halfopen_probes` test
                     requests through. One success → CLOSED; one failure → OPEN.
The de-facto requirement: every state must answer the "what about a request?" question.

DESIGN: the breaker is a *synchronous* state machine inside the event loop.
`is_allowed()` never waits — it just consults state, which is why hundreds of 
connections sharing one breaker never quench it."""
from __future__ import annotations
import asyncio
import enum
import logging
from .config import CircuitConfig

log = logging.getLogger("edgegate.circuit")


class BreakerState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class CircuitBreaker:
    def __init__(self, config: CircuitConfig, loop: asyncio.AbstractEventLoop) -> None:
        self.config = config
        self.loop = loop
        self.state = BreakerState.CLOSED
        self.fail_streak = 0            
        self.halfopen_left = 0          
        self._cooldown_handle: asyncio.TimerHandle | None = None

    # ── the two questions every state must answer ───────────────────────────

    def is_allowed(self) -> bool:
        """May the forwarder send THIS request upstream?"""
        if self.state is BreakerState.CLOSED:
            return True                                  
        if self.state is BreakerState.OPEN:
            return False                                 
        
        return self.halfopen_left > 0

    def record_success(self) -> None:
        if self.state is BreakerState.HALF_OPEN:
            if self._cooldown_handle:
                self._cooldown_handle.cancel()
            self.state = BreakerState.CLOSED
            log.info("circuit closed (probe succeeded)")

        self.fail_streak = 0
        self.halfopen_left = 0

    def record_failure(self) -> None:
        if self.state is BreakerState.HALF_OPEN:

            self._trip_open()
            return
        if self.state is BreakerState.CLOSED:
            self.fail_streak += 1
            if self.fail_streak >= self.config.consecutive_failures:
                self._trip_open()

    # ── transitions ─────────────────────────────────────────────────────────

    def _trip_open(self) -> None:
        self.state = BreakerState.OPEN
        self.halfopen_left = 0
        log.warning("circuit OPEN after %d consecutive failures",
                    self.fail_streak)
        self._cooldown_handle = self.loop.call_later(
            self.config.cooldown_s, self._to_half_open)

    def _to_half_open(self) -> None:
        self._cooldown_handle = None
        self.state = BreakerState.HALF_OPEN
        self.halfopen_left = self.config.halfopen_probes
        log.info("circuit HALF-OPEN (probe budget=%d)", self.halfopen_left)
