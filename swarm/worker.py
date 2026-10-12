"""Run on a second device with Ollama. Private inference only, one request at a time."""
import argparse
import asyncio
import os
import base64
import json
from pathlib import Path
import time
from urllib.parse import urlparse
import httpx
from platformdirs import user_data_dir
from .hardware import hardware, default_budgets, inventory, fit_model, ollama_url
from .providers import ollama_stream, RouteError
from .store import Store
from .vault import Vault


async def run(args):
    if args.pair:
        if len(args.pair) > 32000: raise ValueError("Pairing bundle is too large")
        pairing = json.loads(base64.urlsafe_b64decode(args.pair))
        if pairing.get("version") not in {1,2} or pairing.get("expires_at", 0) < time.time():
            raise ValueError("Pairing bundle is invalid or expired")
        args.coordinator, args.invite = pairing["origin"], pairing["invite"]
    if not args.coordinator:
        raise ValueError("Provide --coordinator or --pair")
    parsed = urlparse(args.coordinator)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Coordinator URL must not contain credentials or query parameters")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("Worker connections require HTTPS beyond loopback")
    base = args.coordinator.rstrip("/")
    root = Path(args.data_dir or user_data_dir("SwarmWorker", appauthor=False))
    root.mkdir(parents=True, exist_ok=True)
    relay_settings=pairing.get("relay") if args.pair else None
    if args.pair and pairing.get("version")==1:
        cert = pairing.get("ca_pem", "")
        if not 100 <= len(cert) <= 16000: raise ValueError("Invalid pairing certificate")
        ca = root / "pairing-ca.pem"
        ca.write_text(cert, encoding="utf-8")
        args.ca_cert = str(ca)
    store = Store(root / "worker.sqlite")
    vault = Vault("swarm-worker")
    relay_channel=getattr(args,"relay_channel",None) or (relay_settings or {}).get("channel")
    if relay_channel:
        if relay_settings:
            vault.set("relay_"+relay_channel,json.dumps(relay_settings),args.ephemeral)
        else:
            stored=vault.get("relay_"+relay_channel)
            if not stored: raise ValueError("Relay credentials unavailable; pair this worker again")
            relay_settings=json.loads(stored)
        base=base+"/linked/"+relay_channel
    info = store.get("worker", base)
    hw = hardware()
    budgets = default_budgets(hw)
    for key, value in (("ram_gb", args.ram_gb), ("vram_gb", args.vram_gb), ("context", args.context)):
        if value is not None:
            budgets[key] = value
    if budgets["ram_gb"] <= 0 or budgets["ram_gb"] > hw["ram_total_gb"] or budgets["vram_gb"] < 0:
        raise ValueError("Worker RAM/VRAM budgets are invalid for this device")
    if hw["gpus"] and budgets["vram_gb"] > max(g["total_gb"] for g in hw["gpus"]):
        raise ValueError("Worker VRAM budget exceeds an individual GPU's capacity")
    if not 512 <= budgets["context"] <= 32768:
        raise ValueError("Context must be between 512 and 32768 tokens")
    url = ollama_url()
    from .private_cluster import ClusterWorker
    cluster=ClusterWorker(store,root,getattr(args,'cluster_image',None),getattr(args,'cluster_model_root',None),getattr(args,'cluster_address',None),budgets)
    await cluster.reap(revoked=True)
    async def cluster_watchdog():
        while True:
            await asyncio.sleep(5);await cluster.reap()

    async def describe():
        inv = await inventory(url)
        hw = hardware()
        loaded={m["name"] for m in inv["loaded"]}
        return dict(hardware=hw, models=[dict(m,loaded=m["name"] in loaded) for m in inv["models"] if fit_model(m, hw, budgets, inv["loaded"])["fits"]]+cluster.models(), budgets=budgets,cluster=cluster.describe(),
                    execution=dict(enabled=bool(getattr(args,"execution_image",None)),image=getattr(args,"execution_image",None)))

    def available():
        day = time.strftime("%Y-%m-%d", time.gmtime())
        contribution = store.get("contribution", base, {})
        count = contribution.get("count", 0) if contribution.get("day") == day else 0
        if count >= args.daily_jobs: return False
        if args.hours:
            start, end = map(int, args.hours.split("-"))
            hour = time.localtime().tm_hour
            if not (start <= hour < end if start < end else hour >= start or hour < end): return False
        if args.pause_on_battery:
            import psutil
            battery = psutil.sensors_battery()
            if battery and not battery.power_plugged: return False
        return True

    # System CA verification stays on; use --ca-cert for a private CA.
    if relay_settings:
        from .relay import RelayWorkerClient
        transport=RelayWorkerClient(relay_settings,base)
    else: transport=httpx.AsyncClient(timeout=15, verify=args.ca_cert or True, follow_redirects=False, trust_env=False)
    async with transport as client:
        if args.invite:
            if not args.ephemeral and not vault.status()["persistent"]:
                raise ValueError("No OS credential store is available. Pair with --ephemeral for session-only worker credentials.")
            desc = await describe()
            response = await client.post(base + "/api/worker/join", json=dict(invite=args.invite, name=args.name or hw["name"], **desc))
            response.raise_for_status()
            joined = response.json()
            storage = vault.set(joined["device_id"], joined["token"], args.ephemeral)
            info = dict(device_id=joined["device_id"], storage=storage)
            store.put("worker", base, info)
        if not info:
            raise ValueError("Pair this device with --invite TOKEN from the workbench Devices panel")
        token = vault.get(info["device_id"])
        if not token:
            raise ValueError("Worker token unavailable. Pair again; session-only credentials expire on restart.")
        client.headers["Authorization"] = "Bearer " + token
        path = base + "/api/worker/" + info["device_id"]
        print("Worker connected. Approve this device in the owner's Devices panel. Ctrl+C stops contribution.", flush=True)
        last_heartbeat = 0
        watchdog=asyncio.create_task(cluster_watchdog())
        try:
            while True:
                try:
                    if time.monotonic()-last_heartbeat > 8:
                        await cluster.reap()
                        description = await describe()
                        if not available(): description["models"] = [];description["execution"]["enabled"]=False
                        r = await client.post(path + "/heartbeat", json=description)
                        r.raise_for_status()
                        last_heartbeat = time.monotonic()
                    if not available():
                        await asyncio.sleep(1)
                        continue
                    r = await client.post(path + "/lease")
                    r.raise_for_status()
                    job = r.json()["job"]
                    if not job:
                        await asyncio.sleep(1)
                        continue
                    day = time.strftime("%Y-%m-%d", time.gmtime())
                    contribution = store.get("contribution", base, {})
                    if job.get('kind')!='cluster':store.put("contribution", base, dict(day=day, count=(contribution.get("count", 0) if contribution.get("day") == day else 0)+1))
                    print(f"Inference job {job['id']} · {job['model']}", flush=True)
                    text = ""
                    result_url = path + "/jobs/" + job["id"]
                    stream = None
                    usage,native_calls={},[]
                    execution_result=None
                    cluster_result=None

                    async def report(done=False, error=False):
                        response = await client.post(result_url, json=dict(lease=job["lease"], text=text, done=done, error=error,usage=usage,native_calls=native_calls,execution_result=execution_result,cluster_result=cluster_result))
                        response.raise_for_status()

                    try:
                        if job.get('kind')=='cluster':
                            control_task=asyncio.create_task(cluster.handle(job['payload']))
                            try:
                                while not control_task.done():
                                    await asyncio.wait({control_task},timeout=3);await report()
                                    r=await client.post(path+'/heartbeat',json=await describe());r.raise_for_status()
                                cluster_result=control_task.result();await report(done=True)
                            finally:
                                if not control_task.done():control_task.cancel();await asyncio.gather(control_task,return_exceptions=True)
                            continue
                        if job.get("kind")=="execution":
                            from .remote_execution import execute_container
                            image=getattr(args,"execution_image",None)
                            if not image: raise RouteError("This worker has not opted in to isolated execution")
                            execution_task=asyncio.create_task(execute_container(job["payload"],image,min(1,budgets["ram_gb"])))
                            try:
                                while not execution_task.done():
                                    await asyncio.wait({execution_task},timeout=3)
                                    await report()
                                    r=await client.post(path+"/heartbeat",json=await describe());r.raise_for_status()
                                execution_result=execution_task.result()
                                await report(done=True)
                            finally:
                                if not execution_task.done(): execution_task.cancel();await asyncio.gather(execution_task,return_exceptions=True)
                            continue
                        if job['model'].startswith('cluster:'):
                            stream=cluster.infer(job['model'],job['messages'],job.get('max_tokens',1536),usage,job.get('native_tools',[]),native_calls)
                        else:
                            stream=None
                        inv = await inventory(url)
                        model = next((m for m in inv["models"] if m["name"] == job["model"]), None)
                        if not model and stream is None:
                            raise RouteError("Model disappeared from this worker")
                        fit = fit_model(model, hardware(), budgets, inv["loaded"]) if model else dict(fits=True,placement='private cluster')
                        if not fit["fits"] and stream is None:
                            raise RouteError("Model no longer fits this worker's budget")
                        if stream is None:stream = ollama_stream(url, job["model"], job["messages"], {**budgets, "max_tokens":job.get("max_tokens",1536),"_usage":usage,"_native_calls":native_calls,"_tools":job.get("native_tools",[])}, fit["placement"])
                        iterator = stream.__aiter__()
                        pending = asyncio.create_task(anext(iterator))
                        last_report = 0
                        while True:
                            done, _ = await asyncio.wait({pending}, timeout=3)
                            if not done:
                                await report()  # renew the lease during model loading or a long token gap
                                r = await client.post(path + "/heartbeat", json=await describe())
                                r.raise_for_status()
                                continue
                            try:
                                text += pending.result()
                            except StopAsyncIteration:
                                break
                            if time.monotonic()-last_report > .2:
                                await report()
                                last_report = time.monotonic()
                            if time.monotonic()-last_heartbeat > 8:
                                r = await client.post(path + "/heartbeat", json=await describe())
                                r.raise_for_status()
                                last_heartbeat = time.monotonic()
                            pending = asyncio.create_task(anext(iterator))
                        await report(done=True)
                    except (RouteError, httpx.HTTPError, ValueError):
                        print("Job interrupted or rejected. Output will not be accepted under an expired lease.", flush=True)
                        try:
                            await report(error=True)
                        except httpx.HTTPError:
                            pass
                    finally:
                        if "pending" in locals() and not pending.done():
                            pending.cancel()
                            await asyncio.gather(pending, return_exceptions=True)
                        if stream:
                            await stream.aclose()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in {401, 403}:
                        raise ValueError("This worker has been revoked or its credential is invalid") from e
                    print("Coordinator request failed; retrying in five seconds.", flush=True)
                    await asyncio.sleep(5)
                except httpx.RequestError:
                    print("Coordinator unavailable; retrying in five seconds.", flush=True)
                    await asyncio.sleep(5)
        finally:
            watchdog.cancel();await asyncio.gather(watchdog,return_exceptions=True)
            await cluster.reap(revoked=True)
            store.close()


def main():
    parser = argparse.ArgumentParser(description="Contribute local Ollama inference to your private swarm")
    parser.add_argument("--coordinator")
    parser.add_argument("--relay-channel",help="Reconnect a previously paired relay channel using its locally stored credentials")
    parser.add_argument("--pair", help="Paste a trusted one-use bundle from the desktop app; includes its TLS trust certificate")
    parser.add_argument("--invite", help="Single-use pairing invitation; valid for five minutes")
    parser.add_argument("--name")
    parser.add_argument("--data-dir")
    parser.add_argument("--ram-gb", type=float)
    parser.add_argument("--vram-gb", type=float)
    parser.add_argument("--context", type=int)
    parser.add_argument("--ephemeral", action="store_true", help="Keep the worker credential in memory for this process only")
    parser.add_argument("--ca-cert", help="PEM file for a trusted private CA")
    parser.add_argument("--daily-jobs", type=int, default=100, help="Maximum inference jobs per UTC day")
    parser.add_argument("--hours", help="Contribution window in local hours, e.g. 20-8")
    parser.add_argument("--pause-on-battery", action="store_true")
    parser.add_argument("--execution-image",help="Opt in to private isolated jobs using this already installed Docker image; no host execution fallback")
    parser.add_argument('--cluster-image',help='Opt in to trusted private vLLM clustering with an installed digest-pinned Linux image')
    parser.add_argument('--cluster-model-root',help='Directory containing permitted complete local model folders')
    parser.add_argument('--cluster-address',help='Private IPv4 interface used for distributed inference')
    args = parser.parse_args()
    if not 1 <= args.daily_jobs <= 10000: parser.error("Daily jobs must be between 1 and 10000")
    if args.hours:
        import re
        if not re.fullmatch(r"(?:[01]?\d|2[0-3])-(?:[01]?\d|2[0-3])", args.hours): parser.error("Hours must use start-end between 0 and 23")
    try:
        asyncio.run(run(args))
    except (ValueError, httpx.HTTPError) as e:
        parser.exit(1, str(e) + "\n")
    except KeyboardInterrupt:
        print("Worker stopped.")


if __name__ == "__main__":
    main()
