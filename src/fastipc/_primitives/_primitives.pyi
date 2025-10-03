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
        Initialize a mutex over a 64-byte header.

        Args:
            buffer: The memory buffer to use. Must be writable, 4-byte aligned, and at least 64 bytes.
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

    def acquire_ns(self, timeout_ns: int = -1, spin: int = 16) -> bool:
        """
        Acquire the mutex with an optional timeout and spin.

        Args:
            timeout_ns: Timeout in nanoseconds (-1 = infinite, 0 = non-blocking).
            spin: Number of spin attempts before blocking on futex.

        Returns:
            True if acquired, False if timed out or would block when timeout_ns==0.
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

    def force_release(self) -> None:
        """
        Forcibly unlock the mutex regardless of ownership and wake one waiter if contended.
        Intended for recovery from crashed owners.
        """
        ...

    def owner_pid(self) -> int:
        """Return the current owner PID, or 0 if unlocked."""
        ...

    def last_acquired_ns(self) -> int:
        """Return CLOCK_REALTIME nanoseconds of the last successful acquire."""
        ...

    def magic(self) -> int:
        """Return the magic constant identifying the header ('MUTX')."""
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
        Initialize a semaphore over a 64-byte header.

        Args:
            buffer: The memory buffer to use. Must be writable, 4-byte aligned, and at least 64 bytes.
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

    def last_acquired_ns(self) -> int:
        """Return CLOCK_REALTIME nanoseconds of the last successful wait (token acquisition)."""
        ...

    def last_pid(self) -> int:
        """Return PID of the last process/thread that acquired a token."""
        ...

    def magic(self) -> int:
        """Return the magic constant identifying the header ('SEMA')."""
        ...
