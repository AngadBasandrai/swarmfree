"""Optional TLS ingress exposes worker endpoints, never the desktop owner's API."""
import json
from pathlib import Path
import ssl
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator
from starlette.responses import JSONResponse


class PrivateNetwork(BaseModel):
    enabled: bool = False
    port: int = Field(default=8443, ge=1024, le=65535)
    public_origin: str = Field(default="", max_length=500)
    cert_file: str = Field(default="", max_length=4000)
    key_file: str = Field(default="", max_length=4000)
    advertise_lan: bool = False

    @model_validator(mode="after")
    def validate_origin(self):
        if self.enabled:
            parsed = urlparse(self.public_origin)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError("Enter an HTTPS origin, such as https://my-computer:8443")
            if not self.cert_file or not self.key_file:
                raise ValueError("Choose the TLS certificate and private key")
        self.public_origin = self.public_origin.rstrip("/")
        return self

    def check_certificate(self):
        if self.enabled:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.cert_file, self.key_file)


def load_settings(root: Path) -> PrivateNetwork:
    file = root / "private-network.json"
    return PrivateNetwork.model_validate_json(file.read_text(encoding="utf-8")) if file.exists() else PrivateNetwork()


def save_settings(root: Path, settings: PrivateNetwork):
    settings.check_certificate()
    file = root / "private-network.json"
    temporary = root / "private-network.json.tmp"
    temporary.write_text(json.dumps(settings.model_dump(), indent=2), encoding="utf-8")
    temporary.replace(file)


class WorkerOnly:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(("/api/worker/","/api/team/")):
            await self.app(scope, receive, send)
        else:
            await JSONResponse({"detail": "Private worker endpoint only"}, status_code=404)(scope, receive, send)
