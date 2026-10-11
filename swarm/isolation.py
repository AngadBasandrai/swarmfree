"""Owner-selected Git worktrees and optional containerized named checks."""
import asyncio
import hashlib
import os
from pathlib import Path
import re
import shutil
from .providers import RouteError


async def command(argv, cwd=None, timeout=30, limit=200000):
    options = {"creationflags":0x08000000} if os.name == "nt" else {"start_new_session":True}
    process = await asyncio.create_subprocess_exec(*argv, cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **options)
    chunks, length = [], 0
    try:
        async with asyncio.timeout(timeout):
            while chunk := await process.stdout.read(4096):
                length += len(chunk)
                if length > limit: raise RouteError("Command output exceeds the inspection limit")
                chunks.append(chunk)
            await process.wait()
        text = b"".join(chunks).decode("utf-8",errors="replace")
        if process.returncode: raise RouteError("Configured command failed: " + text[-1000:])
        return text
    except TimeoutError as error:
        raise RouteError("Configured command timed out") from error
    finally:
        if process.returncode is None:
            import psutil
            try:
                parent = psutil.Process(process.pid)
                for child in parent.children(recursive=True):
                    try: child.kill()
                    except psutil.Error: pass
                parent.kill()
            except psutil.Error: pass
            await process.wait()


class Isolation:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root).resolve()
        self.locks = {}

    async def prepare(self, run, agent):
        if not run.get("isolated_worktrees"): return None
        existing = self.store.get("worktree", agent["id"])
        if existing:
            if not Path(existing["root"]).is_dir(): raise RouteError("Agent worktree is missing; inspect it before resuming")
            return existing
        lock = self.locks.setdefault(agent["id"],asyncio.Lock())
        async with lock:
            existing = self.store.get("worktree", agent["id"])
            if existing: return existing
            project = self.store.get("project",run.get("project_id", ""))
            if not project: raise RouteError("Separate coding environments need a selected Git project")
            if not shutil.which("git"): raise RouteError("Install Git to create agent worktrees")
            repo = Path((await command(["git","rev-parse","--show-toplevel"],project["root"])).strip()).resolve()
            if repo != Path(project["root"]).resolve(): raise RouteError("Select the Git repository root as the project directory")
            if (await command(["git","status","--porcelain"],repo)).strip():
                raise RouteError("Commit or stash project changes before creating isolated worktrees")
            head = (await command(["git","rev-parse","HEAD"],repo)).strip()
            key = hashlib.sha256(agent["id"].encode()).hexdigest()[:24]
            path = (self.root/"worktrees"/key).resolve()
            if not path.is_relative_to(self.root/"worktrees"): raise RouteError("Invalid worktree location")
            path.parent.mkdir(exist_ok=True)
            if path.exists(): raise RouteError("Unrecorded worktree exists; inspect it before creating another")
            await command(["git","worktree","add","--detach",str(path),head],repo)
            record = dict(id=agent["id"],agent_id=agent["id"],run_id=run["id"],project_id=project["id"],root=str(path),base_commit=head)
            self.store.put("worktree",agent["id"],record)
            self.store.event(run["id"],"worktree_created",agent_id=agent["id"],message="Separate working directory created at " + str(path))
            return record

    def project(self, project, agent):
        record = self.store.get("worktree",agent["id"])
        return dict(project,root=record["root"],original_root=project["root"]) if record else project

    def docker_argv(self, project, argv, name):
        image = project.get("container_image", "")
        if not image: return argv
        if not shutil.which("docker"): raise RouteError("Container checks require an installed, running Docker runtime")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9./:_@-]{0,240}",image): raise RouteError("Invalid container image")
        root = str(Path(project["root"]).resolve())
        if "," in root: raise RouteError("Container mount paths cannot contain commas")
        return ["docker","run","--rm","--pull=never","--name",name,"--network","none","--cap-drop","ALL",
                "--security-opt","no-new-privileges","--read-only","--pids-limit","64","--memory","1g","--cpus","1",
                "--tmpfs","/tmp:rw,size=64m","--mount",f"type=bind,source={root},target=/workspace",
                "--workdir","/workspace",image,*argv]

    async def collect(self, agent, workspace):
        tree = self.store.get("worktree",agent["id"])
        if not tree: raise RouteError("This agent has no worktree")
        project = self.store.get("project",tree["project_id"])
        effective = self.project(project,agent)
        paths = (await command(["git","diff","--name-only","HEAD"],tree["root"])).splitlines()
        paths += (await command(["git","ls-files","--others","--exclude-standard"],tree["root"])).splitlines()
        from .workspace import scoped_path
        run = self.store.get("run",agent["run_id"])
        staged = []
        tracked=set((await command(["git","ls-files"],tree["root"])).splitlines())
        for relative in list(dict.fromkeys(paths))[:100]:
            try:
                path = scoped_path(effective,relative)
                if path.is_file() and path.stat().st_size <= 120000:
                    original=await command(["git","show",tree["base_commit"]+":"+relative],tree["root"],limit=120000) if relative in tracked else None
                    staged.append(workspace.stage(effective,run,agent,relative,path.read_text(encoding="utf-8"),integration=True,baseline_original=original))
            except (OSError,UnicodeError,RouteError): continue
        return dict(artifacts=staged,note="Text files only; deletions/binary changes need manual Git review")
