"""Bundled engine entry point; the native desktop owns its lifetime and credentials."""
import argparse
import asyncio
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import secrets
import socket
import sys
import threading

import uvicorn

from swarm.app import create_app
from swarm.private_listener import load_settings, WorkerOnly


def desktop_main():
    parser = argparse.ArgumentParser(description="Private Swarm desktop engine")
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    root = Path(args.data_dir)
    root.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(root / "engine.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)

    token = secrets.token_urlsafe(32)
    network_error = ""
    try:
        network = load_settings(root)
        network.check_certificate()
    except (ValueError, OSError) as error:
        network = None
        network_error = f"Private listener disabled: {error}"
        logging.error(network_error)
    if network and network.enabled:
        os.environ["SWARM_PUBLIC_ORIGIN"] = network.public_origin
    app = create_app(root, token, desktop=True)
    app.state.private_network = dict(enabled=False, public_origin=network.public_origin if network else "",
                                    status="disabled", error=network_error)
    public_server = None
    public_task = None
    advertisement = None
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]

    class DesktopServer(uvicorn.Server):
        async def startup(self, sockets=None):
            nonlocal public_server, public_task, advertisement
            await super().startup(sockets=sockets)
            if self.started:
                if network and network.enabled:
                    try:
                        public_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        public_socket.bind(("0.0.0.0", network.port))
                        public_socket.listen(128)
                        public_server = uvicorn.Server(uvicorn.Config(WorkerOnly(app), host="0.0.0.0", port=network.port,
                            ssl_certfile=network.cert_file, ssl_keyfile=network.key_file, loop="asyncio", http="h11",
                            ws="none", lifespan="off", log_config=None, access_log=False, timeout_graceful_shutdown=3))
                        public_task = asyncio.create_task(public_server.serve(sockets=[public_socket]))
                        for _ in range(100):
                            if public_server.started or public_task.done(): break
                            await asyncio.sleep(.05)
                        if not public_server.started:
                            raise RuntimeError("TLS listener did not start")
                        app.state.private_network.update(enabled=True, status="listening")
                        if network.advertise_lan:
                            try:
                                from swarm.pairing import advertise
                                advertisement = await asyncio.to_thread(advertise, network.public_origin, network.cert_file)
                            except Exception:
                                logging.warning("LAN advertisement unavailable; direct pairing remains available")
                    except (ValueError, OSError, RuntimeError) as error:
                        app.state.private_network.update(status="error", error=str(error))
                        logging.error("Private listener failed: %s", error)
                        if public_server: public_server.should_exit = True
                # This pipe goes only to the native parent. Never print credentials in logs.
                print(json.dumps(dict(event="ready", port=port, token=token)), flush=True)

    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            loop="asyncio", http="h11", ws="none", log_config=None,
                            access_log=False, timeout_graceful_shutdown=4)
    server = DesktopServer(config)

    async def serve():
        loop = asyncio.get_running_loop()

        def control():
            # EOF also closes the engine if the native application disappears.
            for line in sys.stdin:
                if line.strip() == "shutdown":
                    break
            loop.call_soon_threadsafe(setattr, server, "should_exit", True)

        threading.Thread(target=control, name="desktop-lifetime", daemon=True).start()
        try:
            await server.serve(sockets=[sock])
        finally:
            if advertisement:
                zc, info = advertisement
                await asyncio.to_thread(zc.unregister_service, info)
                await asyncio.to_thread(zc.close)
            if public_server:
                public_server.should_exit = True
            if public_task:
                await asyncio.gather(public_task, return_exceptions=True)
            sock.close()

    asyncio.run(serve())


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode in {"worker", "coordinator", "hub", "restore", "relay"}:
        sys.argv.pop(1)
        if mode == "worker":
            from swarm.worker import main as worker_main
            worker_main()
        elif mode == "hub":
            from swarm.hub import main as hub_main
            hub_main()
        elif mode == "restore":
            from swarm.maintenance import main as restore_main
            restore_main()
        elif mode == "relay":
            from swarm.relay import main as relay_main
            relay_main()
        else:
            from swarm.cli import main as coordinator_main
            coordinator_main()
    elif mode == "--version":
        print("Swarm Workbench engine 0.4.0")
    else:
        desktop_main()


if __name__ == "__main__":
    main()
