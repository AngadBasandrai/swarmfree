"""MCP Apps resources served as isolated, expiring local documents."""
import base64
import hashlib
import secrets
import time
from .providers import RouteError
from .protocol import validate

MIME = "text/html;profile=mcp-app"
CSP = "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; form-action 'none'; frame-src 'none'; base-uri 'none'; sandbox allow-scripts"


class ToolApps:
    def __init__(self, store, extensions): self.store, self.extensions = store, extensions

    def project(self, project_id, extension_id):
        project = self.store.get("project", project_id)
        config = self.extensions.config(extension_id, "mcp")
        if not project or not config.get("apps_enabled"): raise RouteError("Enable interactive panels for this extension and select a project")
        return project, config

    async def inspect(self, project_id, extension_id, tool_name):
        project, config = self.project(project_id, extension_id)
        tool = next((t for t in config.get("tools", []) if t["name"] == tool_name), None)
        name = "mcp."+extension_id+"."+tool_name
        if not tool or name not in project.get("tools", []): raise RouteError("Grant this discovered tool in the project first")
        uri = tool.get("_meta", {}).get("ui", {}).get("resourceUri")
        if not isinstance(uri, str) or not uri.startswith("ui://") or len(uri) > 500: raise RouteError("This tool has no supported MCP Apps resource")
        result = await self.extensions.mcp(config, "resources/read", dict(uri=uri), project)
        item = next((c for c in result.get("contents", []) if c.get("uri") == uri and c.get("mimeType") == MIME), None)
        if not item: raise RouteError("Server returned no matching MCP Apps HTML resource")
        try: html = item["text"] if "text" in item else base64.b64decode(item["blob"], validate=True).decode("utf-8")
        except (ValueError, KeyError, UnicodeError) as e: raise RouteError("Invalid panel HTML") from e
        if not isinstance(html, str) or len(html.encode()) > 500000: raise RouteError("Panel exceeds 500 KB")
        id = secrets.token_hex(24)
        row = dict(id=id, project_id=project_id, extension_id=extension_id, tool_name=tool_name, uri=uri, html=html,
            sha256=hashlib.sha256(html.encode()).hexdigest(), expires_at=time.time()+1800, calls=0)
        self.store.put("tool_panel", id, row)
        for old in self.store.all("tool_panel"):
            if old["expires_at"] < time.time(): self.store.delete("tool_panel", old["id"])
        return {k:v for k,v in row.items() if k != "html"}

    def panel(self, id):
        row = self.store.get("tool_panel", id)
        if not row or row["expires_at"] < time.time(): raise RouteError("Panel expired; inspect its resource again")
        self.project(row["project_id"], row["extension_id"])
        return row

    async def call(self, id, tool_name, arguments):
        row = self.panel(id); project, config = self.project(row["project_id"], row["extension_id"])
        name = "mcp."+row["extension_id"]+"."+tool_name
        tool = next((t for t in config.get("tools", []) if t["name"] == tool_name), None)
        if not tool or name not in project.get("tools", []): raise RouteError("This panel tool is not granted in the project")
        validate(arguments, tool.get("inputSchema", dict(type="object")))
        with self.store.lock:
            current = self.panel(id)
            if current["calls"] >= 30: raise RouteError("Panel interaction limit reached; open a fresh reviewed panel")
            self.store.patch("tool_panel", id, dict(calls=current["calls"]+1))
        result = await self.extensions.call_tool(name, arguments, project)
        # Record the operation, never secret argument values or arbitrary HTML.
        self.store.event("panel:"+id, "panel_tool", message=tool_name, project_id=project["id"])
        return result
