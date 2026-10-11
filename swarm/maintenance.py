"""Online SQLite backups and offline restore, excluding credential vaults."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import zipfile
from .locking import WorkspaceLock


def create_backup(root, store):
    root = Path(root)
    destination = root / "backups"
    destination.mkdir(exist_ok=True)
    path = destination / f"swarm-{time.time_ns()}.zip"
    with tempfile.TemporaryDirectory(prefix="swarm-backup-") as folder:
        files = []
        for name in ("workbench.sqlite", "checkpoints.sqlite"):
            target = Path(folder) / name
            source = store.db if name == "workbench.sqlite" else sqlite3.connect(root / name)
            try:
                with closing(sqlite3.connect(target)) as connection:
                    source.backup(connection)
                files.append(target)
            finally:
                if source is not store.db: source.close()
        manifest = dict(version=2, created_at=time.time(), files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for file in files: archive.write(file, file.name)
    return dict(name=path.name, path=str(path), size=path.stat().st_size, vault_included=False, project_files_included=False)


def restore_backup(file, root):
    root = Path(root).resolve()
    with WorkspaceLock(root):
        _restore(file, root)


def _restore(file, root):
    with zipfile.ZipFile(file) as archive:
        if set(archive.namelist()) != {"manifest.json", "workbench.sqlite", "checkpoints.sqlite"} or any(i.file_size > 250_000_000 for i in archive.infolist()):
            raise ValueError("Invalid or oversized backup")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest["version"] != 2:
            raise ValueError("Unsupported backup version")
        with tempfile.TemporaryDirectory(prefix="swarm-restore-") as folder:
            for name in ("workbench.sqlite", "checkpoints.sqlite"):
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != manifest["files"][name]:
                    raise ValueError("Backup integrity check failed")
                path = Path(folder) / name
                path.write_bytes(data)
                with closing(sqlite3.connect(path)) as db:
                    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Invalid database")
            root.mkdir(parents=True, exist_ok=True)
            for name in ("workbench.sqlite", "checkpoints.sqlite"):
                target = root / name
                if target.exists():
                    target.replace(root / (name + f".before-restore-{time.time_ns()}"))
                for suffix in ("-wal", "-shm"):
                    (root / (name + suffix)).unlink(missing_ok=True)
                temporary = root / (name + ".restoring")
                temporary.write_bytes((Path(folder) / name).read_bytes())
                temporary.replace(target)


def main():
    parser = argparse.ArgumentParser(description="Restore a verified Swarm backup with the application closed")
    parser.add_argument("backup")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--encrypted",action="store_true",help="Restore a .swarmbak full recovery snapshot into an empty directory")
    parser.add_argument("--preview",action="store_true",help="Preview encrypted restore paths without writing files")
    parser.add_argument("--mapping",help="JSON file mapping project IDs to new empty directories")
    parser.add_argument("--reviewed-sha256",help="Digest from the exact encrypted restore preview")
    args = parser.parse_args()
    if args.encrypted:
        import getpass
        from .full_backup import preview_full,restore_full
        password=getpass.getpass("Backup passphrase: ")
        mapping=json.loads(Path(args.mapping).read_text(encoding="utf-8")) if args.mapping else {}
        preview=preview_full(args.backup,password,args.data_dir,mapping)
        if args.preview:
            print(json.dumps(preview,indent=2));return
        if not args.reviewed_sha256: parser.error("Use --preview, then supply its --reviewed-sha256")
        print(json.dumps(restore_full(args.backup,password,args.data_dir,mapping,args.reviewed_sha256),indent=2));return
    restore_backup(args.backup, args.data_dir)
    print("Workspace restored. Provider credentials remain in the OS vault; project files were not changed.")
