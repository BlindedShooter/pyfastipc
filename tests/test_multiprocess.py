import os
import sys
import time
import multiprocessing as mp
from pathlib import Path

import pytest

if sys.platform != "linux":
    pytest.skip("Linux-only futex tests", allow_module_level=True)


def _ensure_pid_dir():
    pid_dir = Path(".fastipc_pids").resolve()
    pid_dir.mkdir(parents=True, exist_ok=True)
    os.environ["FASTIPC_PID_DIR"] = str(pid_dir)


# Top-level worker functions for spawn compatibility
def _worker_named_mutex(name: str, iters: int, counter) -> None:
    from fastipc import NamedMutex  # type: ignore

    m = NamedMutex(name)
    for _ in range(iters):
        while not m.acquire():
            pass
        counter.value += 1
        m.release()


def _poster_named_semaphore(name: str, n: int, posted) -> None:
    from fastipc import NamedSemaphore  # type: ignore

    s = NamedSemaphore(name, initial=0)
    for _ in range(n):
        s.post(1)
        posted.value += 1

    while s.value() > 0:
        time.sleep(0.01)  # wait for all posts to be consumed


def _waiter_named_semaphore(name: str, n: int, waited) -> None:
    from fastipc import NamedSemaphore  # type: ignore

    s = NamedSemaphore(name)
    c = 0
    deadline = time.perf_counter() + 5.0
    while c < n and time.perf_counter() < deadline:
        if s.wait(timeout=10_000_000):
            c += 1
            waited.value += 1


@pytest.mark.timeout(10)
def test_named_mutex_exclusion_multiprocess_spawn():
    _ensure_pid_dir()
    name = f"mtx_mp_{os.getpid()}_{time.time_ns()}"
    procs = max(2, min(4, (os.cpu_count() or 2)))
    per_proc = 10000

    ctx = mp.get_context("spawn")
    counter = ctx.Value("I", 0)

    ps = [
        ctx.Process(target=_worker_named_mutex, args=(name, per_proc, counter))
        for _ in range(procs)
    ]
    for p in ps:
        p.start()
        time.sleep(0.01)
    for p in ps:
        p.join(timeout=10)
    for p in ps:
        assert p.exitcode == 0
    assert counter.value == procs * per_proc


@pytest.mark.timeout(10)
def test_named_semaphore_basic_multiprocess_spawn():
    _ensure_pid_dir()
    name = f"sem_mp_{os.getpid()}_{time.time_ns()}"
    posts = 10000
    ctx = mp.get_context("spawn")

    posted = ctx.Value("I", 0)
    waited = ctx.Value("I", 0)
    p1 = ctx.Process(target=_poster_named_semaphore, args=(name, posts, posted))
    p2 = ctx.Process(target=_waiter_named_semaphore, args=(name, posts, waited))
    p1.start()
    time.sleep(0.01)
    p2.start()
    p1.join(timeout=10)
    p2.join(timeout=12)
    assert p1.exitcode == 0
    assert p2.exitcode == 0
    assert posted.value == posts
    assert waited.value == posts


@pytest.mark.timeout(15)
@pytest.mark.bench_heavy
def test_named_semaphore_multiprocess_benchmark(benchmark):
    _ensure_pid_dir()
    name = f"sem_mp_b_{os.getpid()}_{time.time_ns()}"
    posts = 5000
    ctx = mp.get_context("spawn")

    def poster_waiter_round():
        posted = ctx.Value("I", 0)
        waited = ctx.Value("I", 0)
        p1 = ctx.Process(target=_poster_named_semaphore, args=(name, posts, posted))
        p2 = ctx.Process(target=_waiter_named_semaphore, args=(name, posts, waited))
        p1.start(); time.sleep(0.01); p2.start()
        p1.join(timeout=10); p2.join(timeout=12)
        assert p1.exitcode == 0 and p2.exitcode == 0
        assert posted.value == posts and waited.value == posts

    benchmark.group = "NamedSemaphore:spawn_poster_waiter_round"
    benchmark.pedantic(poster_waiter_round, iterations=1, rounds=3)
