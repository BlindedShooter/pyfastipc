# Benchmark Summary

## Locks (threads)

| Impl | Uncontended ops/s | Contended ops/s | Uncontended Δ% | Contended Δ% |
|---|---|---|---|---|
| threading | 5,808,147 | 585,164 | 0.0% | 0.0% |
| fastipc | 2,942,864 | 3,097,117 | -49.3% | 429.3% |
| posix | - | - | - | - |

## Semaphores (threads)

| Impl | Solo ops/s | Light ops/s | Heavy ops/s | Solo Δ% | Light Δ% | Heavy Δ% |
|---|---|---|---|---|---|---|
| threading | 869,516 | 242,403 | 133,848 | 0.0% | 0.0% | 0.0% |
| fastipc | 1,086,256 | 1,149,029 | 848,448 | 24.9% | 374.0% | 533.9% |
| posix | 3,553,261 | 11,734 | 7,390 | 308.6% | -95.2% | -94.5% |

## Multiprocess

### Futex broadcast
| Mode | updates/s | waiter-events/s |
|---|---:|---:|
| posix_shm+spawn | 965,994 | 826,487 |
| posix_shm+spawn | 736,165 | 1,100,529 |

### Mutex (spawn)
| Impl | ops/s | p50 (us) | p90 (us) | p99 (us) | Δ% vs eventfd |
|---|---:|---:|---:|---:|---:|
| fastipc | 749,866 | - | - | - | - |
| eventfd | - | - | - | - | - |

### Semaphore (spawn)
| Impl | ops/s | p50 (us) | p90 (us) | p99 (us) | Δ% vs posix |
|---|---:|---:|---:|---:|---:|
| fastipc | - | - | - | - | - |
| posix | - | - | - | - | - |

