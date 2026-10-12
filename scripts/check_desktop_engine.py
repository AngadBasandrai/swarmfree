"""Smoke-test the frozen executable in isolation, including native lifetime and state."""
import base64
import json
import os
from pathlib import Path
import subprocess
import queue
import socket
import ssl
import struct
import sys
import tempfile
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    binary = Path(sys.argv[1]).resolve()
    creationflags = 0x08000000 if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="swarm-desktop-test-") as folder:
        data = Path(folder) / "state"
        public_port = None
        context = None
        if len(sys.argv) > 3:
            cert, key = Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                public_port = probe.getsockname()[1]
            data.mkdir()
            (data / "private-network.json").write_text(json.dumps(dict(enabled=True, port=public_port,
                public_origin=f"https://127.0.0.1:{public_port}", cert_file=str(cert), key_file=str(key))), encoding="utf-8")
            context = ssl.create_default_context(cafile=str(cert))
        def start():
            process = subprocess.Popen([str(binary), "--data-dir", str(data)], cwd=folder,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, creationflags=creationflags)
            try:
                output = queue.Queue()
                threading.Thread(target=lambda: output.put(process.stdout.readline()), daemon=True).start()
                line = output.get(timeout=50)
                if not line:
                    raise RuntimeError(process.stderr.read())
                ready = json.loads(line)
                assert ready["event"] == "ready"
                return process, ready
            except BaseException:
                process.kill()
                process.wait(timeout=10)
                raise

        def request(ready, path, method="GET", body=None, auth=True):
            headers = {"Authorization": "Bearer " + ready["token"]} if auth else {}
            if body is not None:
                headers["Content-Type"] = "application/json"
            req = Request(f"http://127.0.0.1:{ready['port']}{path}", method=method, headers=headers,
                          data=None if body is None else json.dumps(body).encode())
            with urlopen(req, timeout=30) as response:
                return json.load(response)

        process, ready = start()
        try:
            for path, method, body, expected in [("/", "GET", None, 404),
                    ("/api/snapshot", "GET", None, 401), ("/api/session", "POST", {}, 401)]:
                try:
                    request(ready, path, method, body, auth=False)
                    raise AssertionError("Unauthenticated endpoint unexpectedly succeeded")
                except HTTPError as error:
                    assert error.code == expected
            snapshot = request(ready, "/api/snapshot")
            assert request(ready, "/api/health")["version"] == "0.6.2"
            assert snapshot["hardware"]["ram_total_gb"] > 0
            assert snapshot["architectures"]
            features = request(ready,"/api/features")
            assert "research.pdf" in features["tools"] and len(snapshot["architectures"]) >= 10
            project = request(ready,"/api/projects","POST",dict(name="Frozen project",root=folder,tools=["memory.search"],checks={}))
            request(ready,"/api/projects/"+project["id"]+"/memory","POST",dict(title="Frozen decision",content="Keep portable context"))
            workbench=request(ready,"/api/workbench")
            assert workbench["setup"]["hardware"]["ram_total_gb"]>0 and workbench["registry"]["keys"]
            draft=request(ready,"/api/workbench/architectures/design","POST",dict(description="A planner, researcher and reviewer in parallel"))
            architecture=request(ready,"/api/workbench/architectures/save","POST",dict(architecture=draft["architecture"]))
            assert len(architecture["nodes"])==4 and not architecture["builtin"]
            policy=request(ready,"/api/workbench/policies/"+project["id"],"PUT",dict(destinations=["local","private"],file_rules=[],scan_secrets=True))
            assert "cloud" not in policy["destinations"]
            # The later owned provider fixture is deliberately classified as cloud.
            request(ready,"/api/workbench/policies/"+project["id"],"PUT",dict(destinations=["local","private","cloud"],file_rules=[],scan_secrets=True))
            member=request(ready,"/api/workbench/members","POST",dict(name="Frozen viewer",role="viewer",project_ids=[project["id"]]))
            team=request(dict(ready,token=member["token"]),"/api/team/snapshot")
            assert len(team["projects"])==1 and "root" not in team["projects"][0]
            request(ready,"/api/workbench/members/"+member["member"]["id"]+"/revoke","POST")
            simulation=request(ready,"/api/workbench/faults","POST",dict(scenarios=["stream_disconnect","worker_disconnect"]))
            deadline = time.monotonic() + 75
            while time.monotonic() < deadline:
                simulation=next(j for j in request(ready,"/api/workbench")["faults"] if j["id"]==simulation["id"])
                if simulation["status"]!="running": break
                time.sleep(.1)
            assert simulation["status"]=="completed" and simulation["passed"],simulation
            certificate = request(ready,"/api/pairing/certificate","POST",dict(hostname="127.0.0.1"))
            assert Path(certificate["cert_file"]).is_file() and Path(certificate["key_file"]).is_file()
            if public_port:
                assert snapshot["private_network"]["enabled"] is True, snapshot["private_network"]
                for path in ("/api/snapshot", "/api/health", "/"):
                    try:
                        urlopen(f"https://127.0.0.1:{public_port}{path}", context=context, timeout=10)
                        raise AssertionError("TLS ingress exposed an owner endpoint")
                    except HTTPError as error:
                        assert error.code == 404
                invitation = request(ready, "/api/devices/invite", "POST")["token"]
                body = dict(invite=invitation, name="Frozen TLS worker", hardware={}, models=[],
                    budgets=dict(ram_gb=4, vram_gb=0, context=4096, allow_cpu=True))
                req = Request(f"https://127.0.0.1:{public_port}/api/worker/join", data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"})
                with urlopen(req, context=context, timeout=10) as response:
                    assert json.load(response)["device_id"]
                phone = request(ready, '/api/ops/companion/pair', 'POST', dict(name='Frozen phone',role='viewer',project_ids=[project['id']]))
                bundle = phone['bundle']
                redeem = Request(f'https://127.0.0.1:{public_port}/api/team/companion/redeem',data=json.dumps(dict(invite_id=bundle['invite_id'],invite=bundle['invite'])).encode(),headers={'Content-Type':'application/json'})
                with urlopen(redeem,context=context,timeout=10) as response:
                    credential = json.load(response)
                try:
                    urlopen(redeem,context=context,timeout=10)
                    raise AssertionError('One-use phone invitation was accepted twice')
                except HTTPError as error:
                    assert error.code == 401
                phone_snapshot = Request(f'https://127.0.0.1:{public_port}/api/team/snapshot',headers={'Authorization':'Bearer '+credential['token']})
                with urlopen(phone_snapshot,context=context,timeout=10) as response:
                    shared=json.load(response)
                    assert len(shared['projects'])==1 and 'root' not in shared['projects'][0]
                request(ready,'/api/workbench/members/'+phone['member']['id']+'/revoke','POST')
                try:
                    urlopen(phone_snapshot,context=context,timeout=10)
                    raise AssertionError('Revoked phone credential was accepted')
                except HTTPError as error:
                    assert error.code == 401
            run = request(ready, "/api/runs", "POST", dict(task="Verify the bundled desktop engine",
                architecture_id="parallel", preset="general", mode="demo", free_only=True))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                run = request(ready, "/api/runs/" + run["id"])
                if run["status"] == "completed": break
                time.sleep(.2)
            assert run["status"] == "completed", run["status"]
            assert all(agent["result"] for agent in run["agents"])
            # Frozen 0.4 dependencies and model registration, without downloading weights.
            assert len(request(ready, "/api/next/catalog")) >= 6
            assert request(ready, "/api/next/browser/status")["driver_available"]
            gguf = Path(folder) / "header-fixture.gguf"
            gguf.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 1, 0))
            runtime = request(ready, "/api/next/runtimes", "POST", dict(name="Header fixture",
                path=str(gguf), server_path=str(binary), trusted=True, license="Fixture only"))
            assert runtime["gguf_version"] == 3 and runtime["status"] == "stopped"
            request(ready, "/api/next/runtimes/" + runtime["id"], "DELETE")
            evaluation = request(ready, "/api/next/evaluations", "POST", dict(name="Frozen repeatable evaluation",
                tasks=[dict(task="Frozen verification", checks=[dict(kind="contains", value="Frozen verification")])],
                variants=[dict(name="Small team", architecture_id="baseline")],
                mission=dict(mode="demo", free_only=True)))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                evaluation = next(row for row in request(ready, "/api/next")["evaluations"] if row["id"] == evaluation["id"])
                if evaluation["status"] != "running": break
                time.sleep(.2)
            assert evaluation["status"] == "completed", evaluation
            assert evaluation["recommendation"]["name"] == "Small team", evaluation
            # Exercise the new modules from the frozen distribution, without live models.
            assert request(ready, "/api/advanced")["conversion_jobs"] == []
            assert request(ready, "/api/runs/" + run["id"] + "/budget")["requests"] > 0
            preview_context = request(ready, "/api/agents/" + run["agents"][0]["id"] + "/context")
            assert preview_context["messages"]
            package = dict(name="Frozen portable swarm", architecture=snapshot["architectures"][0])
            # Portable schemas deliberately exclude storage-only builtin metadata.
            package["architecture"].pop("builtin", None)
            preview = request(ready, "/api/packages/preview", "POST", package)
            request(ready, "/api/packages/import", "POST", dict(package=package, reviewed_sha256=preview["sha256"]))
            from PIL import Image
            import io
            image = io.BytesIO()
            Image.new("RGB", (24, 24), "red").save(image, format="PNG")
            attachment = request(ready, "/api/attachments", "POST", dict(name="frozen.png", mime="image/png", base64=base64.b64encode(image.getvalue()).decode()))
            assert attachment["kind"] == "image"
            assert request(ready, "/api/pairing/qr", "POST", dict(bundle="Private fixture pairing"))["image"].startswith("data:image/svg+xml;base64,")
            # An owned loopback fixture exercises bundled PDF extraction through a real tool turn.
            from pypdf import PdfWriter
            from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
            writer = PdfWriter()
            page = writer.add_blank_page(width=400,height=400)
            font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            contents = DecodedStreamObject()
            contents.set_data(b'BT /F1 12 Tf 20 20 Td (Frozen PDF evidence) Tj ET')
            page[NameObject('/Contents')] = writer._add_object(contents)
            writer.write(Path(folder)/'evidence.pdf')
            class Fixture(BaseHTTPRequestHandler):
                calls = 0
                def log_message(self,*args): pass
                def do_POST(self):
                    payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    Fixture.calls += 1
                    if Fixture.calls == 1:
                        assert payload['tools']
                        function = next(tool['function']['name'] for tool in payload['tools'] if tool['function']['name'].endswith('pdf'))
                        delta = dict(tool_calls=[dict(index=0,id='frozen-call',type='function',function=dict(name=function,arguments=json.dumps(dict(path='evidence.pdf'))))])
                    else:
                        assert any(message['role']=='tool' and message['tool_call_id']=='frozen-call' for message in payload['messages'])
                        delta = dict(content='Frozen PDF tool completed')
                    body = ('data: '+json.dumps(dict(choices=[dict(delta=delta,finish_reason='stop')],usage=dict(prompt_tokens=30,completion_tokens=20,prompt_tokens_details=dict(cached_tokens=5))))+'\n\ndata: [DONE]\n\n').encode()
                    self.send_response(200)
                    self.send_header('Content-Type','text/event-stream')
                    self.send_header('Content-Length',str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            fixture = HTTPServer(('127.0.0.1',0),Fixture)
            threading.Thread(target=fixture.serve_forever,daemon=True).start()
            try:
                project = request(ready,'/api/projects/'+project['id'],'PUT',dict(name='Frozen project',root=folder,tools=['research.pdf','memory.search'],checks={}))
                profile = request(ready,'/api/profiles','POST',dict(provider='compatible',label='Owned protocol fixture',api_key='fake-test-key',ephemeral=True,model='fixture',base_url=f'http://127.0.0.1:{fixture.server_port}/v1',input_usd_per_million=0,output_usd_per_million=0))
                pdf_run = request(ready,'/api/runs','POST',dict(task='Read evidence.pdf',architecture_id='baseline',mode='live',free_only=False,project_id=project['id'],tools_enabled=True,native_tools=True,route=dict(kind='profile',profile_id=profile['id'])))
                deadline = time.monotonic()+20
                while time.monotonic()<deadline:
                    pdf_run = request(ready,'/api/runs/'+pdf_run['id'])
                    if pdf_run['status'] in {'completed','interrupted'}: break
                    time.sleep(.1)
                assert pdf_run['status']=='completed',pdf_run
                assert 'Frozen PDF evidence' in json.dumps(pdf_run['agents']) and Fixture.calls==2
                assert pdf_run['usage']['reported_requests']==2, pdf_run['usage']
            finally:
                fixture.shutdown()
                fixture.server_close()
            # Exercise the 0.6 controls in the bundled engine without host peripherals,
            # external model calls, Docker, or training dependencies.
            operations = request(ready, '/api/ops')
            assert not operations['campaigns'] and 'voice' in operations
            knowledge = request(ready, '/api/ops/knowledge', 'POST', dict(project_id=project['id'], title='Frozen requirement', content='Keep this project portable'))
            assert knowledge['revision'] == 1
            preflight = request(ready, '/api/ops/preflight', 'POST', dict(mission=dict(task='Plan', architecture_id='baseline'), architecture_ids=['baseline']))
            assert preflight['comparisons'][0]['initial_requests'] == 1 and 'quota_groups' in preflight
            notebook = request(ready, '/api/ops/notebooks', 'POST', dict(project_id=project['id'], title='Frozen notebook', image='python:3.13-slim', cells=[dict(kind='python',content='print(4)')]))
            assert request(ready, '/api/ops/notebooks/'+notebook['id']+'/export')['nbformat'] == 4
            try:
                request(ready, '/api/ops/voice/chunk', 'POST', dict(wav='invalid'))
                raise AssertionError('Invalid audio accepted')
            except HTTPError as error:
                assert error.code == 400
            campaign = request(ready, '/api/ops/campaigns', 'POST', dict(name='Frozen campaign', mission=dict(task='Plan',architecture_id='baseline',project_id=project['id'],mode='demo'), milestones=[dict(id='plan',title='Plan',task='Produce a plan')]))
            mission = request(ready, '/api/ops/campaigns/'+campaign['id']+'/launch', 'POST')
            deadline = time.monotonic()+20
            while time.monotonic()<deadline:
                mission = request(ready, '/api/runs/'+mission['id'])
                if mission['status'] in {'completed','interrupted'}: break
                time.sleep(.1)
            assert mission['status']=='completed',mission
            reviewed = request(ready, '/api/ops/campaigns/'+campaign['id']+'/milestones/plan/review','POST',dict(accepted=True))
            assert reviewed['milestones'][0]['status']=='accepted'
            backup = request(ready,"/api/backups","POST")
            assert Path(backup["path"]).is_file()
            encrypted = request(ready, "/api/next/backup", "POST", dict(password="Frozen recovery passphrase", project_ids=[]))
            with tempfile.TemporaryDirectory(prefix="swarm-frozen-recovery-") as recovered:
                form = dict(path=encrypted["path"], password="Frozen recovery passphrase", target=str(Path(recovered)/"state"))
                reviewed = request(ready, "/api/next/restore/preview", "POST", form)
                restored = request(ready, "/api/next/restore", "POST", dict(**form, reviewed_sha256=reviewed["sha256"]))
                assert restored["restored"] and (Path(form["target"])/"RESTORE_RECEIPT.json").is_file()
            process.stdin.write("shutdown\n")
            process.stdin.flush()
            assert process.wait(timeout=12) == 0
            first_token = ready["token"]
            process, ready = start()
            assert ready["token"] != first_token
            assert request(ready, "/api/runs/" + run["id"])["status"] == "completed"
            assert request(ready,"/api/features")["memory"][0]["title"] == "Frozen decision"
            assert request(ready,'/api/ops')['campaigns'][0]['milestones'][0]['status']=='accepted'
            # The parent closing its pipe must also stop the engine.
            process.stdin.close()
            assert process.wait(timeout=12) == 0
        finally:
            if process.poll() is None:
                # Let a one-file bootloader's child close its databases before cleanup.
                try:
                    process.stdin.write("shutdown\n")
                    process.stdin.flush()
                    process.wait(timeout=15)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
                    else:
                        process.kill()
                    process.wait(timeout=10)
        restore = subprocess.run([str(binary),"restore",backup["path"],"--data-dir",str(data)],cwd=folder,capture_output=True,text=True,timeout=30,creationflags=creationflags)
        assert restore.returncode == 0, restore.stderr
        for mode in ("worker", "coordinator", "hub", "restore", "relay"):
            help_result = subprocess.run([str(binary), mode, "--help"], cwd=folder,
                capture_output=True, text=True, timeout=30, creationflags=creationflags)
            assert help_result.returncode == 0, help_result.stderr
            if mode == "worker": assert "--execution-image" in help_result.stdout
            if mode == "restore": assert "--encrypted" in help_result.stdout and "--preview" in help_result.stdout
        print("Frozen engine passed: startup, authentication, hardware, graph execution, budgets, context, packages, image validation, QR, project memory, certificates, native PDF calls and reported usage, catalog, GGUF header import, bundled browser driver, evaluations, campaign acceptance, preflight, knowledge, notebook export, audio rejection, encrypted backup/preview/restore, persistence, shutdown and worker/coordinator/hub/restore/relay CLI." + (" TLS private ingress and one-use/revocable phone pairing passed." if public_port else ""))


if __name__ == "__main__":
    main()
