from __future__ import annotations

from fastipc._primitives import Semaphore
from fastipc.guarded_shared_memory import GuardedSharedMemory


class NamedSemaphore:
    """
    A named semaphore that uses a shared memory segment to track the semaphore state.
    This class is designed to be used across different processes.
    """

    def __init__(
        self,
        name: str,
        initial: int | None = None,
    ):
        """
        Initialize the NamedSemaphore with a shared memory segment.

        :param name: The name of the semaphore.
        :param initial: The initial value of the semaphore.
        """
        self._shm = GuardedSharedMemory(f"__pyfastipc_{name}", size=4)
        self._name = name
        # Only set initial value if we created the backing segment
        init_val = initial if getattr(self._shm, "created", False) else None
        self._semaphore = Semaphore(self._shm.buf, initial=init_val, shared=True)

    def post(self, n: int = 1) -> None:
        """
        Increment the semaphore, releasing it for other processes.

        :param n: The number of increments to apply.
        """
        self._semaphore.post(n)

    def post1(self) -> None:
        """
        Increment the semaphore, releasing it for other processes.
        """
        self._semaphore.post1()

    def wait(
        self, blocking: bool = True, timeout: float = -1.0, spin: int = 128
    ) -> bool:
        """
        Wait for the semaphore to be available.

        :param blocking: Whether to block until the semaphore is available.
        :param timeout: The maximum time to wait in seconds.
        :param spin: The number of spins before blocking.
        :return: True if the semaphore was acquired, False if it timed out.
        """
        return self.wait_ns(blocking, int(timeout * 1_000_000_000), spin)

    def wait_ns(
        self, blocking: bool = True, timeout_ns: int = -1, spin: int = 128
    ) -> bool:
        """
        Wait for the semaphore to be available.

        :param blocking: Whether to block until the semaphore is available.
        :param timeout_ns: The maximum time to wait in nanoseconds.
        :param spin: The number of spins before blocking.
        :return: True if the semaphore was acquired, False if it timed out.
        """
        return self._semaphore.wait(blocking, timeout_ns, spin)

    def value(self) -> int:
        """
        Get the current value of the semaphore.

        :return: The current value of the semaphore.
        """
        return self._semaphore.value()

    # Aliases
    P = wait
    acquire = wait
    V = post
    release = post
