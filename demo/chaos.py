"""Chaos backend: behaves for verification, misbehaves on command.

Env knobs:
  FAIL_RATE  float  → respond 500 with this probability (default 0)
  SLEEP_MS   int    → hang before answering (default 0)
Toggling env + restart is how verify.sh 'breaks' a backend (M9)."""
from __future__ import annotations
import argparse
import os
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ChaosHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            # /healthz IS what the health checker probes. To simulate a backend the checker considers DEAD, fail the healthz too with FAIL_RATE.
            if random.random() < float(os.environ.get("FAIL_RATE", "0")):
                self.send_response(503)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        sleep_ms = int(os.environ.get("SLEEP_MS", "0"))
        if sleep_ms:
            # 'hangs' — breaker timeout food
            time.sleep(sleep_ms / 1000.0)          

        if random.random() < float(os.environ.get("FAIL_RATE", "0")):
            self.send_response(500)
            self.send_header("Content-Length", "9")
            self.end_headers()
            self.wfile.write(b"chaos 500")
            return

        body = b'{"instance":"chaos","status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3000)
    args = ap.parse_args()
    print(f"chaos {os.environ.get('NAME','?')} fail={os.environ.get('FAIL_RATE')} "
          f"sleep={os.environ.get('SLEEP_MS')}")
    ThreadingHTTPServer(("0.0.0.0", args.port), ChaosHandler).serve_forever()

if __name__ == "__main__":
    main()