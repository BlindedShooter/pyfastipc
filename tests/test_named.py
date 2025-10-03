import os
import time
import threading
from multiprocessing import shared_memory, resource_tracker
from pathlib import Path

import pytest


def _shm_available() -> bool:
    try:
        shm = shared_memory.SharedMemory(create=True, size=4)
        # avoid resource_tracker noise on close
        resource_tracker.unregister(shm._name, "shared_memory")
        shm.close()
        shm.unlink()
        return True
    except Exception:
        return False


_SKIP_NAMED = not _shm_available()

from fastipc import NamedEvent, NamedMutex, NamedSemaphore


def _ensure_pid_dir():
    pid_dir = Path(".fastipc_pids").resolve()
    pid_dir.mkdir(parents=True, exist_ok=True)
    os.environ["FASTIPC_PID_DIR"] = str(pid_dir)


@pytest.mark.skipif(
    _SKIP_NAMED, reason="shared_memory denied; skipping Named* tests"
)
@pytest.mark.timeout(10)
def test_named_event_threads():
    _ensure_pid_dir()
    name = f"evt_{os.getpid()}_{time.time_ns()}"
    e1 = NamedEvent(name)
    e2 = NamedEvent(name)
    got = []

    def waiter():
        got.append(e2.wait(timeout_ns=500_000_000))

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.05)
    e1.set()
    t.join(timeout=1)
    assert got and got[0] is True
    assert e1.is_set() is True
    e2.clear()
    assert e1.is_set() is False


@pytest.mark.skipif(
    _SKIP_NAMED, reason="POSIX shared_memory denied; skipping Named* tests"
)
@pytest.mark.timeout(10)
def test_named_mutex_threads():
    _ensure_pid_dir()
    name = f"mtx_{os.getpid()}_{time.time_ns()}"
    m1 = NamedMutex(name)
    m2 = NamedMutex(name)
    inside = 0
    max_inside = 0

    def worker():
        nonlocal inside, max_inside
        for _ in range(2000):
            assert m1.acquire()
            inside += 1
            assert inside == 1
            time.sleep(0)
            max_inside = max(max_inside, inside)
            inside -= 1
            m2.release()

    ts = [threading.Thread(target=worker) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
       t.join(timeout=1)
    assert max_inside == 1


@pytest.mark.skipif(
    _SKIP_NAMED, reason="POSIX shared_memory denied; skipping Named* tests"
)
@pytest.mark.timeout(10)
def test_named_semaphore_threads():
    _ensure_pid_dir()
    name = f"sem_{os.getpid()}_{time.time_ns()}"
    s1 = NamedSemaphore(name, initial=0)
    # second attach should not reset value (pass None)
    s2 = NamedSemaphore(name)
    stop = threading.Event()
    counts = [0] * 4

    def waiter(i):
        c = 0
        while not stop.is_set():
            if s2.wait(timeout=0.05):
                c += 1
        counts[i] = c

    ws = [threading.Thread(target=waiter, args=(i,), daemon=True) for i in range(4)]
    for t in ws:
        t.start()
    start = time.perf_counter()
    while time.perf_counter() - start < 0.2:
        s1.post(1)
    stop.set()
    for t in ws:
        t.join(timeout=1)
    assert sum(counts) > 0


@pytest.mark.skipif(
    _SKIP_NAMED, reason="POSIX shared_memory denied; skipping Named* tests"
)
@pytest.mark.timeout(5)
def test_named_semaphore_attach_does_not_reset():
    _ensure_pid_dir()
    name = f"sem_init_{os.getpid()}_{time.time_ns()}"
    s1 = NamedSemaphore(name, initial=2)
    # Attach with no initial should not reset
    s2 = NamedSemaphore(name)
    assert s2.value() == 2


@pytest.mark.skipif(
    _SKIP_NAMED, reason="POSIX shared_memory denied; skipping Named* tests"
)
@pytest.mark.timeout(10)
@pytest.mark.bench_heavy
def test_named_mutex_benchmark(benchmark):
    _ensure_pid_dir()
    name = f"mtx_b_{os.getpid()}_{time.time_ns()}"
    m = NamedMutex(name)

    def acq_rel_n():
        for _ in range(3000):
            assert m.acquire()
            m.release()

    benchmark.group = "NamedMutex:acquire_release_single_process"
    benchmark.pedantic(acq_rel_n, iterations=1, rounds=10)


@pytest.mark.skipif(
    _SKIP_NAMED, reason="POSIX shared_memory denied; skipping Named* tests"
)
@pytest.mark.timeout(10)
@pytest.mark.bench_heavy
def test_named_semaphore_benchmark(benchmark):
    _ensure_pid_dir()
    name = f"sem_b_{os.getpid()}_{time.time_ns()}"
    s = NamedSemaphore(name, initial=0)

    def post_wait_n():
        for _ in range(3000):
            s.post(1)
            assert s.wait(timeout=0.1)

    benchmark.group = "NamedSemaphore:post_wait_single_process"
    benchmark.pedantic(post_wait_n, iterations=1, rounds=10)


@pytest.mark.skipif(
    _SKIP_NAMED, reason="POSIX shared_memory denied; skipping Named* tests"
)
@pytest.mark.timeout(10)
@pytest.mark.bench_heavy
def test_named_event_benchmark(benchmark):
    _ensure_pid_dir()
    name = f"evt_b_{os.getpid()}_{time.time_ns()}"
    e = NamedEvent(name)

    def set_clear_n():
        for _ in range(3000):
            e.set()
            e.clear()

    benchmark.group = "NamedEvent:set_clear_single_process"
    benchmark.pedantic(set_clear_n, iterations=1, rounds=10)
