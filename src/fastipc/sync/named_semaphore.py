from __future__ import annotations

from fastipc._primitives import Semaphore
from fastipc.guarded_shared_memory import GuardedSharedMemory


class NamedSemaphore:
    """
    A named, cross-process semaphore backed by a 64-byte shared-memory header.

    Layout (SEMA):
    - magic: 'SEMA' at offset 0x00
    - count: futex word at offset 0x08
    - last_pid: PID of last successful waiter at 0x0C
    - last_acquired_ns: CLOCK_REALTIME (ns) of last successful wait at 0x10

    Exposed helpers: last_pid(), last_acquired_ns().
    """

    def __init__(
        self,
        name: str,
        initial: int | None = None,
    ):
        """
        Create or attach a 64B shared-memory header for this semaphore.

        :param name: Symbolic name for the shared memory region.
        :param initial: Initial count if we created the segment (attach-only otherwise).
        """
        self._shm = GuardedSharedMemory(f"__pyfastipc_sema_{name}", size=64)
        self._name = name
        # Only set initial value if we created the backing segment
        init_val = initial if getattr(self._shm, "created", False) else None
        self._semaphore = Semaphore(self._shm.buf, initial=init_val, shared=True)

    # Metadata helpers
    def last_acquired_ns(self) -> int:
        """Return CLOCK_REALTIME nanoseconds of the last successful wait."""
        return int(self._semaphore.last_acquired_ns())

    def last_pid(self) -> int:
        """Return PID of the last process/thread that acquired a token."""
        return int(self._semaphore.last_pid())

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
