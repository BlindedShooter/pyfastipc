from __future__ import annotations

from fastipc._primitives import FutexWord
from fastipc.guarded_shared_memory import GuardedSharedMemory


class NamedEvent:
    """
    A named event that uses a shared memory segment to track the event state.
    This class is designed to be used across different processes.
    """

    def __init__(self, name: str) -> None:
        """
        Initialize the NamedEvent with a shared memory segment.

        :param name: The name of the event.
        """
        self._shm = GuardedSharedMemory(f"__pyfastipc_event_{name}", size=4)
        self._name = name
        self._futex = FutexWord(self._shm.buf, shared=True)
        if self._shm.created:
            self._futex.store_release(0)

    def set(self) -> None:
        """
        Set the event, waking up any waiting processes.
        """
        self._futex.store_release(1)
        self._futex.wake(0x7FFFFFFF)  # wake all waiting processes

    def clear(self) -> None:
        """
        Clear the event, resetting its state.
        """
        self._futex.store_release(0)

    def wait(self, timeout_ns: int = -1) -> bool:
        """
        Wait for the event to be set.

        :param timeout_ns: The maximum time to wait in nanoseconds.
        :return: True if the event was set, False if it timed out.
        """
        return self._futex.wait(expected=0, timeout_ns=timeout_ns)

    def is_set(self) -> bool:
        """
        Check if the event is set.

        :return: True if the event is set, False otherwise.
        """
        return self._futex.load_acquire() != 0
