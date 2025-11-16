import sys
import os
import time
import threading
from array import array

import pytest

if sys.platform != "linux":
    pytest.skip("Linux-only futex tests", allow_module_level=True)

from fastipc._primitives import Mutex  # type: ignore


@pytest.mark.timeout(10)
def test_mutex_exclusion_threads():
    # 64B header-required buffer
    m = Mutex(memoryview(bytearray(64)))
    inside = 0
    max_inside = 0
    iters = 3000
    threads = max(8, (os.cpu_count() or 8))
    start = threading.Barrier(threads)

    def worker():
        nonlocal inside, max_inside
        start.wait()
        for _ in range(iters):
            time.sleep(0)
            assert m.acquire()
            inside += 1
            assert inside == 1
            max_inside = max(max_inside, inside)
            inside -= 1
            m.release()
            time.sleep(0)

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert max_inside == 1


@pytest.mark.timeout(5)
def test_mutex_nonblocking_and_context_manager():
    m = Mutex(memoryview(bytearray(64)))
    assert m.acquire() is True
    assert m.try_acquire() is False
    m.release()


@pytest.mark.timeout(5)
@pytest.mark.bench_heavy
def test_mutex_acquire_release_benchmark(benchmark):
    m = Mutex(memoryview(bytearray(64)))

    def acquire_release_n():
        for _ in range(5000):
            assert m.acquire()
            m.release()

    benchmark.group = "Mutex:acquire_release_single_thread"
    benchmark.pedantic(acquire_release_n, iterations=1, rounds=10)
    with m:
        assert m.try_acquire() is False
    assert m.acquire() is True
    m.release()


@pytest.mark.timeout(5)
def test_mutex_acquire_ns_timeout():
    m = Mutex(memoryview(bytearray(64)))
    held = threading.Event()
    release = threading.Event()

    def holder():
        assert m.acquire()
        held.set()
        release.wait(timeout=1.0)
        m.release()

    t = threading.Thread(target=holder)
    t.start()
    held.wait(timeout=1.0)
    # Should time out quickly while lock is held
    assert m.acquire_ns(timeout_ns=5_000_000) is False
    # Release and then acquire should succeed
    release.set()
    t.join(timeout=1.0)
    assert m.acquire_ns(timeout_ns=50_000_000) is True
    m.release()
