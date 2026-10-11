"""Verify release archives without launching installers, windows, or input automation."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import zipfile


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_nsis_executable(packaged, original):
    # Tauri patches exactly the first bundle marker, then restores the original executable.
    # https://github.com/tauri-apps/tauri/blob/dev/crates/tauri-bundler/src/bundle.rs
    plain = original.read_bytes()
    marker = b"__TAURI_BUNDLE_TYPE_VAR_UNK"
    index = plain.find(marker)
    assert index >= 0, "Compiled desktop binary has no Tauri bundle marker"
    expected = plain[:index] + b"__TAURI_BUNDLE_TYPE_VAR_NSS" + plain[index + len(marker):]
    assert packaged.read_bytes() == expected, "NSIS desktop payload differs beyond its known bundle marker"


def check_zip(file, expected, prefix):
    with zipfile.ZipFile(file) as archive:
        assert archive.testzip() is None, f"Corrupt ZIP: {file}"
        for name in archive.namelist():
            path = PurePosixPath(name)
            assert not path.is_absolute() and ".." not in path.parts and "\\" not in name and ":" not in name, name
        for name, original in expected.items():
            assert hashlib.sha256(archive.read(prefix + name)).hexdigest() == digest(original), name
        native = json.loads(archive.read(prefix + "notices/DEPENDENCIES.json"))
        assert "MIT License" in archive.read(prefix + "LICENSE").decode("utf-8")
        assert archive.read(prefix + "docs/FEATURES_0_2.md")
        assert archive.read(prefix + "docs/FEATURES_0_3.md")
        assert archive.read(prefix + "docs/FEATURES_0_4.md")
        assert archive.read(prefix + "docs/FEATURES_0_5.md")
        assert archive.read(prefix + "docs/FEATURES_0_6.md")
        assert archive.read(prefix + "docs/COMPANION.md")
        frontend = json.loads(archive.read(prefix + "notices/frontend/DEPENDENCIES.json"))
        assert native and frontend
        assert any(package["name"] == "CPython" for package in native)
        assert any(package["name"] == "@fontsource/dm-sans" for package in frontend)
        for package in native:
            for notice in package["notices"]:
                assert archive.read(prefix + "notices/" + notice), notice
        for package in frontend:
            for notice in package["notices"]:
                assert archive.read(prefix + "notices/frontend/" + notice), notice


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seven-zip", help="Path to the console 7z executable for NSIS payload inspection")
    parser.add_argument("--check-engine", action="store_true", help="Test the extracted headless engine with development tools removed from PATH")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    output = repo / "releases"
    release = repo / "src-tauri" / "target" / "release"
    version = json.loads((repo / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["version"]
    assert digest(release / "swarm-engine.exe") == digest(repo / "src-tauri" / "binaries" / "swarm-engine-x86_64-pc-windows-msvc.exe"), "Packaged engine is stale"
    for line in (output / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        assert Path(name).name == name
        assert digest(output / name) == expected, name
    check_zip(output / f"Swarm-Workbench_{version}_windows-x64_portable.zip",
        {**{name: release / name for name in ("swarm-workbench.exe", "swarm-engine.exe")},
         **{"docs/"+name:repo/"docs"/name for name in ("BUILD_STATUS.md", "FEATURES_0_3.md", "FEATURES_0_4.md")}}, "Swarm Workbench/")
    check_zip(output / f"Swarm-Worker_{version}_windows-x64.zip",
        {"swarm-engine-windows-x64.exe": release / "swarm-engine.exe",
         **{"docs/"+name:repo/"docs"/name for name in ("BUILD_STATUS.md", "FEATURES_0_3.md", "FEATURES_0_4.md")}}, "Swarm Worker/")
    assert digest(output / "swarm-engine-windows-x64.exe") == digest(release / "swarm-engine.exe")
    seven_zip = args.seven_zip or shutil.which("7z")
    installer = output / f"Swarm Workbench_{version}_x64-setup.exe"
    if seven_zip:
        # 7z parses the installer as an archive; it never executes its installation code.
        with tempfile.TemporaryDirectory(prefix="swarm-payload-inspection-") as folder:
            subprocess.run([seven_zip, "x", str(installer), "-o" + folder, "-y"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            extracted = Path(folder)
            check_nsis_executable(extracted / "swarm-workbench.exe", release / "swarm-workbench.exe")
            assert digest(extracted / "swarm-engine.exe") == digest(release / "swarm-engine.exe")
            assert (extracted / "notices" / "DEPENDENCIES.json").is_file()
            assert (extracted / "notices" / "frontend" / "DEPENDENCIES.json").is_file()
            assert "MIT License" in (extracted / "LICENSE").read_text(encoding="utf-8")
            assert (extracted / "docs" / "FEATURES_0_2.md").is_file()
            assert (extracted / "docs" / "FEATURES_0_3.md").is_file()
            for name in ("BUILD_STATUS.md", "FEATURES_0_3.md", "FEATURES_0_4.md"):
                assert digest(extracted / "docs" / name) == digest(repo / "docs" / name), name
            if args.check_engine:
                environment = os.environ.copy()
                if os.name == "nt":
                    windows_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or "C:/Windows"
                    environment["PATH"] = str(Path(windows_root) / "System32")
                subprocess.run([sys.executable, str(repo / "scripts" / "check_desktop_engine.py"), str(extracted / "swarm-engine.exe")],
                    cwd=folder, env=environment, check=True, timeout=150)
        print("NSIS payload verified with its Tauri bundle marker; engine bytes and native/frontend notices verified.")
    else:
        print("NSIS payload inspection skipped: provide --seven-zip to inspect it without executing it.")
    print("Desktop/worker ZIP integrity, binary matches, dependency/font notices, and release SHA-256 checks passed.")


if __name__ == "__main__":
    main()
