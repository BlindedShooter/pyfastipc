import atexit
import errno
import os
import random
import sys
import time
from multiprocessing import resource_tracker
from multiprocessing.shared_memory import SharedMemory

__all__ = ["GuardedSharedMemory"]


SHM_TRACK_SUPPORTED = sys.version_info >= (3, 13)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError as e:
        return e.errno == errno.EPERM


class GuardedSharedMemory(SharedMemory):
    """
    attach or create a shared memory segment with PID tracking.
    This class ensures that the shared memory segment is created or attached
    safely, handling potential race conditions and errors.
    """

    def __init__(
        self,
        name: str,
        size: int,
        *,
        pid_dir: str = "/dev/shm/fastipc",
        max_attempts: int = 128,
        backoff_base: float = 0.002,
    ) -> None:
        """
        Initialize a guarded shared memory segment.
        Attempts to clean up when the process exits,
        via PID tracking.

        Args:
            name: The name of the shared memory segment.
            size: The size of the shared memory segment.
            base_dir: The base directory for PID files.
            max_attempts: The maximum number of attempts to create/attach the segment.
            backoff_base: The base backoff time (in seconds) for retrying failed attempts.
        """
        self._g_name = name
        self._g_size = size
        self._pid = os.getpid()
        # Allow overriding pid dir for restricted environments (e.g., CI sandboxes)
        pid_root = os.environ.get("FASTIPC_PID_DIR", pid_dir)
        self._pdir = f"{pid_root.rstrip('/')}/{name}.pids"
        os.makedirs(self._pdir, exist_ok=True)

        last_err = None
        self.created = False
        for _ in range(max_attempts):
            try:
                # Try to attach
                try:
                    if SHM_TRACK_SUPPORTED:
                        super().__init__(name=name, create=False, track=False)
                    else:
                        super().__init__(name=name, create=False)
                except FileNotFoundError:
                    # Try to create if not found
                    try:
                        if SHM_TRACK_SUPPORTED:
                            super().__init__(
                                name=name, create=True, size=size, track=False
                            )
                        else:
                            super().__init__(name=name, create=True, size=size)
                        self.created = True
                    except FileExistsError:
                        # Retry if another process created it
                        raise
                # Validate size
                if self.size < size:
                    super().close()
                    raise ValueError(
                        f"Existing shm '{name}' size {self.size} < requested {size}"
                    )
                # unregister tracker if python < 3.13
                if not SHM_TRACK_SUPPORTED:
                    try:
                        resource_tracker.unregister(self._name, "shared_memory")  # type: ignore[attr-defined]
                    except KeyError:
                        pass
                break  # successful attach/create
            except (FileNotFoundError, FileExistsError, ValueError) as e:
                last_err = e
                # Race: short backoff before retrying
                time.sleep(backoff_base * (1 + random.random()))
        else:
            # Failed to escape loop
            raise RuntimeError(
                f"Failed to attach/create shm '{name}' after {max_attempts} attempts"
            ) from last_err

        atexit.register(self.close)
        # PID registration + atexit
        os.makedirs(self._pdir, exist_ok=True)
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
        # Clean up dead PIDs
        try:
            for fn in os.listdir(self._pdir):
                if fn.isdigit() and not _alive(int(fn)):
                    try:
                        os.unlink(f"{self._pdir}/{int(fn)}")
                    except FileNotFoundError:
                        pass
                    except KeyError:
                        pass
        except FileNotFoundError:
            pass

        # Remove my PID file
        try:
            os.unlink(f"{self._pdir}/{self._pid}")
        except FileNotFoundError:
            pass

        # If any PIDs remain, exit (other processes are still using the shm)
        try:
            for fn in os.listdir(self._pdir):
                if fn.isdigit() and _alive(int(fn)):
                    super().close()
                    return
        except FileNotFoundError:
            pass

        # If it looks like I was the last one, clean up
        try:
            super().unlink()
        except FileNotFoundError:
            pass
        except KeyError:
            pass
        try:
            os.rmdir(self._pdir)
        except OSError:
            pass
        super().close()

    def __enter__(self) -> "GuardedSharedMemory":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
        try:
            atexit.unregister(self.close)  # type: ignore[attr-defined]
        except Exception:
            pass
