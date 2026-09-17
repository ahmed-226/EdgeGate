from __future__ import annotations
from .config import RouteConfig
from .http import Request
from .upstream import Backend
from .balancer import RoundRobinBalancer, LeastConnectionsBalancer
from .ratelimit import RateLimiter

def _build_balancer(config: RouteConfig, backends: list[Backend]) -> LoadBalancer:
    if config.lb == "least-connections":
        return LeastConnectionsBalancer(backends)
    return RoundRobinBalancer(backends)   

class Route:
    """A configured route = prefix + backends + the rules attached to it.

    Deliberately a plain holder for now. m4 adds self.balancer, m6 adds
    self.limiter, m7 adds self.breaker. ProxyServer wires them in."""
    def __init__(self, config:RouteConfig) -> None:
        self.config = config
        self.backends = [Backend(c) for c in config.backends]
        self.balancer = _build_balancer(config, self.backends)
        self.limiter = RateLimiter() if config.rate_limit is not None else None



class Router:
    """Owns every Route and the two rules: matching and rewriting."""
    def __init__(self, route_configs:list[RouteConfig]) -> None:
        
        self.routes = sorted((Route(c) for c in route_configs),
                             key = lambda r:len(r.config.prefix),reverse=True)

    def match(self, path:str) -> Route | None:
        """Longest-prefix route match.

        "/api/v1/orders" → "/api/v1".../. We use a path segment boundary so
        "/api" does not capture "/apiary". startswith(%/%) is the W3C rule.
        """
        for route in self.routes:
            prefix = route.config.prefix
            if path == prefix or path.startswith(prefix.rstrip("/")+"/"):
                if prefix == "/" or path.startswith(prefix):
                    return route
        return None

    def rewrite_request(self, request: Request, route: Route, backend, client_ip:str) -> None:
        rewritten = Request(
            method=request.method,
            target=request.target,
            version=request.version,
            headers=dict(request.headers),
            body=request.body,
        )
        backend_host = f"{backend.host}:{backend.port}" if backend.port else backend.host
        rewritten.headers["host"]=backend_host

        # OVERWRITE X-Forwarded-For instead of append ot it for security rule ( turst no client )
        rewritten.headers["x-forwarded-for"] = client_ip
        rewritten.headers["x-forwarded-proto"] = "http"
        rewritten.headers["x-real-ip"] = client_ip

        # Strip hop-by-hop headers 
        for h in ("connection", "proxy-connection", "keep-alive",
                  "transfer-encoding", "upgrade"):
            rewritten.headers.pop(h, None)

        return rewritten       