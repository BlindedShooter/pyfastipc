from __future__ import annotations

import os
import time
from multiprocessing import Pipe, get_context

import pytest

from fastipc.guarded_shared_memory import GuardedSharedMemory


def _child_hold_shm(name: str, size: int, conn):
    # Inherit FASTIPC_PID_DIR from parent; just open and hold
    shm = GuardedSharedMemory(name, size)
    try:
        conn.send("ready")
        conn.recv()  # wait for release
    finally:
        shm.close()
        conn.close()


@pytest.mark.bench_heavy
def test_created_and_cleanup_benchmark(benchmark, tmp_path, monkeypatch):
    """Benchmark create+close cycles and verify cleanup of PID dir."""
    pid_root = tmp_path / "pids_root"
    monkeypatch.setenv("FASTIPC_PID_DIR", str(pid_root))

    counter = {"i": 0}

    def create_and_close():
        i = counter["i"]
        counter["i"] = i + 1
        name = f"bench_gshm_{os.getpid()}_{int(time.time()*1e6)}_{i}"
        pdir = pid_root / f"{name}.pids"
        shm = GuardedSharedMemory(name, size=64)
        try:
            assert shm.created is True
            assert pdir.is_dir()
            files = list(pdir.iterdir())
            assert any(f.name == str(os.getpid()) for f in files if f.name.isdigit())
        finally:
            shm.close()
        assert not pdir.exists()

    benchmark.group = "GuardedSharedMemory:create_close"
    benchmark.pedantic(create_and_close, iterations=1, rounds=20)


@pytest.mark.bench_heavy
def test_pid_tracking_get_num_procs_benchmark(benchmark, tmp_path, monkeypatch):
    """Start a child once, then benchmark get_num_procs() while child alive."""
    pid_root = tmp_path / "pids_root3"
    monkeypatch.setenv("FASTIPC_PID_DIR", str(pid_root))

    name = f"bench_procs_{os.getpid()}_{int(time.time()*1e6)}"
    parent = GuardedSharedMemory(name, size=64)
    assert parent.created is True
    assert parent.get_num_procs() == 1

    parent_conn, child_conn = Pipe()
    ctx = get_context("fork")
    p = ctx.Process(target=_child_hold_shm, args=(name, 64, child_conn))
    p.start()
    assert parent_conn.recv() == "ready"

    # Allow small time for child to register
    for _ in range(50):
        if parent.get_num_procs() >= 2:
            break
        time.sleep(0.01)
    assert parent.get_num_procs() >= 2

    try:
        def poll_num():
            return parent.get_num_procs()

        benchmark.group = "GuardedSharedMemory:get_num_procs_with_child"
        result = benchmark(poll_num)
        assert result >= 1
    finally:
        parent_conn.send("exit")
        p.join(timeout=5)
        assert p.exitcode == 0
        parent.close()


def test_consistency_with_auto_cleanup_disabled(tmp_path, monkeypatch):
    """Verify that with auto-cleanup disabled, PID dir remains after close."""
    pid_root = tmp_path / "pids_root2"
    monkeypatch.setenv("FASTIPC_PID_DIR", str(pid_root))

    name = f"test_no_cleanup_{os.getpid()}_{int(time.time()*1e6)}"
    shm = GuardedSharedMemory(name, size=64, try_cleanup_on_exit=False)
    assert shm.created is True
    pdir = pid_root / f"{name}.pids"
    assert pdir.is_dir()
    files = list(pdir.iterdir())
    assert any(f.name == str(os.getpid()) for f in files if f.name.isdigit())
    del shm
    
    # Try to attach again on same name; should succeed
    shm2 = GuardedSharedMemory(name, size=64, try_cleanup_on_exit=True)
    assert shm2.created is False
    shm2.close()
    assert not pdir.exists()