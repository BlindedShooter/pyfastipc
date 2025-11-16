import sys
import pytest

from fastipc._primitives import Mutex, Semaphore, FutexWord  # type: ignore

if sys.platform != "linux":
    pytest.skip("Linux-only futex tests", allow_module_level=True)


def test_mutex_small_buffer_raises():
    with pytest.raises(ValueError):
        Mutex(memoryview(bytearray(4)))


def test_semaphore_small_buffer_raises():
    with pytest.raises(ValueError):
        Semaphore(memoryview(bytearray(4)))


def test_mutex_misaligned_buffer_raises():
    buf = bytearray(65)
    mv = memoryview(buf)[1:65]  # misaligned by 1 byte
    assert len(mv) == 64
    with pytest.raises(ValueError):
        Mutex(mv)


def test_semaphore_misaligned_buffer_raises():
    buf = bytearray(65)
    mv = memoryview(buf)[1:65]
    assert len(mv) == 64
    with pytest.raises(ValueError):
        Semaphore(mv)


def test_futexword_buffer_alignment():
    # FutexWord requires 4-byte aligned 4B buffer
    with pytest.raises(ValueError):
        FutexWord(memoryview(bytearray(3)))
    ok = memoryview(bytearray(4))
    # If this environment yields misalignment for bytearray(4), fallback
    try:
        FutexWord(ok)
    except ValueError:
        pytest.skip("bytearray alignment unsuitable on this platform")

