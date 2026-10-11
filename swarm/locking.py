"""One coordinator per data directory; OS locks release even after a crash."""
import os
from pathlib import Path


class WorkspaceLock:
    def __init__(self, root):
        self.root, self.stream = Path(root), None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.stream = (self.root / "engine.lock").open("a+b")
        self.stream.seek(0, 2)
        if not self.stream.tell():
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.stream.close()
            raise ValueError("Another engine owns this workspace. Close it before starting or restoring.") from error
        return self

    def __exit__(self, *_):
        if os.name == "nt":
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream, fcntl.LOCK_UN)
        self.stream.close()
