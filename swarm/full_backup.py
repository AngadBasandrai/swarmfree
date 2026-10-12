"""Encrypted database + attachments/project/worktree recovery with preview and remapping."""
import base64
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import sqlite3
import tempfile
import time
import zipfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from .locking import WorkspaceLock
from .providers import RouteError
from .workspace import BLOCKED,scoped_path

MAX_TOTAL=128*1024*1024
MAX_FILES=5000
PRIVATE_SUFFIXES={".pem",".key",".p12",".pfx",".kdbx"}


def key_for(password,salt):
    if not 12<=len(password)<=1024: raise RouteError("Use a backup passphrase of 12–1024 characters")
    return Scrypt(salt=salt,length=32,n=2**15,r=8,p=1).derive(password.encode())


def safe_name(name):
    value=PurePosixPath(name)
    if not name or "\\" in name or ":" in name or value.is_absolute() or any(p in {".",".."} or p.casefold() in BLOCKED or p.casefold().startswith(".env") for p in value.parts):
        raise RouteError("Unsafe backup member path")
    return value


def eligible_files(root):
    root=Path(root).resolve();inspected=0
    for directory,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[d for d in dirs if d.casefold() not in BLOCKED and not d.casefold().startswith(".env") and not (Path(directory)/d).is_symlink()]
        for name in files:
            if (Path(directory)/name).is_symlink():continue
            inspected+=1
            if inspected>10000: raise RouteError("Project file scan exceeds the backup limit")
            relative=(Path(directory)/name).relative_to(root).as_posix()
            try:
                target=scoped_path(dict(root=str(root)),relative)
                if target.is_symlink() or target.suffix.lower() in PRIVATE_SUFFIXES or target.stat().st_size>16*1024*1024: continue
                if any(word in name.casefold() for word in ("credential","secret","password")): continue
                yield relative,target
            except (OSError,RouteError): continue


def create_full_backup(root,store,password,project_ids):
    root=Path(root).resolve();members={};total=0
    def add(name,data):
        nonlocal total
        safe_name(name);total+=len(data)
        if total>MAX_TOTAL or len(members)>=MAX_FILES: raise RouteError("Backup exceeds 128 MiB / 5000 files; select fewer projects")
        members[name]=data
    with tempfile.TemporaryDirectory(prefix="swarm-full-backup-") as folder:
        for name in ("workbench.sqlite","checkpoints.sqlite"):
            target=Path(folder)/name
            source=store.db if name=="workbench.sqlite" else sqlite3.connect(root/name)
            try:
                with store.lock,closing(sqlite3.connect(target)) as dest: source.backup(dest)
                add("data/"+name,target.read_bytes())
            finally:
                if source is not store.db: source.close()
        projects=[];trees=[]
        for id in project_ids:
            project=store.get("project",id)
            if not project: raise RouteError("Selected backup project no longer exists")
            prefix="projects/"+hashlib.sha256(id.encode()).hexdigest()[:24]
            for rel,path in eligible_files(project["root"]): add(prefix+"/"+rel,path.read_bytes())
            projects.append(dict(id=id,name=project["name"],original_root=project["root"],prefix=prefix))
            for tree in store.all("worktree"):
                if tree["project_id"]!=id: continue
                tree_prefix="worktrees/"+hashlib.sha256(tree["id"].encode()).hexdigest()[:24]
                # Recover a snapshot, not a .git pointer back into a machine-specific repository.
                for rel,path in eligible_files(tree["root"]): add(tree_prefix+"/"+rel,path.read_bytes())
                trees.append(dict(id=tree["id"],project_id=id,prefix=tree_prefix,original_root=tree["root"]))
        for attachment in store.all("attachment"):
            path=Path(attachment["path"]).resolve()
            if not path.is_relative_to(root/"attachments") or not path.is_file(): raise RouteError("Attachment is missing or outside the data directory")
            add("data/attachments/"+path.name,path.read_bytes())
        manifest=dict(version=4,created_at=time.time(),projects=projects,worktrees=trees,
            files={name:dict(sha256=hashlib.sha256(data).hexdigest(),size=len(data)) for name,data in members.items()},
            exclusions=["OS credential vault","TLS/relay secrets","browser cookies","model weights","Git metadata","private/configuration folders, secret-named files, files over 16 MiB"])
        output=io.BytesIO()
        with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json",json.dumps(manifest))
            for name,data in members.items(): archive.writestr(name,data)
    salt,nonce=os.urandom(16),os.urandom(12)
    encrypted=AESGCM(key_for(password,salt)).encrypt(nonce,output.getvalue(),b"SwarmWorkbenchBackup4")
    envelope=dict(version=4,kdf="scrypt-32768-8-1",salt=base64.b64encode(salt).decode(),nonce=base64.b64encode(nonce).decode(),ciphertext=base64.b64encode(encrypted).decode())
    folder=root/"backups";folder.mkdir(exist_ok=True)
    path=folder/("swarm-full-"+str(time.time_ns())+".swarmbak")
    path.write_text(json.dumps(envelope,separators=(",",":")),encoding="utf-8");path.chmod(0o600)
    return dict(path=str(path),size=path.stat().st_size,files=len(members),projects=len(projects),exclusions=manifest["exclusions"])


def unpack(path,password):
    path=Path(path).resolve(strict=True)
    if path.stat().st_size>MAX_TOTAL*2: raise RouteError("Encrypted backup exceeds the import limit")
    try:
        envelope=json.loads(path.read_text(encoding="utf-8"))
        if envelope["version"]!=4 or envelope["kdf"]!="scrypt-32768-8-1": raise ValueError()
        salt=base64.b64decode(envelope["salt"],validate=True);nonce=base64.b64decode(envelope["nonce"],validate=True)
        if len(salt)!=16 or len(nonce)!=12: raise ValueError()
        data=AESGCM(key_for(password,salt)).decrypt(nonce,base64.b64decode(envelope["ciphertext"],validate=True),b"SwarmWorkbenchBackup4")
    except Exception as error: raise RouteError("Cannot decrypt this backup; check the passphrase and file integrity") from error
    if len(data)>MAX_TOTAL+4_000_000: raise RouteError("Decrypted archive exceeds the limit")
    members={}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info=archive.infolist()
        if len(info)>MAX_FILES+1 or sum(i.file_size for i in info)>MAX_TOTAL+1_000_000 or len({i.filename for i in info})!=len(info): raise RouteError("Invalid or oversized backup archive")
        for item in info:
            safe_name(item.filename)
            if item.file_size>MAX_TOTAL or item.is_dir(): raise RouteError("Invalid backup member")
        manifest=json.loads(archive.read("manifest.json"))
        if manifest.get("version")!=4 or set(archive.namelist())!={"manifest.json",*manifest["files"]}: raise RouteError("Backup manifest does not match its archive")
        for name,expected in manifest["files"].items():
            content=archive.read(name)
            if len(content)!=expected["size"] or hashlib.sha256(content).hexdigest()!=expected["sha256"]: raise RouteError("Backup member integrity check failed")
            members[name]=content
    if not {"data/workbench.sqlite","data/checkpoints.sqlite"}<=members.keys(): raise RouteError("Backup is missing its databases")
    return manifest,members


def preview_full(path,password,target,mapping=None):
    manifest,members=unpack(path,password);target=Path(target).resolve();mapping=mapping or {}
    if target.exists() and any(target.iterdir()): raise RouteError("Restore into a new or empty data directory")
    destinations={}
    for project in manifest["projects"]:
        destination=Path(mapping.get(project["id"]) or target/"recovered-projects"/project["prefix"].split("/")[-1]).resolve()
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())): raise RouteError("Mapped project directories must be new or empty")
        if destination==target or target.is_relative_to(destination): raise RouteError("A project cannot contain the restored data directory")
        destinations[project["id"]]=str(destination)
    paths=[Path(p) for p in destinations.values()]
    if any(a==b or a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(paths) for b in paths[i+1:]): raise RouteError("Mapped project directories cannot overlap")
    digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    review=hashlib.sha256(json.dumps(dict(digest=digest,target=str(target),mapping=destinations),sort_keys=True).encode()).hexdigest()
    return dict(sha256=review,source_sha256=digest,target=str(target),mapping=destinations,projects=manifest["projects"],worktrees=manifest["worktrees"],files=len(members),size=sum(map(len,members.values())),exclusions=manifest["exclusions"])


def restore_full(path,password,target,mapping,reviewed_sha256):
    preview=preview_full(path,password,target,mapping)
    if preview["sha256"]!=reviewed_sha256: raise RouteError("Restore inputs changed; preview the exact paths again")
    manifest,members=unpack(path,password);target=Path(preview["target"])
    with WorkspaceLock(target):
        # Inspect both databases before writing any destination.
        with tempfile.TemporaryDirectory(prefix="swarm-verify-backup-") as folder:
            for name in ("workbench.sqlite","checkpoints.sqlite"):
                temporary=Path(folder)/name;temporary.write_bytes(members["data/"+name])
                with closing(sqlite3.connect(temporary)) as db:
                    if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok": raise RouteError("Backup SQLite integrity check failed")
            # Lock creates a small lock file; all other existing entries still indicate a conflict.
            project_roots=preview["mapping"];tree_roots={row["original_root"]:str(target/"recovered-worktrees"/row["prefix"].split("/")[-1]) for row in manifest["worktrees"]}
            destinations={"data":target}
            destinations.update({p["prefix"]:Path(project_roots[p["id"]]) for p in manifest["projects"]})
            destinations.update({p["prefix"]:Path(tree_roots[p["original_root"]]) for p in manifest["worktrees"]})
            planned=[]
            for name,data in members.items():
                prefix=next((p for p in sorted(destinations,key=len,reverse=True) if name.startswith(p+"/")),None)
                if prefix is None: raise RouteError("Backup has an unknown file group")
                destination=destinations[prefix]/name[len(prefix)+1:]
                if destination.exists(): raise RouteError("Restore destination has an existing file")
                planned.append((destination,data))
            (target/".restore-in-progress").write_text("Recovery is incomplete until RESTORE_RECEIPT.json is written",encoding="utf-8")
            for destination,data in planned:
                destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(data)
            with closing(sqlite3.connect(target/"workbench.sqlite")) as db:
                original_projects={p["id"]:p for p in manifest["projects"]}
                for kind,id,encoded in db.execute("SELECT kind,id,value FROM records").fetchall():
                    row=json.loads(encoded)
                    if kind=="project":
                        if id in project_roots: row["root"]=project_roots[id]
                        else: row["tools"]=[];row["relink_required"]=True
                        row["execution_devices"]=[];row["browser_sites"]=[]
                    elif kind=="attachment": row["path"]=str(target/"attachments"/Path(row["path"]).name)
                    elif kind=="worktree":
                        if row["root"] in tree_roots: row["root"]=tree_roots[row["root"]];row["recovered_snapshot"]=True
                        else: db.execute("DELETE FROM records WHERE kind=? AND id=?",(kind,id));continue
                    elif kind=="artifact" and row.get("workspace_root"):
                        old=row["workspace_root"];project=original_projects.get(row["project_id"],{})
                        if old in tree_roots: row["workspace_root"]=tree_roots[old]
                        elif project.get("original_root")==old: row["workspace_root"]=project_roots[row["project_id"]]
                        else: row["status"]="relink_required"
                    elif kind in {"extension","runtime","automation","team_remote","a2a_endpoint"}: row["enabled"]=False
                    elif kind=="settings" and id=="team_remote": row["enabled"]=False
                    elif kind=="settings" and id=="voice": row["trusted"]=False
                    elif kind=="campaign": row["enabled"]=False;row["status"]="paused"
                    elif kind=="egress_review": row["status"]="blocked"
                    elif kind=="team_member": row["enabled"]=False;row.pop("token_hash",None)
                    elif kind=="device": row["approved"]=False;row["execution_approved"]=False
                    elif kind in {"invite","relay_link","semantic_index","browser_session","browser_action","companion_invite","tool_panel"}:
                        db.execute("DELETE FROM records WHERE kind=? AND id=?",(kind,id));continue
                    db.execute("UPDATE records SET value=? WHERE kind=? AND id=?",(json.dumps(row),kind,id))
                db.commit()
        (target/"RESTORE_RECEIPT.json").write_text(json.dumps(preview,indent=2),encoding="utf-8")
        (target/".restore-in-progress").unlink()
    return dict(restored=True,target=str(target),files=len(members),note="Start the desktop against this recovered directory. Reconnect credentials and explicitly re-enable trusted adapters.")
