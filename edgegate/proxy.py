from __future__ import annotations
import asyncio
from .config import Config
from .http import Request, Response, HttpParser, encode_request_head
from .observability import MetricsRegistry, AccessLogger
from .router import Router
from .health import HealthChecker
import time
import logging

class ProxyServer:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.metrics = MetricsRegistry()
        self.access_logger = AccessLogger(self.metrics)
        self.router = Router(config.routes)
        self.backends = [b for r in self.router.routes for b in r.backends]
        self.health_checker = HealthChecker(config.health, self.backends)

    async def handle_client(self,reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = ProxyConnection(self, reader, writer)
        await connection.serve()


    async def run(self) -> None:
        host, port = self.config.host, self.config.port
        server = await asyncio.start_server(self.handle_client,host,port)

        loop = asyncio.get_running_loop()

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

        
        backend = route.balancer.pick()
        if backend is None:
            await self._finish("-", request,
                               await self._send_error(503, "Service Unavailable",
                                                      b"no healthy backend"),
                               client_ip, started_at)
            return

        upstream = f"{backend.host}:{backend.port}"
        rewritten = self.server.router.rewrite_request(request, route, backend, client_ip)

        route.balancer.acquire(backend)  # no-op for round-robin; bumps live for least-connections
        try:
            response = await self._forward(rewritten, backend)
        finally:
            route.balancer.release(backend)

        await self._finish(upstream, request, response, client_ip, started_at)

    async def _finish(self, upstream: str, request: Request, response: Response,
                      client_ip: str, started_at: float) -> None:
        """Log the access line + bump counters — the ONE place that records an
        outcome (forwarded, 404, healthz, metrics all pass through here)."""
        self.server.access_logger.log(
            request, response, client_ip=client_ip,
            upstream=upstream, started_at=started_at,
            bytes_in=len(request.body), bytes_out=len(response.body),
        )


    async def _send_error(self, status: int, reason: str, body: bytes) -> Response:
        """Write a proxy-generated response (m2 promised this). Returns the
        Response object so callers can feed it straight into the access logger.
        Content-Length framing — no keep-alive this milestone."""
        response = Response(
            version="HTTP/1.1", status_code=status, reason=reason,
            headers={"Content-Length": str(len(body)),
                     "Content-Type": "text/plain",
                     "Connection": "close"},
            body=body,
        )
        self.writer.write(self._render_response_head(response) + response.body)
        await self.writer.drain()
        return response

    async def _forward(self, request: Request, backend) -> Response:
        peer_ip = self.writer.get_extra_info("peername")[0]
        async with asyncio.timeout(2):
            up_reader, up_writer = await asyncio.open_connection(backend.host, backend.port)
        try:
            head = encode_request_head(request)
            async with asyncio.timeout(10):
                up_writer.write(head + request.body)
                await up_writer.drain()
            async with asyncio.timeout(30):
                response = await HttpParser.read_response(up_reader)
            async with asyncio.timeout(30):
                self.writer.write(self._render_response_head(response))
                self.writer.write(response.body)
                await self.writer.drain()
            return response
        finally:
            up_writer.close()
            await up_writer.wait_closed()



    @staticmethod
    def _render_response_head(response: Response) -> bytes:
        """Serialize our parsed Response head back into bytes for the client."""
        lines = [f"{response.version} {response.status_code} {response.reason}"]
        lines += [f"{k}: {v}" for k, v in response.headers.items()]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")

