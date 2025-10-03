from __future__ import annotations

from types import TracebackType
from typing import Optional, Type

class FutexWord:
    """
    A buffer-backed futex word. The buffer must be a writable, aligned buffer.
    """
    def __init__(self, buffer: memoryview, shared: bool = True) -> None: 
        """
        Initialize a buffer-backed futex word.

        Args:
            buffer: The memory buffer to use. Must be a writable, aligned buffer.
            shared: Whether the futex word is shared between processes.
        """
        ...

    def wait(self, expected: int, timeout_ns: int = -1) -> bool:
        """
        Wait until the futex word equals the expected value.

        Args:
            expected: The integer value to wait for. The wait continues
                until the futex word is equal to this value.
            timeout_ns: Timeout in nanoseconds. If set to -1, the call
                blocks indefinitely. Must be non-negative or -1.

        Returns:
            True if the futex word matched the expected value before
            the timeout expired. False if the wait timed out.
        """
    
    def wake(self, n: int = 1) -> int:
        """
        Wake up to n waiting threads.

        Args:
            n: The number of threads to wake up.

        Returns:
            The number of threads that were actually woken up.
        """
        ...

    def load_acquire(self) -> int:
        """
        Atomically load the value of the futex word with acquire semantics.

        Returns:
            The current value of the futex word.
        """
        ...

    def store_release(self, v: int) -> None:
        """
        Atomically store a new value to the futex word with release semantics.

        Args:
            v: The new value to store.
        """
        ...

class AtomicU32:
    """
    A buffer-backed atomic 32-bit unsigned integer. The buffer must be a writable, aligned buffer.
    """
    def __init__(self, buffer: memoryview) -> None:
        """
        Initialize an atomic 32-bit unsigned integer.

        Args:
            buffer: The memory buffer to use. Must be a writable, aligned buffer.
        """
        ...

    def load(self) -> int:
        """
        Atomically load the current value.

        Returns:
            The current value.
        """
        ...

    def store(self, v: int) -> None:
        """
        Atomically store a new value.

        Args:
            v: The new value to store.
        """
        ...

    def cas(self, expected: int, new: int) -> bool:
        """
        Atomically compare and swap the current value.

        Args:
            expected: The expected current value.
            new: The new value to set.

        Returns:
            True if the swap was successful, False otherwise.
        """
        ...

class AtomicU64:
    """
    A buffer-backed atomic 64-bit unsigned integer. The buffer must be a writable, aligned buffer.
    """
    def __init__(self, buffer: memoryview) -> None:
        """
        Initialize an atomic 64-bit unsigned integer.

        Args:
            buffer: The memory buffer to use. Must be a writable, aligned buffer.
        """
        ...

    def load(self) -> int:
        """
        Atomically load the current value.

        Returns:
            The current value.
        """
        ...

    def store(self, v: int) -> None:
        """
        Atomically store a new value. (store_release)

        Args:
            v: The new value to store.
        """
        ...
    def cas(self, expected: int, new: int) -> bool:
        """
        Atomically compare and swap the current value.

        Args:
            expected: The expected current value.
            new: The new value to set.

        Returns:
            True if the swap was successful, False otherwise.
        """
        ...

class Mutex:
    """
    A buffer-backed mutex. The buffer must be a writable, aligned buffer.
    """
    def __init__(self, buffer: memoryview, shared: bool = True) -> None:
        """
        Initialize a mutex.

        Args:
            buffer: The memory buffer to use. Must be a writable, aligned buffer.
            shared: Whether the mutex is shared between threads.
        """
        ...

    def acquire(self) -> bool:
        """
        Acquire the mutex.

        Returns:
            True if the mutex was acquired, False if it was already held.
        """
        ...

    def try_acquire(self) -> bool:
        """
        Try to acquire the mutex without blocking.

        Returns:
            True if the mutex was acquired, False if it was already held.
        """
        ...

    def release(self) -> None:
        """
        Release the mutex.
        """
        ...

    def __enter__(self) -> "Mutex":
        """
        Enter the runtime context related to this object.

        Returns:
            The mutex object.
        """
        ...

    def __exit__(
        self,
        exc_type: Type[BaseException],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None: ...

class Semaphore:
    """
    A buffer-backed semaphore. The buffer must be a writable, aligned buffer.
    """
    def __init__(
        self, buffer: memoryview, initial: int | None = None, shared: bool = True
    ) -> None:
        """
        Initialize a semaphore.

        Args:
            buffer: The memory buffer to use. Must be a writable, aligned buffer.
            initial: Optional initial value for a newly created semaphore.
                If None, the value is not modified (attach-only semantics).
            shared: Whether the semaphore is shared between threads.
        """
        ...

    def post(self, n: int = 1) -> None:
        """
        Signal the semaphore, incrementing its value.

        Args:
            n: The number of units to increment the semaphore by.
        """
        ...

    def post1(self) -> None:
        """
        Signal the semaphore, incrementing its value by 1.
        """
        ...

    def wait(
        self, blocking: bool = True, timeout_ns: int = -1, spin: int = 128
    ) -> bool:
        """
        Wait for the semaphore to become available.

        Args:
            blocking: Whether to block until the semaphore is available.
            timeout_ns: The maximum time to wait, in nanoseconds.
            spin: The number of spin attempts before blocking.

        Returns:
            True if the semaphore was acquired, False if the timeout was reached.
        """
        ...

    def value(self) -> int:
        """
        Get the current value of the semaphore.

        Returns:
            The current value of the semaphore.
        """
        ...
