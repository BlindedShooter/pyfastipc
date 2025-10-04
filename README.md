fastipc — Fast Machine-level Sync for Python
=============================================

Fast IPC synchronization primitives in C11/CPython extension, with:
- Explicit acquire/release semantics and a focus on latency and throughput. 
- Primitives operate on caller-supplied buffer (thus **machine-level**)
- Comes with Named helpers for easier usage out of the box.

Status: Linux-only (futex backed). Python >= 3.9. Targets x86_64 and other Linux archs.

## Installation
```bash
pip install fastipc
```

## Why fastipc
- Minimal hot-path: pure atomics for uncontended operations; futex syscall only on contention.
- Buffer-backed: pass a 4-byte aligned `memoryview` to operate in threads or across processes.
- Strict memory ordering: acquire/release semantics on loads, stores, and CAS.
- Simple helpers: `NamedEvent`, `NamedMutex`, `NamedSemaphore` for quick cross‑process usage.

## Core Primitives
- `FutexWord`: raw futex wait/wake on a 32‑bit word.
- `AtomicU32` / `AtomicU64`: atomic load/store/CAS on a shared word.
- `Mutex`: futex‑based mutex with spin‑then‑sleep contention path.
- `Semaphore`: futex‑based counting semaphore with exact‑delivery wakeups.


## Cross‑Process: Buffer‑backed
```python
from multiprocessing import shared_memory
from fastipc import FutexWord, Mutex, Semaphore

shm = shared_memory.SharedMemory(create=True, size=4)
try:
    fw = FutexWord(shm.buf, shared=True)
    # Other processes attach via SharedMemory(name=...) and reuse the same buffer
finally:
    shm.close(); shm.unlink()

# Mutex and Semaphore require 64 bytes
shm = shared_memory.SharedMemory(create=True, size=64)
try:
    mtx = Mutex(shm.buf)
    with mtx:
        ...  # critical section
finally:
    shm.close(); shm.unlink()
```

## Cross‑Process: Named Helpers
These helpers use a shared‐memory word under the hood, plus a small PID‑tracking directory for safe cleanup.

```python
from fastipc import NamedEvent, NamedMutex, NamedSemaphore

# Event
evt = NamedEvent("job_ready")
evt.set()          # wake all waiters
evt.clear()        # reset
evt.wait(1_000_000_000)  # wait with 1s timeout (ns)

# Mutex
mtx = NamedMutex("global_lock")
with mtx:
    ...

# Semaphore
sem = NamedSemaphore("queue_slots", initial=0)
sem.post(3)
sem.wait()         # blocks if no tokens
```

Notes:
- PID tracking directory defaults to `/dev/shm/fastipc`. In restricted environments, set `FASTIPC_PID_DIR=/tmp/fastipc` (or any writable dir).

## Performance Notes
- Uncontended paths use only atomics (no syscalls).
- Under contention, primitives spin briefly (adaptive) then `futex` sleep to minimize wake storms and context switches.
- `Semaphore.post(n)` atomically adds `n` tokens and only wakes waiters when the count transitions from 0; the wake hint is capped at `min(n, INT_MAX)` to stay portable.
- Expect on-par or better performance than `posix_ipc` and `multiprocessing` alternatives in most scenarios. 

## Platform Support
- Linux only (uses `linux/futex.h`).
- Wheels target manylinux/musllinux for x86_64 and aarch64. Some arches may require `-latomic` (handled during build).


## AI Code Generation
Most of the writing of this library was assisted by AI (OpenAI Codex). The code was iteratively refined and tested to ensure correctness and performance.
