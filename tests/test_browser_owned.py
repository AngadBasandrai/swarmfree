"""Real owned headless Chromium, using a local TLS fixture instead of the public internet."""
import asyncio
import os
from pathlib import Path
import ssl
from urllib.parse import urlparse
import pytest
from swarm.browser_agents import BrowserAgents,origin_of
from swarm.pairing import generate_certificate
from swarm.providers import RouteError


def test_owned_browser_reads_interacts_blocks_other_sites_and_captures_screenshots(store,tmp_path,monkeypatch):
    asyncio.run(asyncio.wait_for(owned_browser(store,tmp_path,monkeypatch),60))


async def owned_browser(store,tmp_path,monkeypatch):
    from playwright.async_api import async_playwright
    playwright=await asyncio.wait_for(async_playwright().start(),15)
    executable=Path(playwright.chromium.executable_path)
    if not executable.is_file():
        # A cached owned headless shell can be used without downloading anything in this test.
        root=Path(os.environ.get('LOCALAPPDATA',''))/'ms-playwright'
        matches=sorted(root.glob('chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
        if not matches:
            await playwright.stop();pytest.skip('Install Playwright Chromium to run the owned-browser acceptance check')
        executable=matches[-1]
    certificate=generate_certificate(tmp_path,'fixture.test')
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(certificate['cert_file'],certificate['key_file'])
    requests=[]
    async def serve(reader,writer):
        try:
            request=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),3);requests.append(request.decode(errors='replace'))
            body=b'<html><title>Owned fixture</title><body><h1>Browser evidence</h1><input id="answer"><button id="submit" onclick="document.getElementById(\'result\').textContent=document.getElementById(\'answer\').value">Submit</button><p id="result"></p><a id="escape" href="https://blocked.test/">Other site</a></body></html>'
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body);await writer.drain()
        except (asyncio.IncompleteReadError,TimeoutError,ConnectionError): pass
        finally: writer.close()
    server=await asyncio.start_server(serve,'127.0.0.1',0,ssl=tls,ssl_shutdown_timeout=.5,ssl_handshake_timeout=3);port=server.sockets[0].getsockname()[1]
    origin=f'https://fixture.test:{port}'
    original_origin=origin_of
    def fixture_origin(url):
        parsed=urlparse(url)
        if parsed.hostname=='fixture.test' and parsed.port==port: return origin
        return original_origin(url)
    async def fixture_addresses(url):
        assert url==origin;return ['127.0.0.1']
    monkeypatch.setattr('swarm.browser_agents.origin_of',fixture_origin)
    monkeypatch.setattr('swarm.browser_agents.addresses',fixture_addresses)
    launch=playwright.chromium.launch_persistent_context
    async def owned_launch(*args,**kwargs): return await launch(*args,**dict(kwargs,ignore_https_errors=True))
    monkeypatch.setattr(playwright.chromium,'launch_persistent_context',owned_launch)
    store.put('settings','budgets',dict(ram_gb=4,vram_gb=0,context=4096,allow_cpu=True));store.put('settings','browser',dict(executable=str(executable)))
    manager=BrowserAgents(store,tmp_path);manager.playwright=playwright
    project=dict(id='p',browser_sites=[dict(origin=origin,interact=False)])
    run=dict(id='r');agent=dict(id='r:browser')
    try:
        result=await manager.action(project,run,agent,'open',dict(url=origin+'/'))
        assert result['title']=='Owned fixture' and 'Browser evidence' in result['text'] and requests
        with pytest.raises(RouteError,match='read-only'): await manager.action(project,run,agent,'type',dict(selector='#answer',text='hello'))
        with pytest.raises(RouteError,match='granted'): await manager.action(project,run,agent,'open',dict(url='https://blocked.test/'))
        project['browser_sites'][0]['interact']=True
        await manager.action(project,run,agent,'open',dict(url=origin+'/'))
        await manager.action(project,run,agent,'type',dict(selector='#answer',text='Visible agent work'))
        await manager.action(project,run,agent,'click',dict(selector='#submit'))
        assert 'Visible agent work' in (await manager.action(project,run,agent,'read',{}))['text']
        shot=await manager.action(project,run,agent,'screenshot',{})
        path=Path(store.get('browser_action',shot['screenshot_id'])['screenshot']);assert path.read_bytes().startswith(b'\x89PNG')
        assert 'Visible agent work' not in str(store.all('browser_action'))
        # Exercise the actual network guard, not just the top-level URL check.
        _,page=await manager.session(project,run,agent)
        failed=[];page.on('requestfailed',lambda r:failed.append(r.url))
        await page.evaluate("fetch('https://blocked.test/').catch(()=>null)")
        assert failed==['https://blocked.test/']
    finally:
        await manager.stop();server.close()
        if hasattr(server,'abort_clients'): server.abort_clients()
        await asyncio.wait_for(server.wait_closed(),5)
