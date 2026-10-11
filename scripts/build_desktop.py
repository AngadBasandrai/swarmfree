"""Build desktop installers and a portable Windows zip; users need no dev runtimes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-engine", action="store_true", help="Reuse a previously frozen engine")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--bundle-only", action="store_true", help="Regenerate installers from already compiled binaries")
    modes.add_argument("--package-only", action="store_true", help="Repackage existing installers/binaries and refresh checksums")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    config = json.loads((repo / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    version = config["version"]
    for name in ("TAURI_SIGNING_PRIVATE_KEY", "APPLE_SIGNING_IDENTITY", "APPLE_ID", "APPLE_PASSWORD", "APPLE_TEAM_ID"):
        if not os.environ.get(name): os.environ.pop(name, None)
    if not args.skip_engine and not args.bundle_only and not args.package_only:
        subprocess.run([sys.executable, str(repo / "scripts" / "build_engine.py")], cwd=repo, check=True)
    if not args.package_only:
        subprocess.run([sys.executable, str(repo / "scripts" / "collect_native_notices.py")], cwd=repo, check=True)
        if args.bundle_only:
            subprocess.run([shutil.which("node"), str(repo / "scripts" / "collect_frontend_notices.mjs")], cwd=repo, check=True)
        command = [shutil.which("npx"), "tauri", "bundle" if args.bundle_only else "build"]
        if sys.platform == "win32": command += ["--bundles", "nsis"]
        overrides = {}
        if os.environ.get("TAURI_SIGNING_PRIVATE_KEY"):
            overrides["bundle"] = {"createUpdaterArtifacts": True}
        if sys.platform == "win32" and os.environ.get("SWARM_WINDOWS_CERT_THUMBPRINT"):
            overrides.setdefault("bundle", {}).setdefault("windows", {}).update(
                certificateThumbprint=os.environ["SWARM_WINDOWS_CERT_THUMBPRINT"],
                timestampUrl="http://timestamp.digicert.com")
        if overrides:
            override_path = repo / "build" / "release-overrides.json"
            override_path.write_text(json.dumps(overrides), encoding="utf-8")
            command += ["--config", str(override_path)]
        subprocess.run(command, cwd=repo, check=True)
    release = repo / "src-tauri" / "target" / "release"
    output = repo / "releases"
    output.mkdir(exist_ok=True)
    installers = list((release / "bundle").rglob("*-setup.exe")) if sys.platform == "win32" else [
        file for file in (release / "bundle").rglob("*") if file.suffix in {".dmg", ".deb", ".rpm", ".AppImage"}]
    for installer in installers:
        if version not in installer.name: continue
        shutil.copy2(installer, output / installer.name)
        signature = Path(str(installer) + ".sig")
        if signature.is_file(): shutil.copy2(signature, output / signature.name)
    def documents(package, prefix):
        for file in (repo / "build" / "notices").rglob("*"):
            if file.is_file(): package.write(file, prefix + "notices/" + file.relative_to(repo / "build" / "notices").as_posix())
        package.write(repo / "LICENSE", prefix + "LICENSE")
        for file in (repo / "docs").glob("*.md"):
            package.write(file, prefix + "docs/" + file.name)
    if sys.platform == "win32":
        archive = output / f"Swarm-Workbench_{version}_windows-x64_portable.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
            for name in ("swarm-workbench.exe", "swarm-engine.exe"):
                package.write(release / name, "Swarm Workbench/" + name)
            documents(package, "Swarm Workbench/")
            package.write(repo / "README.md", "Swarm Workbench/README.md")
        shutil.copy2(release / "swarm-engine.exe", output / "swarm-engine-windows-x64.exe")
        with zipfile.ZipFile(output / f"Swarm-Worker_{version}_windows-x64.zip", "w", zipfile.ZIP_DEFLATED) as package:
            package.write(release / "swarm-engine.exe", "Swarm Worker/swarm-engine-windows-x64.exe")
            documents(package, "Swarm Worker/")
            package.write(repo / "docs" / "WORKER_QUICKSTART.md", "Swarm Worker/README.md")
    else:
        rust = subprocess.check_output(["rustc", "-vV"], text=True)
        target = next(line.split(": ", 1)[1] for line in rust.splitlines() if line.startswith("host: "))
        engine = repo / "src-tauri" / "binaries" / f"swarm-engine-{target}"
        for updater in (release / "bundle").rglob("*.tar.gz"):
            destination = output / f"Swarm-Workbench_{version}_{target}.app.tar.gz"
            shutil.copy2(updater, destination)
            signature = Path(str(updater) + ".sig")
            if signature.is_file(): shutil.copy2(signature, Path(str(destination) + ".sig"))
        with zipfile.ZipFile(output / f"Swarm-Worker_{version}_{target}.zip", "w", zipfile.ZIP_DEFLATED) as package:
            package.write(engine, "Swarm Worker/swarm-engine")
            documents(package, "Swarm Worker/")
            package.write(repo / "docs" / "WORKER_QUICKSTART.md", "Swarm Worker/README.md")
    files = [file for file in output.iterdir() if file.suffix in {".exe", ".zip", ".dmg", ".deb", ".rpm", ".AppImage", ".gz", ".sig"}]
    checksums = []
    for file in files:
        with file.open("rb") as stream:
            checksums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {file.name}\n")
    (output / "SHA256SUMS.txt").write_text("".join(checksums), encoding="utf-8")
    print(f"Desktop artifacts: {output}")


if __name__ == "__main__":
    main()
