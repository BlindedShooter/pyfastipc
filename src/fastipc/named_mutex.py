from __future__ import annotations

from fastipc._primitives import Mutex
from fastipc.guarded_shared_memory import GuardedSharedMemory


class NamedMutex:
    """
    A named mutex that uses a shared memory segment to track the lock state.
    This class is designed to be used across different processes.
    """

    def __init__(self, name: str) -> None:
        """
        Initialize the NamedMutex with a shared memory segment.

        :param name: The name of the mutex.
        :param size: The size of the shared memory segment.
        :param base_dir: The base directory for the shared memory segment.
        """
        self._name = name
        self._shm = GuardedSharedMemory(f"__pyfastipc_{name}", size=4)
        # Explicitly initialize the state word to 0 (unlocked)
        try:
            self._shm.buf[:4] = (0).to_bytes(4, "little")
        except Exception:
            pass
        self._mutex = Mutex(self._shm.buf, shared=True)

    def acquire(self) -> bool:
        """
        Acquire the mutex, blocking until it is available.

        Returns:
            True if the mutex was acquired, False if it was interrupted.
        """
        return self._mutex.acquire()

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
