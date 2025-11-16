import sys
import threading
from array import array

import pytest

if sys.platform != "linux":
    pytest.skip("Linux-only futex tests", allow_module_level=True)

from fastipc._primitives import AtomicU32, AtomicU64  # type: ignore


@pytest.mark.timeout(10)
@pytest.mark.parametrize("kind", ["u32", "u64"])
def test_atomic_cas_increment_threads(kind: str):
    threads = 16
    per_thread = 3000
    if kind == "u32":
        buf = array("I", [0])
        a = AtomicU32(memoryview(buf))
    else:
        buf = array("Q", [0])
        a = AtomicU64(memoryview(buf))

    def worker():
        for _ in range(per_thread):
            while True:
                v = a.load()
                if a.cas(v, v + 1):
                    break

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert a.load() == threads * per_thread


@pytest.mark.timeout(5)
@pytest.mark.parametrize("kind", ["u32", "u64"])
@pytest.mark.bench_heavy
def test_atomic_cas_increment_benchmark(benchmark, kind: str):
    iters = 200_000
    if kind == "u32":
        buf = array("I", [0])
        a = AtomicU32(memoryview(buf))
    else:
        buf = array("Q", [0])
        a = AtomicU64(memoryview(buf))

    def cas_loop():
        i = 0
        while i < iters:
            v = a.load()
            if a.cas(v, v + 1):
                i += 1

    benchmark.group = f"Atomic:{kind}:cas_increment_single_thread"
    benchmark.pedantic(cas_loop, iterations=1, rounds=5)
