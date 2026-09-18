# EdgeGate

![Python](https://img.shields.io/badge/python-3.12+-blue)
![stdlib](https://img.shields.io/badge/stdlib-only-success)
![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![Docker Compose](https://img.shields.io/badge/Docker%20Compose-2496ED?style=flat&logo=docker&logoColor=white)
![verification](https://img.shields.io/badge/verify-13%20checks%20PASS-brightgreen)

A small Layer 7 HTTP/1.1 reverse proxy built with Python 3.12 and the standard library only (no third-party dependencies). It routes requests to backend services, rewrites headers, balances load, and protects backends with health checks, rate limiting, and a circuit breaker.

## Overview

EdgeGate receives HTTP requests on a single listen address, matches each request path against configured route prefixes, and forwards the request to one of the route's backends. It is built milestone-by-milestone in `docs/code/`, from a raw TCP parser up to a containerized, verifiable proxy. Everything runs on one asyncio event loop, so it is safe, simple, and dependency-free.

## Features

- **Longest-prefix routing** — routes match on path segments (`/api/v1`, `/api`, `/`)
- **Header rewriting** — upstream `Host`, `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Real-IP`
- **Load balancing** — `round-robin` or `least-connections` (slow backends shed load automatically)
- **Active health checks** — probes a `/healthz`-style path and removes unhealthy backends
- **Token-bucket rate limiting** — per-IP limits per route (`429` + `Retry-After`)
- **Circuit breaker** — trips OPEN on repeated upstream failures, serves instant `503`s, half-open probes to recover
- **Observability** — JSON access log per request + a Prometheus-style `/metrics` endpoint
- **Docker lab** — compose stack with two healthy backends and one controllable "chaos" backend
- **`verify.sh`** — repeatable end-to-end verification of the whole story

## How to use

### Prerequisites

- Python 3.12+ (no pip install needed)

### 1. Configure

Edit `config.json`. At minimum, define routes with a prefix and one or more backends:

```json
{
  "listen": "0.0.0.0:8080",
  "routes": [
    {
      "id": "api",
      "prefix": "/api/v1",
      "backends": ["127.0.0.1:3001", "127.0.0.1:3002"],
      "lb": "round-robin"
    }
  ],
  "health": {
    "path": "/healthz",
    "interval_s": 4,
    "timeout_s": 2,
    "unhealthy_threshold": 2
  }
}
```

Per-route options: `lb` (`round-robin` | `least-connections`), `rate_limit` (`per_ip`, `per_seconds`, `burst`), `circuit` (`consecutive_failures`, `cooldown_s`, `halfopen_probes`).

### 2. Run

```bash
python -m edgegate config.json
```

Point your clients at `http://localhost:8080/api/v1/...` — requests are forwarded to one of the configured backends.

### 3. Proxy self-endpoints

- `GET /healthz` → `200 ok` (proxy liveness)
- `GET /metrics` → counter metrics (`edgegate_total_requests`, `edgegate_status.200`, ...)

### Docker lab (optional)

```bash
docker compose up -d --build
curl -s http://localhost:8080/api/v1/products | python -m json.tool   # instance rotates
docker compose logs -f edgegate                                       # JSON access log
```

`demo/backend.py` is a healthy echo backend; `demo/chaos.py` can be made flaky via `FAIL_RATE`/`SLEEP_MS` environment variables.

### Verification

```bash
docker compose down && docker compose up -d --build
bash verify.sh     # assert 13 checks; exits non-zero on any failure
```