"""跨进程 COM/位置文件独占；操作系统释放锁不意味着位置可信。"""
from pathlib import Path
import hashlib
import os

DEFAULT_LOCK_DIR = Path(__file__).resolve().parents[3] / "output" / "hardware_locks"


class ResourceLease:
    def __init__(self, resources, *, directory=None):
        self.resources = tuple(sorted(set(str(r).casefold() for r in resources)))
        self.directory = Path(directory or DEFAULT_LOCK_DIR)
        self.handles = []

    def acquire(self):
        if self.handles:
            raise RuntimeError("RESOURCE_LEASE_ALREADY_ACQUIRED")
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            for resource in self.resources:
                path = self.directory / (hashlib.sha256(resource.encode()).hexdigest() + ".lock")
                stream = path.open("a+b")
                try:
                    stream.seek(0, 2)
                    if not stream.tell():
                        stream.write(b"0")
                        stream.flush()
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, IOError) as exc:
                    stream.close()
                    raise PermissionError("DEVICE_OR_POSITION_FILE_BUSY") from exc
                self.handles.append(stream)
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        for stream in reversed(self.handles):
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()
        self.handles.clear()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.close()


def device_resources(port, position_path):
    return ("com:" + str(port).strip().casefold(), "position:" + str(Path(position_path).resolve()).casefold())
