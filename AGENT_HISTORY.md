Session: 2025-08-14
Context
- Reviewed the fastipc codebase for design, performance, safety, and packaging.
- Did not modify code; provided a prioritized review and fix suggestions.
Key Findings
- Core C11 futex primitives are clean and correct: FutexWord, AtomicU32/U64, Mutex, Semaphore with proper memory ordering and futex usage.
- Adaptive spin exists but README toggle mismatches code: code uses FASTIPC_ADAPTIVE_SPIN, README mentions FASTIPC_NO_ADAPTIVE.
- Packaging mismatches: pyproject requires Python >=3.9; setup.py declares >=3.8.
- Unused/unsafe module: src/fastipc/shm_lease_guard.py imports and calls SharedMemory() at import-time and references sysv_ipc (undeclared dep). Risky and should be removed or guarded.
- __init__.py does not re-export AtomicU64 though implemented and documented in .pyi.
- setup.py links libatomic unconditionally; should be conditional per-arch.
- Minor doc/classifier issues; built artifacts present in repo; .so committed under src.
Suggested Fixes (not applied)
- Align env var: either implement FASTIPC_NO_ADAPTIVE or document FASTIPC_ADAPTIVE_SPIN; prefer the latter and update README.
- Make Python version consistent across pyproject/setup (pick >=3.9 unless 3.8 is required).
- Remove or guard src/fastipc/shm_lease_guard.py (no side effects on import; optional dependency).
- Re-export AtomicU64 in src/fastipc/__init__.py and README.
- Conditional -latomic in setup.py for non-x86 targets.
- Optionally use METH_FASTCALL on hot-path methods; keep no-arg fast-paths.
Next
- Await go-ahead to apply the above patches and run tests/benchmarks locally.

---

Session: 2025-08-14 (later)
Changes
- Added benchmarks/bench_posix_vs_fastipc_semaphore.py comparing fastipc.Semaphore and posix_ipc.Semaphore across solo, threaded, and spawn multiprocess runs.
- Added benchmarks/run_semaphore_compare_py310.sh convenience runner pinned to /home/jhlee/miniforge3/envs/py310/bin/python3.10.
Notes
- posix_ipc remains optional; the script skips those tests if the module is absent.
Follow-up Fix
- Promoted spawn worker helpers to module scope to satisfy multiprocessing spawn pickle requirements.

---

Session: 2025-08-17
Changes
- Fixed packaging/build blockers: corrected Extension name to fastipc._primitives._primitives and path to src/fastipc/_primitives/_primitives.c.
- Added pytest.ini for timeout marker registration and warning suppression.
- Standardized benchmark output formatting via benchmarks/_fmt.py and updates across mutex/semaphore/futex scripts.
Validation
- Built the extension in place and ran subset with PYTHONPATH=./src (10 passed, 1 skipped, 11 deselected).
- Verified benchmark formatter with benchmarks/bench_mutex.py --format pretty/json.
Mutex/Named* Fixes
- Corrected Mutex release path (atomic_exchange + futex wake on contention) and removed NamedMutex early release, zeroing the shared word explicitly.
- GuardedSharedMemory gained FASTIPC_PID_DIR override for sandbox-friendly PID dirs.
- Added tests/test_named_primitives.py covering named primitives; auto-skips when shared memory is unavailable.
Validation (post-fixes)
- PYTHONPATH=./src python -m pytest -q -k "strict or primitives or named_primitives" → 10 passed, 2 skipped, 11 deselected.
Docs & Wheels
- Expanded README.md with Named primitives, cross-process usage, env toggles, and dev notes.
- Added cibuildwheel targets in pyproject.toml and GitHub Actions workflow for wheels/sdist.

---

Session: 2025-08-17 (later) — TicketMutex + Unified Header
Changes
- Introduced 64B fastipc_header_t shared layout reused by all primitives with magic/flags/seq/count/serving/waiters fields.
- Upgraded Mutex to ticket-based acquisition when given the 64B header while preserving legacy 4B compatibility.
- Adapted Semaphore, NamedMutex, NamedSemaphore, and NamedEvent to the unified header and ensured ticket path hits benchmarks.
Validation
- Rebuilt extension and ran PYTHONPATH=src tests (6 passed, 1 skipped).
Notes
- NamedEvent now uses versioned seq futex waits; future work could ticketize Semaphore for FIFO fairness.

---

Session: 2025-08-17 (later) — Revert header + Smooth quantiles
Changes
- Reverted unified-header/TicketMutex changes across C, Named*, and benchmarks reinstating 4-byte layouts.
- Added smoothed percentile estimation to benchmarks/_fmt.py and updated related benchmark scripts with corrected bucket math.
Validation
- Rebuilt extension; PYTHONPATH=src tests still 6 passed, 1 skipped.

---

Session: 2025-08-17 (later) — Cache-line separation in mutex MP bench
Changes
- Adjusted benchmarks/bench_mutex_multiprocess.py to place counter at offset 64, isolating it from the futex word and expanding shared memory size to avoid false sharing.
Rationale
- Improves throughput stability under contention by separating hot cache lines.

---

Session: 2025-10-03
Changes
- Re-implemented Mutex and Semaphore to use 64B headers in C with magic numbers, owner PID tracking, and Mutex.force_release(); updated NamedMutex/NamedSemaphore to allocate 64B and exposed metadata helpers.

---

Session: 2025-10-03 (later)
Changes
- Added pytest coverage for GuardedSharedMemory with pytest-benchmark integration validating create/attach/get_num_procs flows.

---

Session: 2025-10-03 (later)
Changes
- Packaging cleanup: removed checked-in .so, added setup.py for the C extension, GitHub Actions CI, pytest.ini bench_heavy marker, negative tests, Mutex.acquire_ns(timeout_ns, spin) plumbing, and hardened GuardedSharedMemory.__del__.

---

Session: 2025-10-03 (later)
Changes
- Replaced GuardedSharedMemory's multiprocessing.SharedMemory base with direct POSIX shm_open/mmap management while preserving PID tracking and cleanup semantics.

Session: 2025-10-04
Changes
- Hardened semaphore post/post1 to detect 32-bit counter overflow, clamp futex wake counts, and avoid large increment UB; clarified README to describe the wake hint semantics.

Session: 2025-10-04 (later)
Context
- Completed final release review after overflow fixes, covering docs, classifiers, packaging metadata, and tagging strategy.
Notes
- PyProject classifier typo removed per PEP 639; full pytest run and local build/install verified by maintainer; recommended tagging (`git tag v0.1.0 && git push origin v0.1.0`) ahead of PyPI publish.
