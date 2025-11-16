// futexmod.c
#ifndef _GNU_SOURCE
#define _GNU_SOURCE 1
#endif
#include <Python.h>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <errno.h>
#include <stdint.h>
#include <time.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

#ifndef PyCFunction_CAST
#define PyCFunction_CAST(func) ((PyCFunction)(void (*)(void))(func))
#endif

#if !defined(STATIC_ASSERT)
#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
#define STATIC_ASSERT(cond, msg) _Static_assert((cond), msg)
#else
#define STATIC_ASSERT(cond, msg) typedef char static_assertion_##__LINE__[(cond) ? 1 : -1]
#endif
#endif

#if !defined(__GNUC__) && !defined(__clang__)
#error "This implementation requires GCC/Clang __atomic builtins."
#else
STATIC_ASSERT(__atomic_always_lock_free(4, 0), "32-bit atomics not lock-free on this target");
STATIC_ASSERT(__atomic_always_lock_free(8, 0), "64-bit atomics not lock-free on this target");
#endif

#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
#define FASTIPC_TLS _Thread_local
#else
#define FASTIPC_TLS __thread
#endif

#if defined(__x86_64__) || defined(__i386__)
#define CPU_RELAX() __asm__ __volatile__("pause")
#elif defined(__aarch64__)
#define CPU_RELAX() __asm__ __volatile__("yield")
#else
#define CPU_RELAX() \
    do              \
    {               \
    } while (0)
#endif

typedef struct
{
    PyObject_HEAD uint32_t *uaddr;
    int shared;      // 0: PRIVATE futex (intra-process), 1: SHARED futex (inter-process)
    PyObject *owner; // keep a reference to the buffer object
} FutexWord;

static int FutexWord_init(FutexWord *self, PyObject *args, PyObject *kw)
{
    static char *kwlist[] = {"buffer", "shared", NULL};
    PyObject *buf_obj;
    int shared = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "O|p", kwlist, &buf_obj, &shared))
        return -1;

    // buffer pin
    Py_buffer view;
    if (PyObject_GetBuffer(buf_obj, &view, PyBUF_SIMPLE) < 0)
        return -1;
    if (view.len < 4 || ((uintptr_t)view.buf % 4) != 0)
    {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "need 4-byte aligned >=4 buffer");
        return -1;
    }
    self->uaddr = (uint32_t *)view.buf;
    self->shared = shared ? 1 : 0;
    self->owner = buf_obj;
    Py_INCREF(self->owner);
    PyBuffer_Release(&view);

    return 0;
}

static void FutexWord_dealloc(FutexWord *self)
{
    Py_XDECREF(self->owner);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static long futex_wait_sys(uint32_t *uaddr, uint32_t val, const struct timespec *ts, int shared)
{
    int op = shared ? FUTEX_WAIT : FUTEX_WAIT_PRIVATE;
    return syscall(SYS_futex, uaddr, op, val, ts, NULL, 0);
}

static long futex_wake_sys(uint32_t *uaddr, int n, int shared)
{
    int op = shared ? FUTEX_WAKE : FUTEX_WAKE_PRIVATE;
    return syscall(SYS_futex, uaddr, op, n, NULL, NULL, 0);
}

static PyObject *FutexWord_wait(FutexWord *self, PyObject *args, PyObject *kw)
{
    static char *kwlist[] = {"expected", "timeout_ns", NULL};
    uint32_t expected;
    long long timeout_ns = -1;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "I|L", kwlist, &expected, &timeout_ns))
        return NULL;

    struct timespec ts, *pts = NULL;
    if (timeout_ns >= 0)
    {
        ts.tv_sec = timeout_ns / 1000000000LL;
        ts.tv_nsec = timeout_ns % 1000000000LL;
        pts = &ts;
    }

    for (;;)
    {
        int ret, err;
        Py_BEGIN_ALLOW_THREADS
            ret = (int)futex_wait_sys(self->uaddr, expected, pts, self->shared);
        err = errno;
        Py_END_ALLOW_THREADS if (ret == 0) Py_RETURN_TRUE;
        if (err == EAGAIN)
            Py_RETURN_FALSE; // value already changed
        if (err == ETIMEDOUT)
            Py_RETURN_FALSE;
        if (err == EINTR)
        {
            // If finite timeout was provided, don't loop to avoid extending total wait
            if (pts != NULL)
                Py_RETURN_FALSE;
            // Otherwise, retry on EINTR for infinite waits
            continue;
        }
        return PyErr_SetFromErrno(PyExc_OSError);
    }
}

static PyObject *FutexWord_wake(FutexWord *self, PyObject *args, PyObject *kw)
{
    static char *kwlist[] = {"n", NULL};
    int n = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "|i", kwlist, &n))
        return NULL;

    int ret, err = 0;
    Py_BEGIN_ALLOW_THREADS
        ret = (int)futex_wake_sys(self->uaddr, n, self->shared);
    err = errno;
    Py_END_ALLOW_THREADS

        if (ret >= 0) return PyLong_FromLong(ret);
    errno = err;
    return PyErr_SetFromErrno(PyExc_OSError);
}

static PyObject *FutexWord_load_acquire(FutexWord *self, PyObject *Py_UNUSED(ignored))
{
    uint32_t v = atomic_load_explicit((_Atomic uint32_t *)self->uaddr, memory_order_acquire);
    return PyLong_FromUnsignedLong(v);
}
static PyObject *FutexWord_store_release(FutexWord *self, PyObject *args)
{
    uint32_t v;
    if (!PyArg_ParseTuple(args, "I", &v))
        return NULL;
    atomic_store_explicit((_Atomic uint32_t *)self->uaddr, v, memory_order_release);
    Py_RETURN_NONE;
}

static PyMethodDef FutexWord_methods[] = {
    {"wait", PyCFunction_CAST(FutexWord_wait), METH_VARARGS | METH_KEYWORDS, "futex_wait"},
    {"wake", PyCFunction_CAST(FutexWord_wake), METH_VARARGS | METH_KEYWORDS, "futex_wake"},
    {"load_acquire", (PyCFunction)FutexWord_load_acquire, METH_NOARGS, "atomic load acquire"},
    {"store_release", (PyCFunction)FutexWord_store_release, METH_VARARGS, "atomic store release"},
    {NULL, NULL, 0, NULL}};

static PyObject *FutexWord_repr(PyObject *self)
{
    FutexWord *s = (FutexWord *)self;
    return PyUnicode_FromFormat("<fastipc.FutexWord addr=%p shared=%d>", (void *)s->uaddr, s->shared);
}

static PyTypeObject FutexWordType = {
    PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = "fastipc.FutexWord",
    .tp_basicsize = sizeof(FutexWord),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)FutexWord_init,
    .tp_dealloc = (destructor)FutexWord_dealloc,
    .tp_methods = FutexWord_methods,
    .tp_repr = (reprfunc)FutexWord_repr,
};

static int check_aligned(void *buf, Py_ssize_t len, size_t sz, size_t align)
{
    if (len < (Py_ssize_t)sz)
        return 0;
    if (((uintptr_t)buf % align) != 0)
        return 0;
    return 1;
}

// ---------- AtomicU32 ----------
typedef struct
{
    PyObject_HEAD uint32_t *uaddr;
    PyObject *owner;
} AtomicU32;

static int AtomicU32_init(AtomicU32 *self, PyObject *args, PyObject *kw)
{
    static char *kwlist[] = {"buffer", NULL};
    PyObject *buf_obj;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "O", kwlist, &buf_obj))
        return -1;

    Py_buffer view;
    if (PyObject_GetBuffer(buf_obj, &view, PyBUF_SIMPLE) < 0)
        return -1;

    int ok = check_aligned(view.buf, view.len, 4, 4);
    if (ok == 0)
    {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "need 4-byte aligned >=4 buffer");
        return -1;
    }
    else if (ok < 0)
    {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_RuntimeError, "32-bit atomics not lock-free at this address/target");
        return -1;
    }

    self->uaddr = (uint32_t *)view.buf;
    self->owner = buf_obj;
    Py_INCREF(self->owner);
    PyBuffer_Release(&view);
    return 0;
}

static void AtomicU32_dealloc(AtomicU32 *self)
{
    Py_XDECREF(self->owner);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *AtomicU32_load(AtomicU32 *self, PyObject *Py_UNUSED(ignored))
{
    uint32_t v;
    __atomic_load(self->uaddr, &v, __ATOMIC_ACQUIRE);
    return PyLong_FromUnsignedLong((unsigned long)v);
}

static PyObject *AtomicU32_store(AtomicU32 *self, PyObject *args)
{
    unsigned long v_ul;
    if (!PyArg_ParseTuple(args, "k", &v_ul))
        return NULL; // 'k' = unsigned long (fits uint32_t on LP64/LLP64 with range check below)
    uint32_t v = (uint32_t)v_ul;
    // optional range guard when unsigned long wider than 32b
    if (sizeof(unsigned long) > 4 && v_ul > 0xFFFFFFFFUL)
    {
        PyErr_SetString(PyExc_OverflowError, "value out of range for uint32");
        return NULL;
    }
    __atomic_store(self->uaddr, &v, __ATOMIC_RELEASE);
    Py_RETURN_NONE;
}

static PyObject *AtomicU32_cas(AtomicU32 *self, PyObject *args, PyObject *kw)
{
    static char *kwlist[] = {"expected", "new", NULL};
    unsigned long expected_ul, desired_ul;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "kk", kwlist, &expected_ul, &desired_ul))
        return NULL;
    if (sizeof(unsigned long) > 4)
    {
        if (expected_ul > 0xFFFFFFFFUL || desired_ul > 0xFFFFFFFFUL)
        {
            PyErr_SetString(PyExc_OverflowError, "value out of range for uint32");
            return NULL;
        }
    }
    uint32_t expected = (uint32_t)expected_ul;
    uint32_t desired = (uint32_t)desired_ul;

    // strong CAS, acquire-release on success, acquire on fail
    bool ok = __atomic_compare_exchange_n(
        self->uaddr, &expected, desired,
        /*weak=*/false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);

    if (ok)
        Py_RETURN_TRUE;
    else
        Py_RETURN_FALSE;
}

static PyMethodDef AtomicU32_methods[] = {
    {"load", (PyCFunction)AtomicU32_load, METH_NOARGS, "atomic load (acquire)"},
    {"store", (PyCFunction)AtomicU32_store, METH_VARARGS, "atomic store (release)"},
    {"cas", PyCFunction_CAST(AtomicU32_cas), METH_VARARGS | METH_KEYWORDS, "compare-and-swap"},
    {NULL, NULL, 0, NULL}};

static PyObject *AtomicU32_repr(PyObject *self)
{
    AtomicU32 *s = (AtomicU32 *)self;
    return PyUnicode_FromFormat("<fastipc.AtomicU32 addr=%p>", (void *)s->uaddr);
}

static PyTypeObject AtomicU32Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = "fastipc.AtomicU32",
    .tp_basicsize = sizeof(AtomicU32),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)AtomicU32_init,
    .tp_dealloc = (destructor)AtomicU32_dealloc,
    .tp_methods = AtomicU32_methods,
    .tp_repr = (reprfunc)AtomicU32_repr,
};

// ---------- AtomicU64 ----------
typedef struct
{
    PyObject_HEAD uint64_t *uaddr;
    PyObject *owner;
} AtomicU64;

static int AtomicU64_init(AtomicU64 *self, PyObject *args, PyObject *kw)
{
    static char *kwlist[] = {"buffer", NULL};
    PyObject *buf_obj;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "O", kwlist, &buf_obj))
        return -1;

    Py_buffer view;
    if (PyObject_GetBuffer(buf_obj, &view, PyBUF_SIMPLE) < 0)
        return -1;

    int ok = check_aligned(view.buf, view.len, 8, 8);
    if (ok == 0)
    {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "need 8-byte aligned >=8 buffer");
        return -1;
    }
    else if (ok < 0)
    {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_RuntimeError, "64-bit atomics not lock-free at this address/target");
        return -1;
    }

    self->uaddr = (uint64_t *)view.buf;
    self->owner = buf_obj;
    Py_INCREF(self->owner);
    PyBuffer_Release(&view);
    return 0;
}

static void AtomicU64_dealloc(AtomicU64 *self)
{
    Py_XDECREF(self->owner);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *AtomicU64_load(AtomicU64 *self, PyObject *Py_UNUSED(ignored))
{
    uint64_t v;
    __atomic_load(self->uaddr, &v, __ATOMIC_ACQUIRE);
    return PyLong_FromUnsignedLongLong((unsigned long long)v);
}

static PyObject *AtomicU64_store(AtomicU64 *self, PyObject *args)
{
    unsigned long long v;
    if (!PyArg_ParseTuple(args, "K", &v))
        return NULL;
    __atomic_store(self->uaddr, &v, __ATOMIC_RELEASE);
    Py_RETURN_NONE;
}

static PyObject *AtomicU64_cas(AtomicU64 *self, PyObject *args, PyObject *kw)
{
    static char *kwlist[] = {"expected", "new", NULL};
    unsigned long long expected, desired;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "KK", kwlist, &expected, &desired))
        return NULL;

    bool ok = __atomic_compare_exchange_n(
        self->uaddr, &expected, desired,
        /*weak=*/false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);

    if (ok)
        Py_RETURN_TRUE;
    else
        Py_RETURN_FALSE;
}

static PyMethodDef AtomicU64_methods[] = {
    {"load", (PyCFunction)AtomicU64_load, METH_NOARGS, "atomic load (acquire)"},
    {"store", (PyCFunction)AtomicU64_store, METH_VARARGS, "atomic store (release)"},
    {"cas", PyCFunction_CAST(AtomicU64_cas), METH_VARARGS | METH_KEYWORDS, "compare-and-swap"},
    {NULL, NULL, 0, NULL}};

static PyObject *AtomicU64_repr(PyObject *self)
{
    AtomicU64 *s = (AtomicU64 *)self;
    return PyUnicode_FromFormat("<fastipc.AtomicU64 addr=%p>", (void *)s->uaddr);
}

static PyTypeObject AtomicU64Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = "fastipc.AtomicU64",
    .tp_basicsize = sizeof(AtomicU64),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)AtomicU64_init,
    .tp_dealloc = (destructor)AtomicU64_dealloc,
    .tp_methods = AtomicU64_methods,
    .tp_repr = (reprfunc)AtomicU64_repr,
};

// 64B cacheline-aligned style headers for primitives
// Layout (Mutex):
//   0x00: u32 magic ('MUTX')
//   0x04: u32 flags (reserved)
//   0x08: u32 state (futex word; 0=unlocked,1=locked,2=contended)
//   0x0C: u32 owner_pid (PID holding lock or 0)
//   0x10: u64 last_acquired_ns (CLOCK_REALTIME in ns)
//   0x18..0x3F: reserved
#define MUTEX64_SIZE 64u
#define MUTEX_MAGIC 0x4D555458u /* 'MUTX' */
#define MUTEX_OFF_MAGIC 0u
#define MUTEX_OFF_FLAGS 4u
#define MUTEX_OFF_STATE 8u
#define MUTEX_OFF_OWNER 12u
#define MUTEX_OFF_LASTNS 16u

// Layout (Semaphore):
//   0x00: u32 magic ('SEMA')
//   0x04: u32 flags (reserved)
//   0x08: u32 count (futex word)
//   0x0C: u32 last_pid (last successful waiter pid)
//   0x10: u64 last_acquired_ns (CLOCK_REALTIME in ns)
//   0x18..0x3F: reserved
#define SEM64_SIZE 64u
#define SEM_MAGIC 0x53454D41u /* 'SEMA' */
#define SEM_OFF_MAGIC 0u
#define SEM_OFF_FLAGS 4u
#define SEM_OFF_COUNT 8u
#define SEM_OFF_LASTPID 12u
#define SEM_OFF_LASTNS 16u

static inline uint64_t now_realtime_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static inline void u32_store_rel(void *base, size_t off, uint32_t v)
{
    atomic_store_explicit((_Atomic uint32_t *)((uint8_t *)base + off), v, memory_order_release);
}
static inline uint32_t u32_load_acq(void *base, size_t off)
{
    return atomic_load_explicit((_Atomic uint32_t *)((uint8_t *)base + off), memory_order_acquire);
}
static inline uint32_t u32_xchg_rel(void *base, size_t off, uint32_t v)
{
    return atomic_exchange_explicit((_Atomic uint32_t *)((uint8_t *)base + off), v, memory_order_release);
}
static inline bool u32_cas_acqrel(void *base, size_t off, uint32_t *expected, uint32_t desired)
{
    return atomic_compare_exchange_strong_explicit((_Atomic uint32_t *)((uint8_t *)base + off), expected, desired, memory_order_acq_rel, memory_order_acquire);
}
static inline void u64_store_rel_unaligned(void *base, size_t off, uint64_t v)
{
    // Store via memcpy to be safe on unaligned addresses
    uint64_t tmp = v;
    __atomic_store_n(&tmp, tmp, __ATOMIC_RELAXED); // no-op but placates -O0
    memcpy((uint8_t *)base + off, &tmp, sizeof(tmp));
}
static inline uint64_t u64_load_acq_unaligned(void *base, size_t off)
{
    uint64_t v;
    memcpy(&v, (uint8_t *)base + off, sizeof(v));
    return v;
}

// Futex-based Mutex wrapper over 64B header
typedef struct
{
    PyObject_HEAD uint8_t *base; // points to start of 64B header
    int shared;
    PyObject *owner;
} FutexMutex;

static int FutexMutex_init(FutexMutex *self, PyObject *args, PyObject *kw)
{
    static char *kwlist[] = {"buffer", "shared", NULL};
    PyObject *buf_obj;
    int shared = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "O|p", kwlist, &buf_obj, &shared))
        return -1;
    Py_buffer view;
    if (PyObject_GetBuffer(buf_obj, &view, PyBUF_SIMPLE) < 0)
        return -1;
    if (view.len < (Py_ssize_t)MUTEX64_SIZE || ((uintptr_t)view.buf % 4) != 0)
    {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "need 4-byte aligned >=64 buffer for Mutex");
        return -1;
    }
    self->base = (uint8_t *)view.buf;
    self->shared = shared ? 1 : 0;
    self->owner = buf_obj;
    Py_INCREF(self->owner);
    // set magic and clear reserved fields (idempotent)
    u32_store_rel(self->base, MUTEX_OFF_MAGIC, MUTEX_MAGIC);
    PyBuffer_Release(&view);
    return 0;
}

static void FutexMutex_dealloc(FutexMutex *self)
{
    Py_XDECREF(self->owner);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *FutexMutex_release(FutexMutex *self, PyObject *Py_UNUSED(ignored))
{
    // check owner pid and release only if we own the lock
    if (u32_load_acq(self->base, MUTEX_OFF_OWNER) != (uint32_t)getpid())
    {
        PyErr_SetString(PyExc_RuntimeError, "cannot release a mutex not owned by this process");
        return NULL;
    }

    // Set state to 0; wake exactly one waiter only if we observed contended state (2)
    uint32_t prev = u32_xchg_rel(self->base, MUTEX_OFF_STATE, 0);
    // Clear owner
    u32_store_rel(self->base, MUTEX_OFF_OWNER, 0);

    if (prev == 2)
    {
        int op_wake = self->shared ? FUTEX_WAKE : FUTEX_WAKE_PRIVATE;
        int ret;
        Py_BEGIN_ALLOW_THREADS
            ret = (int)syscall(SYS_futex, (uint32_t *)(self->base + MUTEX_OFF_STATE), op_wake, 1, NULL, NULL, 0);
        Py_END_ALLOW_THREADS(void) ret;
    }
    // If prev was 1, we transitioned 1->0 with no waiters: nothing to wake.
    // If prev was 0, double-release: treat as no-op. (actually should not happen due to owner check above)
    Py_RETURN_NONE;
}

static PyObject *FutexMutex_force_release(FutexMutex *self, PyObject *Py_UNUSED(ignored))
{
    // Forcibly clear the lock to unlocked state; wake one waiter if needed
    uint32_t prev = u32_xchg_rel(self->base, MUTEX_OFF_STATE, 0);

    // Clear owner unconditionally
    u32_store_rel(self->base, MUTEX_OFF_OWNER, 0);
    if (prev == 2)
    {
        int op_wake = self->shared ? FUTEX_WAKE : FUTEX_WAKE_PRIVATE;
        int ret;
        Py_BEGIN_ALLOW_THREADS
            ret = (int)syscall(SYS_futex, (uint32_t *)(self->base + MUTEX_OFF_STATE), op_wake, 1, NULL, NULL, 0);
        Py_END_ALLOW_THREADS(void) ret;
    }
    Py_RETURN_NONE;
}

static PyObject *FutexMutex_owner_pid(FutexMutex *self, PyObject *Py_UNUSED(ignored))
{
    uint32_t v = u32_load_acq(self->base, MUTEX_OFF_OWNER);
    return PyLong_FromUnsignedLong(v);
}
static PyObject *FutexMutex_last_acquired_ns(FutexMutex *self, PyObject *Py_UNUSED(ignored))
{
    uint64_t v = u64_load_acq_unaligned(self->base, MUTEX_OFF_LASTNS);
    return PyLong_FromUnsignedLongLong(v);
}
static PyObject *FutexMutex_magic(FutexMutex *self, PyObject *Py_UNUSED(ignored))
{
    uint32_t v = u32_load_acq(self->base, MUTEX_OFF_MAGIC);
    return PyLong_FromUnsignedLong(v);
}

// Fast-path helpers to avoid vararg parsing overhead
static PyObject *FutexMutex_try_acquire(FutexMutex *self, PyObject *Py_UNUSED(ignored))
{
    uint32_t expected = 0;
    if (u32_cas_acqrel(self->base, MUTEX_OFF_STATE, &expected, 1))
    {
        u32_store_rel(self->base, MUTEX_OFF_OWNER, (uint32_t)getpid());
        u64_store_rel_unaligned(self->base, MUTEX_OFF_LASTNS, now_realtime_ns());
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

static PyObject *FutexMutex_acquire_fast(FutexMutex *self, PyObject *Py_UNUSED(ignored))
{
    uint32_t expected = 0;

    // Check owner pid and raise if we already own the lock
    if (u32_load_acq(self->base, MUTEX_OFF_OWNER) == (uint32_t)getpid())
    {
        PyErr_SetString(PyExc_RuntimeError, "cannot re-acquire a mutex already owned by this process");
        return NULL;
    }

    if (u32_cas_acqrel(self->base, MUTEX_OFF_STATE, &expected, 1))
    {
        u32_store_rel(self->base, MUTEX_OFF_OWNER, (uint32_t)getpid());
        u64_store_rel_unaligned(self->base, MUTEX_OFF_LASTNS, now_realtime_ns());
        Py_RETURN_TRUE;
    }

    int op_wait = self->shared ? FUTEX_WAIT : FUTEX_WAIT_PRIVATE;
    for (;;)
    {
        uint32_t c = atomic_exchange_explicit((_Atomic uint32_t *)(self->base + MUTEX_OFF_STATE), 2, memory_order_acquire);
        if (c == 0)
            break;
        int ret;
        Py_BEGIN_ALLOW_THREADS
            ret = (int)syscall(SYS_futex, (uint32_t *)(self->base + MUTEX_OFF_STATE), op_wait, 2, NULL, NULL, 0);
        Py_END_ALLOW_THREADS(void) ret;
    }
    // We now hold the lock with state=2 (contended). Keep it 2 to preserve waiter flag.
    u32_store_rel(self->base, MUTEX_OFF_OWNER, (uint32_t)getpid());
    u64_store_rel_unaligned(self->base, MUTEX_OFF_LASTNS, now_realtime_ns());
    Py_RETURN_TRUE;
}

static PyObject *FutexMutex_acquire_ns(FutexMutex *self, PyObject *args, PyObject *kw)
{
    static char *kwlist[] = {"timeout_ns", "spin", NULL};
    long long timeout_ns = -1;
    int spin = 16;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "|Li", kwlist, &timeout_ns, &spin))
        return NULL;

    uint32_t expected = 0;
    if (u32_cas_acqrel(self->base, MUTEX_OFF_STATE, &expected, 1))
    {
        u32_store_rel(self->base, MUTEX_OFF_OWNER, (uint32_t)getpid());
        u64_store_rel_unaligned(self->base, MUTEX_OFF_LASTNS, now_realtime_ns());
        Py_RETURN_TRUE;
    }
    // spin a bit
    for (int i = 0; i < spin; i++)
    {
        uint32_t v = u32_load_acq(self->base, MUTEX_OFF_STATE);
        if (v == 0)
        {
            uint32_t e2 = 0;
            if (atomic_compare_exchange_weak_explicit((_Atomic uint32_t *)(self->base + MUTEX_OFF_STATE), &e2, 1, memory_order_acquire, memory_order_relaxed))
            {
                u32_store_rel(self->base, MUTEX_OFF_OWNER, (uint32_t)getpid());
                u64_store_rel_unaligned(self->base, MUTEX_OFF_LASTNS, now_realtime_ns());
                Py_RETURN_TRUE;
            }
        }
        CPU_RELAX();
    }
    if (timeout_ns == 0)
        Py_RETURN_FALSE;

    struct timespec ts, *pts = NULL;
    if (timeout_ns > 0)
    {
        ts.tv_sec = timeout_ns / 1000000000LL;
        ts.tv_nsec = timeout_ns % 1000000000LL;
        pts = &ts;
    }
    int op_wait = self->shared ? FUTEX_WAIT : FUTEX_WAIT_PRIVATE;
    for (;;)
    {
        uint32_t c = atomic_exchange_explicit((_Atomic uint32_t *)(self->base + MUTEX_OFF_STATE), 2, memory_order_acquire);
        if (c == 0)
            break;
        int ret, err;
        Py_BEGIN_ALLOW_THREADS
            ret = (int)syscall(SYS_futex, (uint32_t *)(self->base + MUTEX_OFF_STATE), op_wait, 2, pts, NULL, 0);
        err = errno;
        Py_END_ALLOW_THREADS if (ret == -1)
        {
            if (err == ETIMEDOUT)
                Py_RETURN_FALSE;
            if (err == EINTR)
            {
                if (pts != NULL)
                    Py_RETURN_FALSE;
                continue;
            }
        }
        // otherwise, loop (spurious wake tolerated)
    }
    // Keep state=2 (contended) to ensure release will wake waiters.
    u32_store_rel(self->base, MUTEX_OFF_OWNER, (uint32_t)getpid());
    u64_store_rel_unaligned(self->base, MUTEX_OFF_LASTNS, now_realtime_ns());
    Py_RETURN_TRUE;
}

static PyObject *FutexMutex_enter(FutexMutex *self, PyObject *Py_UNUSED(ignored))
{
    // Inline blocking acquire to avoid calling a METH_VARARGS function directly
    uint32_t expected = 0;
    if (u32_cas_acqrel(self->base, MUTEX_OFF_STATE, &expected, 1))
    {
        Py_INCREF(self);
        u32_store_rel(self->base, MUTEX_OFF_OWNER, (uint32_t)getpid());
        u64_store_rel_unaligned(self->base, MUTEX_OFF_LASTNS, now_realtime_ns());
        return (PyObject *)self;
    }
    // small default spin before sleep
    for (int i = 0; i < 16; i++)
    {
        uint32_t v = u32_load_acq(self->base, MUTEX_OFF_STATE);
        if (v == 0)
        {
            uint32_t exp2 = 0;
            if (atomic_compare_exchange_weak_explicit((_Atomic uint32_t *)(self->base + MUTEX_OFF_STATE), &exp2, 1, memory_order_acquire, memory_order_relaxed))
            {
                Py_INCREF(self);
                u32_store_rel(self->base, MUTEX_OFF_OWNER, (uint32_t)getpid());
                u64_store_rel_unaligned(self->base, MUTEX_OFF_LASTNS, now_realtime_ns());
                return (PyObject *)self;
            }
        }
        CPU_RELAX();
    }
    int op_wait = self->shared ? FUTEX_WAIT : FUTEX_WAIT_PRIVATE;
    int c;
    for (;;)
    {
        c = atomic_exchange_explicit((_Atomic uint32_t *)(self->base + MUTEX_OFF_STATE), 2, memory_order_acquire);
        if (c == 0)
            break;
        int ret, err;
        Py_BEGIN_ALLOW_THREADS
            ret = (int)syscall(SYS_futex, (uint32_t *)(self->base + MUTEX_OFF_STATE), op_wait, 2, NULL, NULL, 0);
        err = errno;
        Py_END_ALLOW_THREADS(void) ret;
        (void)err;
    }
    // Keep state=2 (contended) to ensure release will wake waiters.
    u32_store_rel(self->base, MUTEX_OFF_OWNER, (uint32_t)getpid());
    u64_store_rel_unaligned(self->base, MUTEX_OFF_LASTNS, now_realtime_ns());
    Py_INCREF(self);
    return (PyObject *)self;
}
static PyObject *FutexMutex_exit(FutexMutex *self, PyObject *Py_UNUSED(args))
{
    return FutexMutex_release(self, NULL);
}

static PyMethodDef FutexMutex_methods[] = {
    {"acquire", (PyCFunction)FutexMutex_acquire_fast, METH_NOARGS, "acquire the mutex (blocking)"},
    {"acquire_ns", PyCFunction_CAST(FutexMutex_acquire_ns), METH_VARARGS | METH_KEYWORDS, "acquire with timeout_ns and spin; returns bool"},
    {"release", (PyCFunction)FutexMutex_release, METH_NOARGS, "release the mutex"},
    {"force_release", (PyCFunction)FutexMutex_force_release, METH_NOARGS, "forcibly unlock the mutex"},
    {"try_acquire", (PyCFunction)FutexMutex_try_acquire, METH_NOARGS, "nonblocking acquire"},
    {"owner_pid", (PyCFunction)FutexMutex_owner_pid, METH_NOARGS, "current owner PID or 0"},
    {"last_acquired_ns", (PyCFunction)FutexMutex_last_acquired_ns, METH_NOARGS, "last successful acquisition time (ns)"},
    {"magic", (PyCFunction)FutexMutex_magic, METH_NOARGS, "Get magic constant"},
    {"__enter__", (PyCFunction)FutexMutex_enter, METH_NOARGS, "ctx enter"},
    {"__exit__", (PyCFunction)FutexMutex_exit, METH_VARARGS, "ctx exit"},
    {NULL, NULL, 0, NULL}};

static PyObject *FutexMutex_repr(PyObject *self)
{
    FutexMutex *s = (FutexMutex *)self;
    uint32_t owner = u32_load_acq(s->base, MUTEX_OFF_OWNER);
    uint64_t ts = u64_load_acq_unaligned(s->base, MUTEX_OFF_LASTNS);
    uint32_t st = u32_load_acq(s->base, MUTEX_OFF_STATE);
    return PyUnicode_FromFormat("<fastipc.Mutex buf=%p shared=%d state=%u owner=%u last_ns=%llu>", (void *)s->base, s->shared, (unsigned)st, (unsigned)owner, (unsigned long long)ts);
}

static PyTypeObject FutexMutexType = {
    PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = "fastipc.Mutex",
    .tp_basicsize = sizeof(FutexMutex),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)FutexMutex_init,
    .tp_dealloc = (destructor)FutexMutex_dealloc,
    .tp_methods = FutexMutex_methods,
    .tp_repr = (reprfunc)FutexMutex_repr,
};

// Futex-based counting semaphore with 64B header
typedef struct
{
    PyObject_HEAD uint8_t *base; // start of 64B header
    int shared;
    PyObject *owner;
} FutexSemaphore;

static int FutexSemaphore_init(FutexSemaphore *self, PyObject *args, PyObject *kw)
{
    static char *kwlist[] = {"buffer", "initial", "shared", NULL};
    PyObject *buf_obj;
    PyObject *init_obj = NULL;
    int shared = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "O|Op", kwlist, &buf_obj, &init_obj, &shared))
        return -1;
    Py_buffer view;
    if (PyObject_GetBuffer(buf_obj, &view, PyBUF_SIMPLE) < 0)
        return -1;
    if (view.len < (Py_ssize_t)SEM64_SIZE || ((uintptr_t)view.buf % 4) != 0)
    {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "need 4-byte aligned >=64 buffer for Semaphore");
        return -1;
    }
    self->base = (uint8_t *)view.buf;
    self->shared = shared ? 1 : 0;
    self->owner = buf_obj;
    Py_INCREF(self->owner);
    // Initialize magic and, if requested, initial count
    u32_store_rel(self->base, SEM_OFF_MAGIC, SEM_MAGIC);
    // Initialize only if an explicit initial value is provided (not None)
    if (init_obj && init_obj != Py_None)
    {
        unsigned long init_ul = PyLong_AsUnsignedLong(init_obj);
        if (PyErr_Occurred())
        {
            PyBuffer_Release(&view);
            return -1;
        }
        if (sizeof(unsigned long) > 4 && init_ul > 0xFFFFFFFFUL)
        {
            PyBuffer_Release(&view);
            PyErr_SetString(PyExc_OverflowError, "initial out of range for uint32");
            return -1;
        }
        uint32_t init = (uint32_t)init_ul;
        atomic_store_explicit((_Atomic uint32_t *)(self->base + SEM_OFF_COUNT), init, memory_order_release);
    }
    PyBuffer_Release(&view);
    return 0;
}

static void FutexSemaphore_dealloc(FutexSemaphore *self)
{
    Py_XDECREF(self->owner);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static int semaphore_add_tokens(FutexSemaphore *self, uint32_t add, uint32_t *prev_out)
{
    _Atomic uint32_t *c = (_Atomic uint32_t *)(self->base + SEM_OFF_COUNT);
    if (add == 0)
    {
        if (prev_out)
            *prev_out = atomic_load_explicit(c, memory_order_acquire);
        return 0;
    }

    for (;;)
    {
        uint32_t cur = atomic_load_explicit(c, memory_order_acquire);
        if (cur > UINT32_MAX - add)
        {
            PyErr_SetString(PyExc_OverflowError, "semaphore count overflow");
            return -1;
        }
        if (atomic_compare_exchange_weak_explicit(c, &cur, cur + add, memory_order_release, memory_order_acquire))
        {
            if (prev_out)
                *prev_out = cur;
            return 0;
        }
    }
}

static void semaphore_wake_waiters(FutexSemaphore *self, uint32_t prev, uint32_t add)
{
    if (add == 0 || prev != 0)
        return;

    int wake_n = add > (uint32_t)INT_MAX ? INT_MAX : (int)add;
    if (wake_n <= 0)
        wake_n = INT_MAX;

    int op_wake = self->shared ? FUTEX_WAKE : FUTEX_WAKE_PRIVATE;
    int ret;
    Py_BEGIN_ALLOW_THREADS
        ret = (int)syscall(SYS_futex, (uint32_t *)(self->base + SEM_OFF_COUNT), op_wake, wake_n, NULL, NULL, 0);
    Py_END_ALLOW_THREADS(void) ret;
}

static PyObject *FutexSemaphore_post(FutexSemaphore *self, PyObject *args, PyObject *kw)
{
    static char *kwlist[] = {"n", NULL};
    unsigned long long n_ull = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "|K", kwlist, &n_ull))
        return NULL;
    if (n_ull > UINT32_MAX)
    {
        PyErr_SetString(PyExc_OverflowError, "semaphore increment out of range");
        return NULL;
    }
    uint32_t add = (uint32_t)n_ull;
    uint32_t prev = 0;
    if (semaphore_add_tokens(self, add, &prev) < 0)
        return NULL;
    semaphore_wake_waiters(self, prev, add);
    Py_RETURN_NONE;
}

static PyObject *FutexSemaphore_post1(FutexSemaphore *self, PyObject *Py_UNUSED(ignored))
{
    uint32_t prev = 0;
    if (semaphore_add_tokens(self, 1u, &prev) < 0)
        return NULL;
    semaphore_wake_waiters(self, prev, 1u);
    Py_RETURN_NONE;
}

static PyObject *FutexSemaphore_wait(FutexSemaphore *self, PyObject *args, PyObject *kw)
{
    static char *kwlist[] = {"blocking", "timeout_ns", "spin", NULL};
    int blocking = 1;
    long long timeout_ns = -1;
    int spin = 16;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "|pLi", kwlist, &blocking, &timeout_ns, &spin))
        return NULL;
    _Atomic uint32_t *c = (_Atomic uint32_t *)(self->base + SEM_OFF_COUNT);
    struct timespec ts, *pts = NULL;
    if (timeout_ns >= 0)
    {
        ts.tv_sec = timeout_ns / 1000000000LL;
        ts.tv_nsec = timeout_ns % 1000000000LL;
        pts = &ts;
    }
    for (;;)
    {
        // spin attempts to acquire a token under light contention
        for (int i = 0; i < spin; i++)
        {
            uint32_t v = atomic_load_explicit(c, memory_order_acquire);
            if (v > 0)
            {
                uint32_t vv = v;
                if (atomic_compare_exchange_weak_explicit(c, &vv, vv - 1, memory_order_acq_rel, memory_order_acquire))
                {
                    u32_store_rel(self->base, SEM_OFF_LASTPID, (uint32_t)getpid());
                    u64_store_rel_unaligned(self->base, SEM_OFF_LASTNS, now_realtime_ns());
                    Py_RETURN_TRUE;
                }
            }
            CPU_RELAX();
        }
        if (!blocking)
            Py_RETURN_FALSE;
        int op_wait = self->shared ? FUTEX_WAIT : FUTEX_WAIT_PRIVATE;
        int ret, err;
        Py_BEGIN_ALLOW_THREADS
            ret = (int)syscall(SYS_futex, (uint32_t *)(self->base + SEM_OFF_COUNT), op_wait, 0, pts, NULL, 0);
        err = errno;
        Py_END_ALLOW_THREADS if (ret == -1 && err == ETIMEDOUT) Py_RETURN_FALSE;
        // else loop (spurious wake-ups tolerated)
    }
}

static PyObject *FutexSemaphore_value(FutexSemaphore *self, PyObject *Py_UNUSED(ignored))
{
    uint32_t v = atomic_load_explicit((_Atomic uint32_t *)(self->base + SEM_OFF_COUNT), memory_order_acquire);
    return PyLong_FromUnsignedLong(v);
}

static PyObject *FutexSemaphore_last_acquired_ns(FutexSemaphore *self, PyObject *Py_UNUSED(ignored))
{
    uint64_t v = u64_load_acq_unaligned(self->base, SEM_OFF_LASTNS);
    return PyLong_FromUnsignedLongLong(v);
}
static PyObject *FutexSemaphore_last_pid(FutexSemaphore *self, PyObject *Py_UNUSED(ignored))
{
    uint32_t v = u32_load_acq(self->base, SEM_OFF_LASTPID);
    return PyLong_FromUnsignedLong(v);
}
static PyObject *FutexSemaphore_magic(FutexSemaphore *self, PyObject *Py_UNUSED(ignored))
{
    uint32_t v = u32_load_acq(self->base, SEM_OFF_MAGIC);
    return PyLong_FromUnsignedLong(v);
}

static PyMethodDef FutexSemaphore_methods[] = {
    {"post", PyCFunction_CAST(FutexSemaphore_post), METH_VARARGS | METH_KEYWORDS, "Increment and possibly wake waiters"},
    {"post1", PyCFunction_CAST(FutexSemaphore_post1), METH_VARARGS | METH_KEYWORDS, "Increment one and possibly wake waiters"},
    {"wait", PyCFunction_CAST(FutexSemaphore_wait), METH_VARARGS | METH_KEYWORDS, "Decrement or block until available"},
    {"value", (PyCFunction)FutexSemaphore_value, METH_NOARGS, "Get current value"},
    {"last_acquired_ns", (PyCFunction)FutexSemaphore_last_acquired_ns, METH_NOARGS, "Get last successful wait time (ns)"},
    {"last_pid", (PyCFunction)FutexSemaphore_last_pid, METH_NOARGS, "Get last successful waiter PID"},
    {"magic", (PyCFunction)FutexSemaphore_magic, METH_NOARGS, "Get magic constant"},
    {NULL, NULL, 0, NULL}};

static PyObject *FutexSemaphore_repr(PyObject *self)
{
    FutexSemaphore *s = (FutexSemaphore *)self;
    uint32_t v = atomic_load_explicit((_Atomic uint32_t *)(s->base + SEM_OFF_COUNT), memory_order_acquire);
    uint32_t pid = u32_load_acq(s->base, SEM_OFF_LASTPID);
    uint64_t ts = u64_load_acq_unaligned(s->base, SEM_OFF_LASTNS);
    return PyUnicode_FromFormat("<fastipc.Semaphore buf=%p shared=%d value=%u last_pid=%u last_ns=%llu>", (void *)s->base, s->shared, (unsigned)v, (unsigned)pid, (unsigned long long)ts);
}

static PyTypeObject FutexSemaphoreType = {
    PyVarObject_HEAD_INIT(NULL, 0)
        .tp_name = "fastipc.Semaphore",
    .tp_basicsize = sizeof(FutexSemaphore),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)FutexSemaphore_init,
    .tp_dealloc = (destructor)FutexSemaphore_dealloc,
    .tp_methods = FutexSemaphore_methods,
    .tp_repr = (reprfunc)FutexSemaphore_repr,
};

static PyModuleDef futexmod = {
    PyModuleDef_HEAD_INIT,
    .m_name = "fastipc._primitives",
    .m_doc = NULL,
    .m_size = -1,
    .m_methods = NULL,
    .m_slots = NULL,
    .m_traverse = NULL,
    .m_clear = NULL,
    .m_free = NULL,
};

PyMODINIT_FUNC PyInit__primitives(void)
{
    PyObject *m = PyModule_Create(&futexmod);
    if (!m)
        return NULL;
    // Initialize adaptive flag from environment
    if (PyType_Ready(&FutexWordType) < 0)
        return NULL;
    Py_INCREF(&FutexWordType);
    PyModule_AddObject(m, "FutexWord", (PyObject *)&FutexWordType);
    if (PyType_Ready(&AtomicU32Type) < 0)
        return NULL;
    Py_INCREF(&AtomicU32Type);
    PyModule_AddObject(m, "AtomicU32", (PyObject *)&AtomicU32Type);
    if (PyType_Ready(&AtomicU64Type) < 0)
        return NULL;
    Py_INCREF(&AtomicU64Type);
    PyModule_AddObject(m, "AtomicU64", (PyObject *)&AtomicU64Type);
    if (PyType_Ready(&FutexMutexType) < 0)
        return NULL;
    Py_INCREF(&FutexMutexType);
    PyModule_AddObject(m, "Mutex", (PyObject *)&FutexMutexType);
    if (PyType_Ready(&FutexSemaphoreType) < 0)
        return NULL;
    Py_INCREF(&FutexSemaphoreType);
    PyModule_AddObject(m, "Semaphore", (PyObject *)&FutexSemaphoreType);
    return m;
}
