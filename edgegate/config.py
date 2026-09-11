from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any

@dataclass
class BackendConfig:
    host : str
    port : int

    @classmethod
    def from_string(cls, address: str) -> "BackendConfig":
        host, _, port = address.rpartition(":")
        if not port.isdigit() or not host:
            raise ValueError(f"invalid backend address:{address!r}")
        return cls(host=host, port=int(port))
    
@dataclass
class RateLimitConfig:
    per_ip: int = 20
    per_seconds: int = 10
    burst: int = 5

@dataclass
class CircuitConfig:
    consecutive_failures: int = 5
    cooldown_s : float = 30.0
    halfopen_probes: int = 3

@dataclass
class RouteConfig:
    id: str
    prefix: str
    backends: list[BackendConfig]
    lb: str = "round-robin"
    rate_limit: RateLimitConfig | None = None
    circuit: CircuitConfig= field(default_factory=CircuitConfig)


@dataclass
class HealthConfig:
    path: str = "/healthz"
    interval_s: float = 5.0
    timeout_s: float = 2.0
    unhealthy_threshold: int = 2

@dataclass
class Config:
    listen: str
    routes: list[RouteConfig]
    health: HealthConfig = field(default_factory=HealthConfig)

    @property
    def host(self) -> str:
        return self.listen.split(":",1)[0]

    @property
    def port(self)-> int:
        return int(self.listen.split(":",1)[1])


class ConfigLoader:

    @staticmethod 
    def from_file(path: str) -> Config:
        with open(path, encoding="utf-8") as f:
            raw: dict[str,Any] = json.load(f)

        routes = [ConfigLoader._parse_route(r) for r in raw["routes"]]
        if not routes:
            raise ValueError("config.json must define at least one route")

        return Config(
            listen=raw.get("listen","0.0.0.0:8080"),
            routes=routes,
            health=HealthConfig(**raw.get("health",{})),

        )

    @staticmethod
    def _parse_route(raw: dict[str,Any]) -> RouteConfig:
        rl_raw = raw.get("rate_limit")
        return RouteConfig(
            id=raw["id"],
            prefix=raw["prefix"],
            backends=[BackendConfig.from_string(s) for s in raw["backends"]],
            lb=raw.get("lb","round-robin"),
            rate_limit=RateLimitConfig(**rl_raw) if rl_raw else None,
            circuit=CircuitConfig(**(raw.get("circuit") or {})),
        )