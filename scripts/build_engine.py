"""Freeze a self-contained engine for Tauri's externalBin on the current OS."""
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    repo = Path(__file__).resolve().parent.parent
    rust = subprocess.check_output(["rustc", "-vV"], text=True)
    target = next(line.split(": ", 1)[1] for line in rust.splitlines() if line.startswith("host: "))
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
               "--name", "swarm-engine", "--distpath", str(repo / "build" / "engine"),
               "--workpath", str(repo / "build" / "pyinstaller"), "--specpath", str(repo / "build"),
               "--paths", str(repo), "--copy-metadata", "keyring",
               "--copy-metadata", "langgraph", "--copy-metadata", "langgraph-checkpoint",
               "--copy-metadata", "langgraph-checkpoint-sqlite", "--copy-metadata", "langchain-core",
               "--collect-all", "playwright", "--collect-data", "jsonschema_specifications",
               "--collect-submodules", "keyring.backends", "--hidden-import", "uvicorn.logging",
               "--hidden-import", "uvicorn.loops.asyncio", "--hidden-import", "uvicorn.protocols.http.h11_impl",
               "--hidden-import", "uvicorn.lifespan.on", "--exclude-module", "pytest",
               "--exclude-module", "tkinter", str(repo / "scripts" / "desktop_entry.py")]
    subprocess.run(command, cwd=repo, check=True)
    extension = ".exe" if sys.platform == "win32" else ""
    folder = repo / "src-tauri" / "binaries"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"swarm-engine-{target}{extension}"
    shutil.copy2(repo / "build" / "engine" / f"swarm-engine{extension}", destination)
    print(f"Bundled engine: {destination}")


if __name__ == "__main__":
    main()
