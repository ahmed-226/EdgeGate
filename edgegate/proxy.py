from __future__ import annotations
import asyncio
from .config import Config

class ProxyServer:
    def __init__(self, config: Config) -> None:
        self.config = config


    async def handle_client(self,reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        print(f"[echo] connection from {peer}")

        data = await reader.read(100)
        writer.write(b"hello from edgegate, you said: "+ data)
        await writer.drain()

        writer.close()
        await writer.wait_closed()


    async def run(self) -> None:
        host, port = self.config.host, self.config.port
        server = await asyncio.start_server(self.handle_client,host,port)
        async with server:
            print(f"edgegate listening on {host}:{port}")
            await server.serve_forever()
    