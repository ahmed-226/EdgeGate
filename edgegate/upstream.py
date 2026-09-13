from __future__ import annotations
from .config import BackendConfig

class Backend:
    def __init__(self,config:BackendConfig) -> None:
        self.config=config
        self.host = config.host
        self.port = config.port
        self.healthy = True
        self.live = 0

    @property
    def address(self) ->str:
        return f"{self.host}:{self.port}"

    def __repr__(self) -> str:
        return f"<Backend {self.address} healthy={self.healthy} live={self.live}>"
    