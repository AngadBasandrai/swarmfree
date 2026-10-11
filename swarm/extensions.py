"""Opt-in MCP HTTP/stdio tools and bounded ACP stdio agent tasks."""
import asyncio
import json
import os
import httpx
import psutil
from .providers import RouteError, validate_base_url


class StdioRPC:
    def __init__(self, argv, cwd):
        self.argv, self.cwd, self.serial = argv, cwd, 0
        self.notifications = []

    async def __aenter__(self):
        options = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
        self.process = await asyncio.create_subprocess_exec(*self.argv, cwd=self.cwd, limit=2_000_000,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, **options)
        return self

    async def send(self, method, params=None, notify=False, on_notification=None):
        self.serial += 1
        request = dict(jsonrpc="2.0", method=method, params=params or {})
        if not notify: request["id"] = self.serial
        self.process.stdin.write((json.dumps(request)+"\n").encode())
        await self.process.stdin.drain()
        if notify: return None
        async with asyncio.timeout(120):
            while True:
                line = await self.process.stdout.readline()
                if not line: raise RouteError("Extension closed its protocol connection")
                message = json.loads(line)
                if message.get("id") == request["id"] and "method" not in message:
                    if "error" in message: raise RouteError("Extension rejected the protocol request")
                    return message.get("result", {})
                if "id" in message and "method" in message:
                    # This client grants no implicit filesystem, terminal, sampling or approvals.
                    if message["method"] == "session/request_permission":
                        reply = {"result": {"outcome": {"outcome": "cancelled"}}}
                    else:
                        reply = {"error": {"code": -32601, "message": "Client capability is not granted"}}
                    self.process.stdin.write((json.dumps(dict(jsonrpc="2.0", id=message["id"], **reply))+"\n").encode())
                    await self.process.stdin.drain()
                else:
                    self.notifications.append(message)
                    if len(self.notifications) > 10000: raise RouteError("Extension notification limit reached")
                    if on_notification: on_notification(message)

    async def __aexit__(self, *_):
        if self.process.returncode is None:
            try:
                process = psutil.Process(self.process.pid)
                for child in process.children(recursive=True):
                    try: child.kill()
                    except psutil.Error: pass
                process.kill()
            except psutil.Error: pass
            await self.process.wait()


class Extensions:
    def __init__(self, store, vault):
        self.store, self.vault = store, vault

    def config(self, id, kind=None):
        value = self.store.get("extension", id)
        if not value or not value["enabled"] or kind and value["kind"] != kind:
            raise RouteError("Enable and configure this extension first")
        return value

    async def mcp(self, config, method, params, project):
        initialization = dict(protocolVersion="2025-11-25", capabilities={}, clientInfo=dict(name="Swarm Workbench", version="0.2.0"))
        if config["transport"] == "stdio":
            async with StdioRPC(config["argv"], project["root"]) as rpc:
                result = await rpc.send("initialize", initialization)
                if result.get("protocolVersion") not in {"2025-11-25", "2025-06-18", "2024-11-05"}:
                    raise RouteError("MCP server requires an unsupported protocol revision")
                await rpc.send("notifications/initialized", notify=True)
                return await rpc.send(method, params)
        validate_base_url(config["url"])
        headers = {"Accept": "application/json, text/event-stream"}
        key = self.vault.get("extension_" + config["id"])
        if key: headers["Authorization"] = "Bearer " + key
        async with httpx.AsyncClient(timeout=45, follow_redirects=False, trust_env=False) as client:
            async def request(payload):
                async with client.stream("POST",config["url"],json=payload,headers=headers) as response:
                    response.raise_for_status()
                    if response.status_code == 202: return {}
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content)+len(chunk) > 2_000_000: raise RouteError("MCP response exceeds the limit")
                        content.extend(chunk)
                    response_headers = response.headers.copy()
                if "text/event-stream" in response_headers.get("content-type", ""):
                    messages = [json.loads(line[5:].strip()) for line in content.decode("utf-8").splitlines() if line.startswith("data:")]
                    data = next((m for m in messages if m.get("id") == payload.get("id")), {})
                else: data = json.loads(content)
                if "id" in payload and data.get("id") != payload["id"]: raise RouteError("MCP response does not match its request")
                if "error" in data: raise RouteError("MCP server rejected the request")
                return data.get("result", {}), response_headers
            initialized, response_headers = await request(dict(jsonrpc="2.0", id=1, method="initialize", params=initialization))
            revision = initialized.get("protocolVersion")
            if revision not in {"2025-11-25", "2025-06-18", "2024-11-05"}:
                raise RouteError("MCP server requires an unsupported protocol revision")
            headers["MCP-Protocol-Version"] = revision
            session = response_headers.get("mcp-session-id")
            if session: headers["Mcp-Session-Id"] = session
            try:
                await request(dict(jsonrpc="2.0", method="notifications/initialized"))
                result, _ = await request(dict(jsonrpc="2.0", id=2, method=method, params=params))
                return result
            finally:
                if session:
                    try: await client.delete(config["url"], headers=headers)
                    except httpx.HTTPError: pass

    async def discover(self, id, project):
        config = self.config(id, "mcp")
        result = await self.mcp(config, "tools/list", {}, project)
        tools = result.get("tools", [])[:100]
        return self.store.patch("extension", id, dict(tools=tools))

    async def call_tool(self, name, arguments, project):
        _, id, tool = name.split(".", 2)
        config = self.config(id, "mcp")
        if not any(t["name"] == tool for t in config.get("tools", [])):
            raise RouteError("Discover and explicitly grant this server tool first")
        return await self.mcp(config, "tools/call", dict(name=tool, arguments=arguments), project)

    async def agent_stream(self, id, project, messages):
        config = self.config(id, "acp")
        async with StdioRPC(config["argv"], project["root"]) as rpc:
            initialized = await rpc.send("initialize", dict(protocolVersion=1, clientCapabilities={}, clientInfo=dict(name="Swarm Workbench", version="0.2.0")))
            if initialized.get("protocolVersion") != 1:
                raise RouteError("ACP agent requires an unsupported protocol revision")
            session = await rpc.send("session/new", dict(cwd=project["root"], mcpServers=[]))
            chunks = asyncio.Queue()
            def receive(notification):
                update = notification.get("params", {}).get("update", {})
                if update.get("sessionUpdate") == "agent_message_chunk" and update.get("content", {}).get("type") == "text":
                    chunks.put_nowait(update["content"]["text"])
            async def prompt():
                try:
                    return await rpc.send("session/prompt", dict(sessionId=session["sessionId"], prompt=[dict(type="text", text="\n\n".join(m["role"] + ": " + m["content"] for m in messages))]), on_notification=receive)
                finally:
                    chunks.put_nowait(None)
            pending = asyncio.create_task(prompt())
            try:
                while (chunk := await chunks.get()) is not None:
                    yield chunk
                await pending
            finally:
                if not pending.done(): pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
