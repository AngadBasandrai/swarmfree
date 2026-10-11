"""Public web retrieval pins DNS results and records source provenance."""
import asyncio
from html.parser import HTMLParser
import http.client
import io
import ipaddress
import json
import socket
import ssl
import time
from urllib.parse import urlparse, urljoin
import httpx
from .providers import RouteError, validate_base_url
from .store import uid
from .workspace import scoped_path, sha


def public_fetch(url, redirects=0):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.port not in {None, 443}:
        raise RouteError("Page retrieval requires a public HTTPS URL on port 443")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    ips = {item[4][0] for item in addresses}
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise RouteError("Page retrieval cannot access local/private networks")
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=15, context=ssl.create_default_context())
    # Pin a checked address while retaining the original hostname for TLS validation.
    connection._create_connection = lambda address, timeout, source_address=None: socket.create_connection((sorted(ips)[0], 443), timeout, source_address)
    try:
        connection.request("GET", (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""), headers={"User-Agent": "SwarmWorkbench/0.2", "Accept": "text/html,text/plain,application/pdf"})
        response = connection.getresponse()
        if response.status in {301, 302, 303, 307, 308} and redirects < 3:
            location = response.getheader("Location")
            if location:
                return public_fetch(urljoin(url, location), redirects+1)
        if response.status != 200:
            raise RouteError(f"Source returned HTTP {response.status}; redirects are not followed")
        data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise RouteError("Source exceeds the 2 MB retrieval limit")
        return data, response.getheader("Content-Type", "")
    finally:
        connection.close()


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}: self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}: self.skip = max(0, self.skip-1)

    def handle_data(self, data):
        if not self.skip and data.strip(): self.parts.append(data.strip())


def pdf_text(data):
    from pypdf import PdfReader, apply_configuration
    with apply_configuration(zlib_maximum_output_length=2_000_000, lzw_maximum_output_length=2_000_000,
            array_based_stream_maximum_output_length=2_000_000, maximum_declared_stream_length=2_000_000,
            stream_decoding_work_maximum_length=8_000_000):
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or len(reader.pages) > 100:
            raise RouteError("PDF must be unencrypted and at most 100 pages")
        parts, length = [], 0
        for i, page in enumerate(reader.pages):
            text = f"[Page {i+1}]\n{page.extract_text() or ''}"
            parts.append(text[:100000-length])
            length += len(text)
            if length >= 100000: break
        return "\n\n".join(parts)


async def research_tool(store, project, run, agent, tool, args):
    if tool == "research.search":
        endpoint = project.get("search_url", "")
        if not endpoint:
            raise RouteError("Configure a SearXNG JSON search endpoint in the project first")
        validate_base_url(endpoint)
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
            response = await client.get(endpoint.rstrip("/") + "/search", params={"q": str(args["query"])[:500], "format": "json"})
            response.raise_for_status()
            return {"results": [{"title": str(item.get("title", ""))[:200], "url": item.get("url"),
                "excerpt": str(item.get("content", ""))[:1000]} for item in response.json().get("results", [])[:10]], "verified": False}
    if tool == "research.pdf":
        file = scoped_path(project, args["path"])
        if file.stat().st_size > 2_000_000:
            raise RouteError("PDF exceeds the 2 MB extraction limit")
        text = await asyncio.to_thread(pdf_text, file.read_bytes())
        source = "project:" + args["path"]
    else:
        data, content_type = await asyncio.to_thread(public_fetch, str(args["url"]))
        source = args["url"]
        if "application/pdf" in content_type:
            text = await asyncio.to_thread(pdf_text, data)
        elif "text/" in content_type:
            parser = VisibleText()
            parser.feed(data.decode("utf-8", errors="replace"))
            text = "\n".join(parser.parts)[:100000]
        else:
            raise RouteError("Source is not HTML, text or PDF")
    id = uid("source_")
    record = dict(id=id, project_id=project["id"], run_id=run["id"], agent_id=agent["id"],
        url=source, retrieved_at=time.time(), content_hash=sha(text), content=text, verified=False)
    store.put("source", id, record)
    return {"source_id": id, "url": source, "retrieved_at": record["retrieved_at"], "text": text[:18000],
        "citation": f"[{id}]({source})", "note": "Retrieved source text; retrieval does not establish that its claims are true."}
