from __future__ import annotations
import asyncio
from .config import Config
from .http import Request, Response, HttpParser, encode_request_head

class ProxyServer:
    def __init__(self, config: Config) -> None:
        self.config = config

    async def handle_client(self,reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        print(f"[echo] connection from {peer}")
        
        connection = ProxyConnection(self.config, reader, writer)
        await connection.serve()


    async def run(self) -> None:
        host, port = self.config.host, self.config.port
        server = await asyncio.start_server(self.handle_client,host,port)
        async with server:
            print(f"edgegate listening on {host}:{port}")
            await server.serve_forever()

class ProxyConnection:
    def __init__(self, config: Config,
                 reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.config = config
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
        request = await HttpParser.read_request(self.reader)
        
        route = self.config.routes[0]
        backend = route.backends[0]

        await self._forward(request, backend)

    async def _forward(self, request: Request, backend) -> None:
        peer_ip = self.writer.get_extra_info("peername")[0]   
        
        async with asyncio.timeout(2):       
            up_reader, up_writer = await asyncio.open_connection(backend.host,
                                                                 backend.port)

        try:
            head = encode_request_head(request, host=f"{backend.host}:{backend.port}",
                                       xff=peer_ip)
            async with asyncio.timeout(10):  
                up_writer.write(head + request.body)   
                await up_writer.drain()

            async with asyncio.timeout(30):  
                response = await HttpParser.read_response(up_reader)

            async with asyncio.timeout(30):  
                self.writer.write(self._render_response_head(response))
                self.writer.write(response.body)
                await self.writer.drain()
            
        finally:
            up_writer.close()                
            await up_writer.wait_closed()

    @staticmethod
    def _render_response_head(response: Response) -> bytes:
        """Serialize our parsed Response head back into bytes for the client."""
        lines = [f"{response.version} {response.status_code} {response.reason}"]
        lines += [f"{k}: {v}" for k, v in response.headers.items()]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")