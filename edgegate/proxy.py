from __future__ import annotations
import asyncio
from .config import Config
from .http import Request, Response, HttpParser, encode_request_head
from .observability import MetricsRegistry, AccessLogger
from .router import Router, Route
from .upstream import Backend
from .health import HealthChecker
import time
import logging

class ProxyServer:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.metrics = MetricsRegistry()
        self.access_logger = AccessLogger(self.metrics)

    async def handle_client(self,reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = ProxyConnection(self, reader, writer)
        await connection.serve()


    async def run(self) -> None:
        host, port = self.config.host, self.config.port
        loop = asyncio.get_running_loop()

        # The router + breaker/delay-collaborators need the RUNNING loop
        # (CircuitBreaker schedules its cooldown via loop.call_later), so we
        # wire them here — not in __init__, which runs before the loop exists.
        self.router = Router(self.config.routes, loop)
        self.backends = [b for r in self.router.routes for b in r.backends]
        self.health_checker = HealthChecker(self.config.health, self.backends)

        server = await asyncio.start_server(self.handle_client,host,port)

        task = loop.create_task(self.health_checker.run())
        task.add_done_callback(self._log_task_exception)
        async with server:
            await server.serve_forever()

    @staticmethod
    def _log_task_exception(task) -> None:
        if not task.cancelled() and task.exception():
            logging.exception("background task crashed", exc_info=task.exception())



class ProxyConnection:
    def __init__(self, server: "ProxyServer",
                 reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.server = server
        self.config = server.config
        self.reader = reader
        self.writer = writer

    
    async def serve(self) -> None:
        try:
            await self._handle_one()          
        except Exception:                      
            pass                               
        finally:
            self.writer.close()
            await self.writer.wait_closed()

    async def _handle_one(self) -> None:
        started_at = time.perf_counter()

        request = await HttpParser.read_request(self.reader)
        client_ip = self.writer.get_extra_info("peername")[0]

        # Proxy's OWN endpoints — answered unconditionally, BEFORE routing.
        # They must work even when no route matches (docker healthchecks, /metrics).
        if request.target == "/healthz":
            await self._finish("-", request,
                               await self._send_error(200, "OK", b"ok"),
                               client_ip, started_at)
            return
        if request.target == "/metrics":
            await self._finish("-", request,
                               await self._send_error(200, "OK",
                                   self.server.metrics.render_metrics().encode()),
                               client_ip, started_at)
            return

        route = self.server.router.match(request.target)

        if route is None:
            # No route matched: 404 straight from the proxy (the router's own response, nothing upstream involved).
            await self._finish("-", request,
                               await self._send_error(404, "Not Found", b"no route matched"),
                               client_ip, started_at)
            return

        # Rate-limit deny happens BEFORE any upstream work: a rejected request must never open a backend socket. 
        # Logged through _finish like every other outcome, with upstream "-" proving no contact.
        if route.limiter is not None:
            rate_cfg = route.config.rate_limit
            allowed = route.limiter.try_acquire(
                route.config.id, client_ip, rate_cfg, time.perf_counter())
            if not allowed:
                self.server.metrics.incr("rate_limited")
                await self._finish("-", request,
                                   await self._send_error(429, "Too Many Requests",
                                                          b"rate limit exceeded",
                                                          retry_after=1),
                                   client_ip, started_at)
                return

        # Circuit breaker + bounded failover: consult the breaker, then try healthy backends one at a time. OutageError means the breaker
        # refused (OPEN) OR every attempt failed — both are a proxy-side 503 with zero upstream traffic in the OPEN case.
        try:
            backend, response = await self._forward_with_retry(request, route, client_ip)
        except OutageError:
            await self._finish("-", request,
                               await self._send_error(503, "Service Unavailable",
                                                      b"no healthy backend / circuit open"),
                               client_ip, started_at)
            return

        await self._finish(backend.address, request, response, client_ip, started_at)

    async def _finish(self, upstream: str, request: Request, response: Response,
                      client_ip: str, started_at: float) -> None:
        """Log the access line + bump counters — the ONE place that records an
        outcome (forwarded, 404, healthz, metrics all pass through here)."""
        self.server.access_logger.log(
            request, response, client_ip=client_ip,
            upstream=upstream, started_at=started_at,
            bytes_in=len(request.body), bytes_out=len(response.body),
        )


    async def _send_error(self, status: int, reason: str, body: bytes,
                          *, retry_after: int | None = None) -> Response:
        """Write a proxy-generated response. Returns the Response object so callers can feed it straight into the access logger.
        Content-Length framing — no keep-alive this milestone.

        retry_after: for 429 the RFC 7231 hint tells clients when to try again;
        the token bucket's refill rate already tells us the honest seconds."""
        headers = {"Content-Length": str(len(body)),
                   "Content-Type": "text/plain",
                   "Connection": "close"}
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        response = Response(
            version="HTTP/1.1", status_code=status, reason=reason,
            headers=headers,
            body=body,
        )
        self.writer.write(self._render_response_head(response) + response.body)
        await self.writer.drain()
        return response
    
    async def _forward_with_retry(self, request: Request, route: Route,
                                  client_ip: str) -> tuple[Backend, Response]:
        """Try healthy backends one at a time, bounded by the pool size.
        Each attempt re-picks via the balancer so we never use the same
        backend twice if the pool still has alternatives. Returns the backend
        that served us so the access log can name it."""
        breaker = route.breaker

        if not breaker.is_allowed():
            # OPEN (or HALF_OPEN without probes left): refuse WITHOUT touching
            # the network — this is the breaker's entire reason to exist.
            self.server.metrics.incr("circuit_open_rejections")
            raise OutageError("circuit open")

        # Bounded retries: at most len(pool) attempts per request.
        last_error: Exception | None = None
        attempted: set[Backend] = set()
        for _ in range(max(1, len(route.backends))):
            backend = route.balancer.pick()
            if backend is None or backend in attempted:
                break                             # all unhealthy / pool exhausted
            attempted.add(backend)

            route.balancer.acquire(backend)
            try:
                # Rewriting is per-attempt: the rewritten Host must name THIS
                # backend, so it happens after each pick.
                rewritten = self.server.router.rewrite_request(request, route, backend, client_ip)
                response = await self._forward(rewritten, backend, client_ip)
                breaker.record_success()
                return backend, response
            except UpstreamError as e:
                last_error = e
                breaker.record_failure()
                # fall through → next attempt (different backend) unless breaker OPEN
                if not breaker.is_allowed():
                    break
            finally:
                route.balancer.release(backend)

        raise OutageError(str(last_error)) if last_error else OutageError("no healthy backend")


    async def _forward(self, request: Request, backend: Backend, client_ip: str) -> Response:
        """Open one upstream connection, send the request, read the response,
        relay it to the client. ANY upstream misbehavior raises UpstreamError
        (breaker-worthy); success returns the parsed Response for logging."""
        try:
            async with asyncio.timeout(2):
                reader, writer = await asyncio.open_connection(backend.host, backend.port)
        except (OSError, TimeoutError) as e:
            raise UpstreamError(f"connect failed: {e}") from e
        try:
            head = encode_request_head(request)
            async with asyncio.timeout(10):
                writer.write(head + request.body)
                await writer.drain()
            async with asyncio.timeout(30):
                response = await HttpParser.read_response(reader)
            await self._relay(response)
            return response
        except (TimeoutError, ConnectionError, OSError) as e:
            raise UpstreamError(str(e)) from e
        finally:
            writer.close()
            await writer.wait_closed()

    async def _relay(self, response: Response) -> None:
        """Re-serialize the upstream response head + body to the client (m2)."""
        self.writer.write(self._render_response_head(response))
        self.writer.write(response.body)
        await self.writer.drain()


    @staticmethod
    def _render_response_head(response: Response) -> bytes:
        """Serialize our parsed Response head back into bytes for the client."""
        lines = [f"{response.version} {response.status_code} {response.reason}"]
        lines += [f"{k}: {v}" for k, v in response.headers.items()]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


class UpstreamError(Exception):        
    """Upstream connect/timeout/protocol failure — breaker-worthy."""

class OutageError(Exception):          
    """No way to serve this request right now → 503 without retries."""
