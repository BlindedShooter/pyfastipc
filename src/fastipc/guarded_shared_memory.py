import atexit
import ctypes
import ctypes.util
import errno
import mmap
import os
import random
import time
from typing import Optional

__all__ = ["GuardedSharedMemory"]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError as e:
        return e.errno == errno.EPERM


def _load_posix_shm_lib() -> ctypes.CDLL:
    """Return a libc/librt handle that exposes shm_open/shm_unlink."""

    candidates = []
    try:
        candidates.append(ctypes.CDLL(None, use_errno=True))
    except OSError:
        pass

    librt = ctypes.util.find_library("rt")
    if librt:
        try:
            candidates.append(ctypes.CDLL(librt, use_errno=True))
        except OSError:
            pass

    libc = ctypes.util.find_library("c")
    if libc:
        try:
            lib = ctypes.CDLL(libc, use_errno=True)
        except OSError:
            pass
        else:
            if lib not in candidates:
                candidates.append(lib)

    for lib in candidates:
        if getattr(lib, "shm_open", None) and getattr(lib, "shm_unlink", None):
            return lib
    raise RuntimeError("POSIX shared memory APIs not available on this platform")


_POSIX_SHM = _load_posix_shm_lib()

_shm_open = _POSIX_SHM.shm_open
_shm_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint]
_shm_open.restype = ctypes.c_int

_shm_unlink = _POSIX_SHM.shm_unlink
_shm_unlink.argtypes = [ctypes.c_char_p]
_shm_unlink.restype = ctypes.c_int


def _shm_open_wrapped(name: bytes, flags: int, mode: int) -> int:
    ctypes.set_errno(0)
    fd = _shm_open(name, flags, mode)
    if fd != -1:
        return fd
    err = ctypes.get_errno()
    if err == errno.ENOENT:
        raise FileNotFoundError(errno.ENOENT, os.strerror(err), name.decode())
    if err == errno.EEXIST:
        raise FileExistsError(errno.EEXIST, os.strerror(err), name.decode())
    raise OSError(err, os.strerror(err))


def _shm_unlink_wrapped(name: bytes) -> None:
    ctypes.set_errno(0)
    if _shm_unlink(name) == 0:
        return
    err = ctypes.get_errno()
    if err in (errno.ENOENT,):
        return
    raise OSError(err, os.strerror(err))


class NoShmFoundError(Exception):
    pass


class GuardedSharedMemory:
    """
    attach or create a shared memory segment with PID tracking.
    This class ensures that the shared memory segment is created or attached
    safely, handling potential race conditions and errors.
    """

    def __init__(
        self,
        name: str,
        size: int,
        attach_only: bool = False,
        *,
        pid_dir: str = "/dev/shm/fastipc",
        max_attempts: int = 128,
        backoff_base: float = 0.002,
        try_cleanup_on_exit: bool = True,
    ) -> None:
        """
        Initialize a guarded shared memory segment.
        Attempts to clean up when the process exits,
        via PID tracking.

        Args:
            name: The name of the shared memory segment.
            size: The size of the shared memory segment.
            attach_only: Whether to only attach to an existing shared memory segment. Raise an error if the segment does not exist.
            *
            pid_dir: The base directory for PID files.
            max_attempts: The maximum number of attempts to create/attach the segment.
            backoff_base: The base backoff time (in seconds) for retrying failed attempts.
            try_cleanup_on_exit: Whether to attempt cleanup on object deletion or program exit.
        """
        if size <= 0:
            raise ValueError("size must be a positive integer")

        # Normalize name for POSIX shm (leading slash required) but keep exposed form.
        if not name:
            raise ValueError("name must be a non-empty string")
        if "/" in name:
            # POSIX shared memory names cannot contain '/' aside from leading position.
            raise ValueError("name cannot contain '/' characters")

        self._name = name
        self._posix_name = f"/{name}"
        self._posix_name_b = self._posix_name.encode("utf-8")
        self._try_cleanup_on_exit = try_cleanup_on_exit

        self._pid = os.getpid()
        self._size = size
        self._fd: Optional[int] = None
        self._mmap: Optional[mmap.mmap] = None
        self._buf: Optional[memoryview] = None
        self._closed = False
        self._unlinked = False

        pid_root = os.environ.get("FASTIPC_PID_DIR", pid_dir)
        self._pdir = f"{pid_root.rstrip('/')}/{name}.pids"
        os.makedirs(self._pdir, exist_ok=True)

        last_err = None
        self.created = False

        for _ in range(max_attempts):
            fd: Optional[int] = None
            created = False
            try:
                try:
                    fd = _shm_open_wrapped(self._posix_name_b, os.O_RDWR, 0o600)
                except FileNotFoundError as e:
                    if attach_only:
                        raise NoShmFoundError(
                            f"Shared memory segment '{name}' does not exist"
                        ) from e
                    fd = _shm_open_wrapped(
                        self._posix_name_b,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    os.ftruncate(fd, size)
                    created = True

                stat_result = os.fstat(fd)
                actual_size = stat_result.st_size
                if actual_size < size:
                    raise ValueError(
                        f"Existing shm '{name}' size {actual_size} < requested {size}"
                    )

                mm = mmap.mmap(fd, actual_size, access=mmap.ACCESS_WRITE)
                self._fd = fd
                self._mmap = mm
                self._buf = memoryview(mm)
                self._size = actual_size
                self.created = created
                break
            except (FileNotFoundError, FileExistsError, ValueError) as e:
                last_err = e
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    if created:
                        try:
                            _shm_unlink_wrapped(self._posix_name_b)
                        except OSError:
                            pass
                time.sleep(backoff_base * (1 + random.random()))
            except Exception:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    if created:
                        try:
                            _shm_unlink_wrapped(self._posix_name_b)
                        except OSError:
                            pass
                raise
        else:
            raise RuntimeError(
                f"Failed to attach/create shm '{name}' after {max_attempts} attempts"
            ) from last_err

        if self._try_cleanup_on_exit:
            atexit.register(self.close)
        
        with open(f"{self._pdir}/{self._pid}", "w"):
            pass

    def get_num_procs(self) -> int:
        """
        Get the number of processes currently using the shared memory segment.
        """
        try:
            return len(os.listdir(self._pdir))
        except FileNotFoundError:
            return 0

    def close(self) -> None:
        """
        Check if any processes are still using the shared memory segment.
        Unlink the shared memory segment if it is no longer in use.
        """
        if self._closed:
            return

        self._cleanup_dead_pids()
        self._remove_pid_file()

        if self._has_other_alive_pids():
            self._detach()
            self._closed = True
            self._unregister_atexit()
            return

        try:
            self.unlink()
        except OSError:
            pass

        try:
            os.rmdir(self._pdir)
        except OSError:
            pass

        self._detach()
        self._closed = True
        self._unregister_atexit()

    def unlink(self) -> None:
        if self._unlinked:
            return
        _shm_unlink_wrapped(self._posix_name_b)
        self._unlinked = True

    def __enter__(self) -> "GuardedSharedMemory":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        if self._try_cleanup_on_exit:
            try:
                self.close()
            except Exception:
                pass
        self._unregister_atexit()

    @property
    def buf(self) -> memoryview:
        if self._buf is None:
            raise ValueError("Shared memory is closed")
        return self._buf

    @property
    def size(self) -> int:
        return self._size

    @property
    def name(self) -> str:
        return self._name

    def _cleanup_dead_pids(self) -> None:
        try:
            for fn in os.listdir(self._pdir):
                if fn.isdigit() and not _alive(int(fn)):
                    try:
                        os.unlink(f"{self._pdir}/{fn}")
                    except Exception:
                        pass
        except FileNotFoundError:
            pass

    def _remove_pid_file(self) -> None:
        try:
            os.unlink(f"{self._pdir}/{self._pid}")
        except FileNotFoundError:
            pass

    def _has_other_alive_pids(self) -> bool:
        try:
            for fn in os.listdir(self._pdir):
                if fn.isdigit():
                    pid = int(fn)
                    if pid != self._pid and _alive(pid):
                        return True
        except FileNotFoundError:
            pass
        return False

    def _detach(self) -> None:
        if self._buf is not None:
            try:
                self._buf.release()
            except AttributeError:
                pass
            self._buf = None
        if self._mmap is not None:
            try:
                self._mmap.close()
            except Exception:
                pass
            self._mmap = None
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def _unregister_atexit(self) -> None:
        if not self._try_cleanup_on_exit:
            return
        try:
            atexit.unregister(self.close)
        except Exception:
            pass
