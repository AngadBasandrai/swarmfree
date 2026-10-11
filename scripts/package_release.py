"""Build a source-alpha wheel including the UI and bundled dependency licenses."""
import json
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    repo = Path(__file__).resolve().parent.parent
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("Node.js/npm is required to build the UI")
    subprocess.run([npm, "run", "build"], cwd=repo, check=True)
    destination = (repo / "swarm" / "web_dist").resolve()
    # Verify the absolute path before deleting only the generated package UI.
    if destination.parent != (repo / "swarm").resolve() or destination.name != "web_dist":
        raise SystemExit("Unexpected package UI destination")
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(repo / "dist", destination)
    licenses = destination / "licenses"
    licenses.mkdir()
    paths = subprocess.check_output([npm, "ls", "--omit=dev", "--all", "--parseable"], cwd=repo, text=True).splitlines()
    manifest = []
    for directory in dict.fromkeys(paths):
        package = Path(directory)
        if package.resolve() == repo or not (package / "package.json").exists():
            continue
        metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
        name = metadata.get("name", package.name)
        copied = []
        for file in package.iterdir():
            if file.is_file() and file.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "OFL")):
                filename = name.replace("/", "__").replace("@", "") + "__" + file.name
                shutil.copyfile(file, licenses / filename)
                copied.append(filename)
        manifest.append(dict(name=name, version=metadata.get("version"), license=metadata.get("license"), notices=copied))
    (licenses / "DEPENDENCIES.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "build", "--wheel"], cwd=repo, check=True)
    print("Packaged MIT source alpha. Signing and publication remain release operations.")


if __name__ == "__main__":
    main()
