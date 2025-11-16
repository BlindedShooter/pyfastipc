import sys
import os
import time
import threading

import pytest

if sys.platform != "linux":
    pytest.skip("Linux-only futex tests", allow_module_level=True)

from fastipc._primitives import Semaphore  # type: ignore


@pytest.mark.timeout(5)
def test_semaphore_solo_post_wait():
    s = Semaphore(memoryview(bytearray(64)), initial=0)
    n = 0
    start = time.perf_counter()
    while time.perf_counter() - start < 0.1:
        s.post(1)
        assert s.wait()
        n += 1
    assert n > 0


@pytest.mark.timeout(10)
def test_semaphore_light_contention_threads():
    threads = max(3, (os.cpu_count() or 3))
    s = Semaphore(memoryview(bytearray(64)), initial=0)
    stop = threading.Event()
    counts = [0] * (threads - 1)

    def poster():
        while not stop.is_set():
            s.post(1)

    def waiter(i):
        c = 0
        while not stop.is_set():
            if s.wait(timeout_ns=5_000_000):
                c += 1
        counts[i] = c

    ws = [
        threading.Thread(target=waiter, args=(i,), daemon=True)
        for i in range(threads - 1)
    ]
    for t in ws:
        t.start()
    p = threading.Thread(target=poster, daemon=True)
    p.start()
    time.sleep(0.2)
    stop.set()
    for t in ws:
        t.join(timeout=1)
    p.join(timeout=1)
    assert sum(counts) > 0


@pytest.mark.timeout(5)
@pytest.mark.bench_heavy
def test_semaphore_post_wait_benchmark(benchmark):
    s = Semaphore(memoryview(bytearray(64)), initial=0)

    def post_wait_n():
        for _ in range(5000):
            s.post(1)
            assert s.wait()

    benchmark.group = "Semaphore:post_wait_single_thread"
    benchmark.pedantic(post_wait_n, iterations=1, rounds=10)
