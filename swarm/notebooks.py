"""Review-bound Python notebooks executed only in explicitly selected Docker images."""
import base64
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import shutil
import tempfile
import time
from pydantic import BaseModel, Field, model_validator
from .providers import RouteError
from .store import uid
from .isolation import command


class CellInput(BaseModel):
    kind: str = Field(pattern=r"^(markdown|python)$")
    content: str = Field(max_length=20000)


class NotebookInput(BaseModel):
    project_id: str = Field(max_length=80)
    title: str = Field(min_length=1, max_length=160)
    cells: list[CellInput] = Field(min_length=1, max_length=30)
    inputs: dict[str, str] = Field(default_factory=dict)
    image: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9./:_@-]{0,240}$")
    expected_revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def bounded(self):
        if len(json.dumps(self.model_dump()).encode()) > 300000: raise ValueError("Notebook exceeds 300 KB")
        if len(self.inputs) > 20: raise ValueError("At most twenty input files")
        for name in self.inputs:
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}\.(csv|json|txt)", name): raise ValueError("Input filenames must be simple CSV, JSON or TXT names")
        return self


def digest(row):
    return hashlib.sha256(json.dumps({k:row[k] for k in ("project_id", "cells", "inputs", "image")}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# The output filesystem is a bounded container tmpfs, never a writable host mount.
RUNNER = r'''
import base64, contextlib, io, json, pathlib, traceback
cells=json.loads(pathlib.Path('/inputs/notebook.json').read_text())['cells']
scope={'__name__':'__main__'}; results=[]
for cell in cells:
    if cell['kind']!='python': continue
    out=io.StringIO(); error=''
    try:
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(out):
            exec(compile(cell['content'],'<reviewed notebook>','exec'),scope)
    except BaseException: error=traceback.format_exc()[-6000:]
    results.append({'stdout':out.getvalue()[:24000],'error':error})
    if error: break
files=[]; total=0
for p in sorted(pathlib.Path('/outputs').glob('*')):
    if p.is_symlink() or not p.is_file() or p.suffix.lower() not in {'.png','.csv','.json','.txt'}: continue
    n=p.stat().st_size
    if n>2000000 or total+n>4000000: continue
    total+=n;files.append({'name':p.name,'data':base64.b64encode(p.read_bytes()).decode()})
print(json.dumps({'cells':results,'files':files}))
'''


class Notebooks:
    def __init__(self, store, root): self.store, self.root = store, root

    def save(self, form, id=""):
        with self.store.lock:
            if not self.store.get("project", form.project_id): raise RouteError("Project not found")
            old = self.store.get("notebook", id) if id else None
            if id and (not old or old["project_id"] != form.project_id): raise RouteError("Notebook not found in this project")
            if form.expected_revision != (old["revision"] if old else 0): raise RouteError("Notebook changed; refresh before saving")
            if not old and len(self.store.all("notebook")) >= 100: raise RouteError("Notebook limit reached")
            id = id or uid("notebook_")
            row = dict(**form.model_dump(exclude={"expected_revision"}), id=id, revision=(old["revision"] if old else 0)+1, updated_at=time.time())
            row["review_sha256"] = digest(row)
            return self.store.put("notebook", id, row)

    async def run(self, id, reviewed):
        row = self.store.get("notebook", id)
        if not row or digest(row) != reviewed: raise RouteError("Review the current code, inputs and image before executing")
        if not shutil.which("docker"): raise RouteError("Notebook execution requires an installed Docker runtime and a preinstalled Python image")
        # Resolve the tag once; prevent a tag changing between review and execution.
        image = (await command(["docker", "image", "inspect", "--format", "{{.Id}}", row["image"]], timeout=15)).strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image): raise RouteError("Cannot identify the installed image")
        from pathlib import Path
        name = "swarm-notebook-"+uid()
        with tempfile.TemporaryDirectory(prefix="notebook-", dir=self.root) as folder:
            root = Path(folder); (root/"notebook.json").write_text(json.dumps(row), encoding="utf-8")
            (root/"runner.py").write_text(RUNNER, encoding="utf-8")
            for filename, content in row["inputs"].items(): (root/filename).write_text(content, encoding="utf-8")
            if "," in str(root): raise RouteError("Container mount paths cannot contain commas")
            argv = ["docker", "run", "--rm", "--pull=never", "--name", name, "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--read-only", "--pids-limit", "64", "--memory", "1g", "--cpus", "1", "--tmpfs", "/tmp:rw,size=64m", "--tmpfs", "/outputs:rw,size=16m",
                "--mount", "type=bind,source="+str(root)+",target=/inputs,readonly", "--workdir", "/inputs", image, "python", "-I", "/inputs/runner.py"]
            try: output = await command(argv, timeout=120, limit=6000000)
            finally:
                # The name is generated by this job; cancellation never targets other containers.
                try: await command(["docker", "rm", "-f", name], timeout=10, limit=4000)
                except RouteError: pass
            try: result = json.loads(output)
            except ValueError as e: raise RouteError("Container did not return a valid notebook result") from e
            if not isinstance(result, dict) or not isinstance(result.get("cells"), list) or not isinstance(result.get("files"), list): raise RouteError("Invalid notebook result")
            files = []
            for f in result["files"][:20]:
                name = str(f.get("name", ""))
                if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", name) or name in {".", ".."}: raise RouteError("Unsafe notebook output filename")
                try: data = base64.b64decode(f["data"], validate=True)
                except (ValueError, KeyError) as e: raise RouteError("Invalid notebook output") from e
                if len(data) > 2000000: raise RouteError("Notebook output exceeds the limit")
                if name.endswith(".png"):
                    from PIL import Image
                    try:
                        with Image.open(io.BytesIO(data)) as img:
                            if img.format != "PNG" or img.width*img.height > 16000000: raise ValueError()
                            img.verify()
                    except Exception as e: raise RouteError("Invalid notebook plot") from e
                elif PurePosixPath(name).suffix not in {".csv", ".json", ".txt"}: raise RouteError("Unsupported notebook output")
                files.append(dict(name=name, data=f["data"], sha256=hashlib.sha256(data).hexdigest()))
            return dict(notebook_id=id, revision=row["revision"], reviewed_sha256=reviewed, image_digest=image,
                cells=result["cells"][:30], files=files, completed_at=time.time())

    def export(self, id):
        row = self.store.get("notebook", id)
        if not row: raise RouteError("Notebook not found")
        return dict(nbformat=4, nbformat_minor=5, metadata=dict(swarm=dict(image=row["image"], inputs=row["inputs"], review_sha256=digest(row))),
            cells=[dict(cell_type="code" if c["kind"] == "python" else "markdown", metadata={}, source=c["content"].splitlines(True),
                **(dict(execution_count=None, outputs=[]) if c["kind"] == "python" else {})) for c in row["cells"]])
