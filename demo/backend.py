"""Dummy API backend for the EdgeGate lab. Replies with a small JSON payload
proving which instance served the request and what the proxy told it.

Run:  python demo/backend.py [--port 3000]
Env:  NAME environment variable → instance id (compose injects it)."""
from __future__ import annotations
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class EchoHandler(BaseHTTPRequestHandler):
    server_version = "EdgeGateDemo/1.0"

    def do_GET(self):                                   
        if self.path == "/healthz":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Echo back what the PROXY showed us. This is how verify asserts header rewriting.
        payload = {
            "instance": os.environ.get("NAME", "unnamed"),
            "path": self.path,
            "client_ip": self.headers.get("X-Real-IP"),
            "xff": self.headers.get("X-Forwarded-For"),
            "host_header": self.headers.get("Host"),
        }
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # Silence the default per-request stderr line — clean docker logs.
    def log_message(self, fmt, *args):
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3000)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), EchoHandler)
    print(f"backend {os.environ.get('NAME','?')} on :{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
