from __future__ import annotations
import argparse
import asyncio
import logging
from .config import ConfigLoader
from .proxy import ProxyServer


def main() -> int:
    p = argparse.ArgumentParser(prog="edgegate")
    p.add_argument("config", nargs="?", default="config.json")
    args = p.parse_args()

    config = ConfigLoader.from_file(args.config)
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    server = ProxyServer(config)
    asyncio.run(server.run())

    return 0

if __name__ == "__main__":
    raise SystemExit(main())