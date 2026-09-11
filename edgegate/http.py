from __future__ import annotations
import asyncio
from dataclasses import dataclass, field


@dataclass
class Request:
    """A parsed HTTP request.

    headers: dict[str, str]  — keys ALWAYS lowercase (case-insensitive grammar,
    tutorial 02), first value wins per RFC (duplicate headers are joined with
    ", " in simple proxies; we keep simple: join with comma).
    """
    method: str                     
    target: str                     
    version: str                    
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""               
                                    

    @property
    def host(self) -> str:
        """Convenience: the Host header the client sent (lowercased lookup)."""
        return self.headers.get("host", "")


@dataclass
class Response:
    version: str                    
    status_code: int                
    reason: str                     
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


class HttpParser:
    """Stateless reader class: instance-free, @staticmethod everywhere.

    Why a class and not a module of functions? Grouping, and a home for the
    grammar CONFIG that might grow (e.g. max header bytes in m9) without
    threading parameters through every function.
    """

    HEAD_END = b"\r\n\r\n"    

    @staticmethod
    async def read_line(reader: asyncio.StreamReader) -> bytes:
        """Read ONE \r\n-terminated line INCLUDING the terminator."""
        return await reader.readuntil(b"\r\n")     

    @staticmethod
    async def read_headers(reader: asyncio.StreamReader) -> tuple[bytes, dict[str, str]]:
        """Read the raw header block + parse it into lowercase keys.

        Returns (raw_block, headers). Later milestones (keep-alive, m9) need the
        raw block to re-serialize to upstream; returning it now avoids
        re-reading.
        """
        raw = await reader.readuntil(HttpParser.HEAD_END)    
        headers: dict[str, str] = {}

        for line in raw.split(b"\r\n"):
            if not line:
                continue
            name_b, _, value_b = line.partition(b":")        
            name = name_b.decode("ascii").strip().lower()    
            value = value_b.strip().decode("ascii")
            if name in headers:
                headers[name] += ", " + value               
            else:
                headers[name] = value
        return raw, headers

    @staticmethod
    async def read_request(reader: asyncio.StreamReader, max_body: int = 1_000_000) -> Request:
        """Parse one full request from a connected reader.

        Returns Request with body fully read into memory.
        """
        
        first_line = (await HttpParser.read_line(reader)).rstrip(b"\r\n")
        method_b, sep, rest = first_line.partition(b" ")
        if not sep:
            raise ValueError(f"malformed request line: {first_line!r}")
        target_b, _, version_b = rest.partition(b" ")

        
        _, headers = await HttpParser.read_headers(reader)
        
        body = b""
        cl = headers.get("content-length")
        if cl is not None:
            length = int(cl)
            if length > max_body:
                raise ValueError("request body too large")
            
            body = await reader.readexactly(length)

        return Request(
            method=method_b.decode("ascii"),
            target=target_b.decode("ascii"),
            version=version_b.decode("ascii"),
            headers=headers,
            body=body,
        )

    @staticmethod
    async def read_response(reader: asyncio.StreamReader, max_body: int = 1_000_000) -> Response:
        """Parse one full response from an upstream reader (mirror of above)."""
        status_b = (await HttpParser.read_line(reader)).rstrip(b"\r\n")
        version_b, _, rest = status_b.partition(b" ")        
        code_b, _, reason_b = rest.partition(b" ")
        _, headers = await HttpParser.read_headers(reader)

        body = b""
        cl = headers.get("content-length")
        if cl is not None:
            if int(cl) > max_body:
                raise ValueError("response body too large")
            body = await reader.readexactly(int(cl))
        return Response(
            version=version_b.decode("ascii"),
            status_code=int(code_b.decode("ascii")),
            reason=reason_b.decode("ascii").strip(),
            headers=headers,
            body=body,
        )


def encode_request_head(request: Request, *, host: str, xff: str) -> bytes:
    """Render a request line + headers, with the two rewrites a proxy MUST do
    (Host and X-Forwarded-For — full rewrite rules in tutorial 10, m3).

    host: the upstream "host:port" it should target.
    xff:  the client IP to report upstream.
    """
    lines = [f"{request.method} {request.target} {request.version}"]
    out_headers = dict(request.headers)
    out_headers["host"] = host
    if "x-forwarded-for" in out_headers:
        out_headers["x-forwarded-for"] += f", {xff}"        
    else:
        out_headers["x-forwarded-for"] = xff
    out_headers["x-forwarded-proto"] = "http"
    lines += [f"{k}: {v}" for k, v in out_headers.items()]
    
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")




