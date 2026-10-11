"""Collect dependency notices for the frozen Python runtime and native Rust binary."""
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from packaging.requirements import Requirement


def main():
    repo = Path(__file__).resolve().parent.parent
    output = repo / "build" / "notices"
    # This folder contains only generated notices. Check its resolved boundary before replacement.
    if output.resolve().parent != (repo / "build").resolve() or output.name != "notices":
        raise SystemExit("Unexpected notice output directory")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    records = []
    seen = set()

    def collect(kind, name, version, license, files):
        directory = output / kind / f"{name}-{version}"
        directory.mkdir(parents=True, exist_ok=True)
        copied = []
        used = set()
        for file in files:
            if file.is_file():
                target = directory / file.name
                if file.name in used:
                    target = directory / (file.parent.name + "__" + file.name)
                used.add(file.name)
                shutil.copyfile(file, target)
                copied.append(str(target.relative_to(output)).replace("\\", "/"))
        records.append(dict(kind=kind, name=name, version=version, license=license, notices=copied))

    def visit(requirement):
        requirement = Requirement(requirement)
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}): return
        distribution = metadata.distribution(requirement.name)
        name = distribution.metadata["Name"]
        if name in seen: return
        seen.add(name)
        files = [Path(distribution.locate_file(file)) for file in distribution.files or []
                 if Path(file).name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "OFL", "NOTICE", "THIRDPARTY"))]
        collect("python", name, distribution.version, distribution.metadata.get("License-Expression") or distribution.metadata.get("License"), files)
        for dependency in distribution.requires or []: visit(dependency)

    project = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    for requirement in project["project"]["dependencies"] + ["pyinstaller"]: visit(requirement)
    collect("python", "CPython", sys.version.split()[0], "PSF-2.0", [Path(sys.base_prefix) / "LICENSE.txt"])
    cargo = json.loads(subprocess.check_output(["cargo", "metadata", "--manifest-path", str(repo / "src-tauri" / "Cargo.toml"),
        "--format-version", "1", "--locked"], text=True, encoding="utf-8"))
    for package in cargo["packages"]:
        if not package["source"]: continue
        directory = Path(package["manifest_path"]).parent
        files = [file for file in directory.iterdir() if file.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE"))]
        if package.get("license_file"): files.append(directory / package["license_file"])
        collect("rust", package["name"], package["version"], package["license"], dict.fromkeys(files))
    (output / "DEPENDENCIES.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    (output / "README.txt").write_text("Third-party dependency notices for Swarm Workbench. The Rust inventory includes cross-platform/build dependencies as well as runtime crates. A missing notice entry requires review before a public release. Project licensing is separate from these dependency licenses.\n", encoding="utf-8")
    print(f"Included native/runtime inventory for {len(records)} dependencies.")


if __name__ == "__main__":
    main()
