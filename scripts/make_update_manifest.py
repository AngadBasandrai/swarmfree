"""Create a reviewable Tauri manifest from signed artifacts; never publish it."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import quote, urlparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="HTTPS directory where the operator will host these artifacts")
    parser.add_argument("--notes", default="Swarm Workbench update")
    parser.add_argument("--target", required=True, choices=["windows-x86_64", "darwin-aarch64", "darwin-x86_64", "linux-x86_64", "linux-aarch64"])
    parser.add_argument("artifact", help="Signed setup.exe, .app.tar.gz or .AppImage")
    parser.add_argument("--asset-name", help="Public filename when the host uses a different asset name")
    parser.add_argument("--config", help="Tauri config from the release source checkout")
    parser.add_argument("--output", default="releases/update-manifest.json")
    args = parser.parse_args()
    url = urlparse(args.base_url)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        parser.error("Use an HTTPS artifact directory without credentials, query or fragment")
    artifact = Path(args.artifact).resolve()
    signature = Path(str(artifact) + ".sig")
    if not artifact.is_file() or not signature.is_file(): parser.error("The artifact and its adjacent .sig must exist")
    asset_name = args.asset_name or artifact.name
    if not asset_name or Path(asset_name).name != asset_name or '\\' in asset_name or asset_name in {'.', '..'}:
        parser.error("Use a plain public asset filename")
    repo = Path(__file__).resolve().parent.parent
    config = Path(args.config) if args.config else repo/"src-tauri"/"tauri.conf.json"
    version = json.loads(config.read_text(encoding="utf-8"))["version"]
    output = Path(args.output)
    manifest = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    if manifest.get("version") != version: manifest = {"version":version,"platforms":{}}
    manifest.update(notes=args.notes, pub_date=datetime.now(timezone.utc).isoformat().replace("+00:00","Z"))
    manifest["platforms"][args.target] = dict(url=args.base_url.rstrip("/")+"/"+quote(asset_name), signature=signature.read_text(encoding="utf-8").strip())
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    print("Manifest prepared locally: "+str(output)+". No files were published.")


if __name__ == "__main__": main()
