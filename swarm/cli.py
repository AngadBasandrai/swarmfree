import argparse
import os
import secrets
import uvicorn
from .app import create_app


def main():
    parser = argparse.ArgumentParser(description="Start the Swarm Workbench coordinator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir")
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    parser.add_argument("--public-origin", help="Required HTTPS origin when listening beyond loopback")
    args = parser.parse_args()
    remote = args.host not in {"127.0.0.1", "localhost", "::1"}
    if remote and not (args.tls_cert and args.tls_key and args.public_origin and args.public_origin.startswith("https://")):
        parser.error("Remote binding requires --tls-cert, --tls-key, and --public-origin https://... . Loopback works without TLS.")
    if args.public_origin:
        os.environ["SWARM_PUBLIC_ORIGIN"] = args.public_origin
    token = os.getenv("SWARM_OWNER_TOKEN") or secrets.token_urlsafe(32)
    scheme = "https" if args.tls_cert else "http"
    origin = args.public_origin or f"{scheme}://{args.host}:{args.port}"
    os.environ["SWARM_PUBLIC_ORIGIN"] = origin
    print(f"Swarm Workbench: {origin}/#token={token}", flush=True)
    uvicorn.run(create_app(args.data_dir, token), host=args.host, port=args.port,
        ssl_certfile=args.tls_cert, ssl_keyfile=args.tls_key, access_log=False)


if __name__ == "__main__":
    main()
