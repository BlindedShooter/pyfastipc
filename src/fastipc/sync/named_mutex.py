from __future__ import annotations

import atexit

from fastipc._primitives import Mutex
from fastipc.guarded_shared_memory import GuardedSharedMemory


class NamedMutex:
    """
    A named, cross-process mutex backed by a 64-byte shared-memory header.

    Layout (MUTX):
    - magic: 'MUTX' at offset 0x00
    - state: futex word at offset 0x08 (0=unlocked,1=locked,2=contended)
    - owner_pid: PID of holder at 0x0C
    - last_acquired_ns: CLOCK_REALTIME (ns) of last successful acquire at 0x10

    Exposed helpers: force_release() for recovery, owner_pid(), last_acquired_ns().
    """

    def __init__(self, name: str) -> None:
        """
        Create or attach a 64B shared-memory header for this mutex.

        :param name: Symbolic name for the shared memory region.
        """
        self._name = name
        self._shm = GuardedSharedMemory(f"__pyfastipc_mutex_{name}", size=64)
        self._mutex = Mutex(self._shm.buf, shared=True)
        # Initialize header only if we created the backing segment
        if getattr(self._shm, "created", True):
            try:
                self._mutex.force_release()  # ensure initialized
                self._shm.buf[:64] = b"\x00" * 64
            except Exception:
                pass

    def acquire(self) -> bool:
        """
        Acquire the mutex, blocking until it is available.

        Returns:
            True if the mutex was acquired, False if it was interrupted.
        """
        return self._mutex.acquire()

    def acquire_ns(self, timeout_ns: int = -1, spin: int = 16) -> bool:
        """
        Acquire the mutex with timeout/spin.

        :param timeout_ns: Timeout in nanoseconds (-1 = infinite).
        :param spin: Spin attempts before blocking.
        :return: True if acquired, False if timed out.
        """
        return bool(self._mutex.acquire_ns(timeout_ns, spin))

    def try_acquire(self) -> bool:
        """
        Try to acquire the mutex without blocking.

        Returns:
            True if the mutex was acquired, False if it is already held.
        """
        return self._mutex.try_acquire()

    def release(self) -> None:
        """
        Release the mutex, making it available for other processes.
        """
        self._mutex.release()
    
    def try_release(self) -> bool:
        """
        Try to release the mutex.

        Returns:
            True if the mutex was released, False if the caller is not the owner.
        """
        try:
            self._mutex.release()
            return True
        except RuntimeError:
            return False

    def force_release(self) -> None:
        """
        Forcibly release the mutex, regardless of owner.
        Use to recover from crashed holders; wakes one waiter if contended.
        """
        self._mutex.force_release()

    # Metadata helpers
    def owner_pid(self) -> int:
        """Return the PID currently recorded as the owner, or 0 if unlocked."""
        return int(self._mutex.owner_pid())

    def last_acquired_ns(self) -> int:
        """Return CLOCK_REALTIME nanoseconds of the last successful acquire."""
        return int(self._mutex.last_acquired_ns())

    def __enter__(self) -> NamedMutex:
        """
        Enter the runtime context related to this object.

        :return: self
        """
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """
        Exit the runtime context related to this object.

        :param exc_type: The exception type, if any.
        :param exc_value: The exception value, if any.
        :param traceback: The traceback object, if any.
        """
        self.release()
