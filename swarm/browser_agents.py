"""Headless browser processes owned by the workbench, with exact-origin grants."""
import asyncio
import ipaddress
import os
from pathlib import Path
import socket
import time
from urllib.parse import urlparse
from .providers import RouteError
from .store import uid


def origin_of(url):
    parsed=urlparse(url)
    if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None,443}:
        raise RouteError("Browser grants require an HTTPS origin on port 443")
    return "https://"+parsed.hostname.lower()


async def addresses(origin):
    hostname=urlparse(origin).hostname
    found=await asyncio.to_thread(socket.getaddrinfo,hostname,443,0,socket.SOCK_STREAM)
    ips=sorted({row[4][0] for row in found})
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips): raise RouteError("Browser agents cannot access local or private networks")
    return ips


class BrowserAgents:
    def __init__(self,store,root):
        self.store,self.root=store,Path(root)
        self.playwright=None;self.sessions={};self.locks={};self.start_lock=asyncio.Lock()
        for row in store.all("browser_session"):
            if row.get("status")=="open": store.patch("browser_session",row["id"],dict(status="closed"))

    async def session(self,project,run,agent):
        grants=project.get("browser_sites",[])
        if not grants: raise RouteError("Grant exact browser site origins for this project first")
        existing=self.sessions.get(agent["id"])
        signature=str(grants)
        if existing and existing[2]==signature: return existing[:2]
        async with self.start_lock:
            if existing: await self.close(agent["id"])
            if len(self.sessions)>=2: raise RouteError("Two browser agents are already open; close one before opening another")
            from .hardware import hardware
            budgets=self.store.get("settings","budgets")
            if min(budgets["ram_gb"],hardware()["ram_available_gb"])<1: raise RouteError("Browser agents need at least 1 GiB available within the memory allocation")
            rules=[]
            allowed={origin_of(g["origin"]):g for g in grants}
            for origin in allowed:
                ips=await addresses(origin);host=urlparse(origin).hostname
                rules.append("MAP "+host+" "+ips[0])
            from playwright.async_api import async_playwright,Error
            if self.playwright is None: self.playwright=await async_playwright().start()
            import hashlib
            profile=self.root/"browser-profiles"/hashlib.sha256(agent["id"].encode()).hexdigest()[:24]
            settings=self.store.get("settings","browser",{})
            executable=settings.get("executable") or None
            try:
                context=await self.playwright.chromium.launch_persistent_context(str(profile),headless=True,
                    executable_path=executable,accept_downloads=False,service_workers="block",permissions=[],
                    args=["--no-proxy-server","--disable-background-networking","--host-resolver-rules="+", ".join(rules+["MAP * ~NOTFOUND"])])
            except Error as error: raise RouteError("Headless Chromium is unavailable. Install its runtime in the Browser panel or select a trusted executable.") from error
            async def guard(route):
                try:
                    origin=origin_of(route.request.url);grant=allowed.get(origin)
                    if not grant or route.request.method not in ({"GET","HEAD"} if not grant.get("interact") else {"GET","HEAD","POST","PUT","PATCH","DELETE","OPTIONS"}):
                        await route.abort();return
                    # Connections use the fixed checked address from launch, including redirects.
                    await route.continue_()
                except Exception:
                    try: await route.abort()
                    except Exception: pass
            await context.route("**/*",guard)
            await context.route_web_socket("**/*",lambda ws:ws.close())
            context.on("page",lambda page:asyncio.create_task(page.close()) if context.pages and page!=context.pages[0] else None)
            page=context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(10000)
            page.on("dialog",lambda dialog:asyncio.create_task(dialog.dismiss()))
            self.sessions[agent["id"]]=(context,page,signature)
            self.store.put("browser_session",agent["id"],dict(id=agent["id"],agent_id=agent["id"],run_id=run["id"],project_id=project["id"],status="open",url="about:blank",profile=str(profile),created_at=time.time()))
            return context,page

    async def action(self,project,run,agent,kind,args):
        lock=self.locks.setdefault(agent["id"],asyncio.Lock())
        async with lock:
            context,page=await self.session(project,run,agent)
            grant={origin_of(g["origin"]):g for g in project.get("browser_sites",[])}
            id=uid("browser_")
            record=dict(id=id,agent_id=agent["id"],run_id=run["id"],kind=kind,url=page.url,selector=str(args.get("selector",""))[:500],status="running",created_at=time.time())
            self.store.put("browser_action",id,record)
            try:
                if kind=="open":
                    if origin_of(args["url"]) not in grant: raise RouteError("This site has not been granted to this browser agent")
                    await page.goto(args["url"],wait_until="domcontentloaded",timeout=20000)
                elif kind in {"click","type"}:
                    if not grant.get(origin_of(page.url),{}).get("interact"): raise RouteError("This site has read-only browser permission")
                    selector=str(args["selector"])
                    if len(selector)>500: raise RouteError("Selector exceeds the limit")
                    locator=page.locator(selector)
                    if await locator.count()!=1: raise RouteError("Selector must identify exactly one element")
                    if kind=="click": await locator.click(no_wait_after=True)
                    else: await locator.fill(str(args["text"])[:12000])
                elif kind not in {"read","screenshot"}: raise RouteError("Unknown browser action")
                await asyncio.sleep(.15)
                if page.url!="about:blank" and origin_of(page.url) not in grant: raise RouteError("Navigation left the granted sites")
                result=dict(url=page.url,title=await page.title())
                if kind=="screenshot":
                    folder=self.root/"browser-shots";folder.mkdir(exist_ok=True)
                    path=folder/(id+".png")
                    await page.screenshot(path=str(path),full_page=False)
                    self.store.patch("browser_action",id,dict(screenshot=str(path)))
                    result["screenshot_id"]=id
                else:
                    result["text"]=(await page.locator("body").inner_text())[:18000]
                    result["controls"]=await page.locator("a,button,input,textarea,select").evaluate_all("els => els.slice(0,60).map(el => ({tag:el.tagName.toLowerCase(),id:el.id,name:el.getAttribute('name'),text:(el.innerText||el.getAttribute('aria-label')||'').slice(0,120),href:el.getAttribute('href')}))")
                self.store.patch("browser_session",agent["id"],dict(url=page.url))
                self.store.patch("browser_action",id,dict(status="completed",url=page.url,title=result["title"],finished_at=time.time()))
                return dict(action_id=id,**result)
            except Exception as error:
                message=str(error) if isinstance(error,RouteError) else "Browser action failed or timed out; inspect the owned session"
                self.store.patch("browser_action",id,dict(status="failed",error=message));raise RouteError(message) from error

    async def close(self,id):
        session=self.sessions.pop(id,None)
        if session: await session[0].close()
        if self.store.get("browser_session",id): self.store.patch("browser_session",id,dict(status="closed"))

    async def stop(self):
        for id in list(self.sessions): await self.close(id)
        if self.playwright: await self.playwright.stop();self.playwright=None

    async def install(self):
        # Executed only following the owner's explicit Install runtime action.
        from playwright._impl._driver import compute_driver_executable,get_driver_env
        executable,cli=compute_driver_executable()
        # Playwright's bundled node CLI works in source and frozen engines alike.
        env=get_driver_env()
        options={"creationflags":0x08000000} if os.name=="nt" else {}
        process=await asyncio.create_subprocess_exec(executable,cli,"install","chromium","--only-shell",env=env,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,**options)
        try:
            await asyncio.wait_for(process.wait(),240)
            if process.returncode: raise RouteError("Headless browser download failed")
        finally:
            if process.returncode is None: process.kill();await process.wait()
