from ctypes import Structure, c_char, c_int, c_int64, c_uint64, c_ubyte, sizeof
from functools import cached_property

from fastipc.guarded_shared_memory import GuardedSharedMemory
from fastipc._primitives import Mutex


def align_dtype(ctype, alignment):
    class Aligned(Structure):
        _pack_ = alignment
        _fields_ = [("value", ctype)]

    return Aligned


class QueueMode:
    QUEUE = 0
    LATEST_CACHE = 1

    @classmethod
    def to_str(cls, mode: int) -> str:
        if mode == cls.QUEUE:
            return "QUEUE"
        elif mode == cls.LATEST_CACHE:
            return "LATEST_CACHE"
        else:
            return "UNKNOWN"

    @classmethod
    def from_str(cls, mode_str: str) -> int:
        if mode_str.lower() == "queue":
            return cls.QUEUE
        elif mode_str.lower() == "latest_cache":
            return cls.LATEST_CACHE
        else:
            raise ValueError(f"Unknown QueueMode string: {mode_str}")


class NamedQueueHeader(Structure):
    _pack_ = 64  # align to cache line size
    _fields_ = [
        ("magic", c_uint64),
        ("meta_size", c_uint64),
        ("num_slots", c_uint64),  # number of slots
        ("slot_size", c_uint64),  # max payload size
        (
            "head",
            c_int64,
        ),  # message id (index = id % num_slots) of the next slot to read
        (
            "tail",
            c_int64,
        ),  # message id (index = id % num_slots) of the next slot to write
        ("mode", c_uint64),  # Queue mode
        (
            "_padding",
            c_ubyte * (64 - sizeof(c_uint64) * 7),
        ),  # padding to cache line size
        ("writer_mutex", c_ubyte * 64),  # embedded mutex for writers
    ]
    magic: c_uint64
    meta_size: c_uint64
    num_slots: c_uint64
    slot_size: c_uint64
    head: c_int64
    tail: c_int64
    mode: c_uint64


class SlotHeader(Structure):
    _pack_ = 64  # align to cache line size
    _fields_ = [
        ("size", c_uint64),
        ("start_version", c_uint64),  # monotonic_ns
        ("end_version", c_uint64),  # monotonic_ns
        (
            "_padding",
            c_ubyte * (64 - sizeof(c_uint64) * 3),
        ),  # padding to cache line size
    ]


class NamedQueue:
    """
    A named, cross-process queue backed by shared memory.

    Layout:
    - NamedQueueHeader at offset 0x00
    - SlotHeaders and payloads follow
    """

    def __init__(self, name: str, *, shm: GuardedSharedMemory | None = None) -> None:
        """
        Create or attach a shared-memory region for this named queue.

        :param name: Symbolic name for the shared memory region.
        :param num_slots: Number of slots in the queue.
        :param slot_size: Size of each slot's payload.
        :param meta_size: Size of the metadata region.
        :param mode: Queue mode (either "queue" or "latest_cache").
        """
        self._name = name

        # Try to attach to existing shared memory
        tmp_shm = GuardedSharedMemory(
            f"__pyfastipc_queue_{name}", size=sizeof(NamedQueueHeader), try_create=False
        )  # attach only, will raise if not exists
        self._header = NamedQueueHeader.from_buffer(tmp_shm.buf)
        if self._header.magic != 0x50464E51:  # 'PFNQ'
            raise ValueError("Shared memory segment has invalid magic number")
        self._writer_mutex = Mutex(
            tmp_shm.buf[sizeof(NamedQueueHeader) - 64 : sizeof(NamedQueueHeader)],
            shared=True,
        )
        total_size = (
            sizeof(NamedQueueHeader)
            + num_slots * (sizeof(SlotHeader) + slot_size)
            + meta_size
        )

    @classmethod
    def create(
        cls,
        name: str,
        num_slots: int,
        slot_size: int,
        metadata: str = "",
        mode: str = "queue",
    ) -> "NamedQueue":
        """Create a new NamedQueue."""
        meta_encoded = metadata.encode("utf-8")
        meta_size = len(meta_encoded)
        total_size = (
            sizeof(NamedQueueHeader)
            + num_slots * (sizeof(SlotHeader) + slot_size)
            + meta_size
        )
        shm = GuardedSharedMemory(f"__pyfastipc_queue_{name}", size=total_size)
        if not getattr(shm, "created", False):
            raise FileExistsError(f"NamedQueue with name '{name}' already exists.")

        # Initialize header
        header = NamedQueueHeader.from_buffer(shm.buf)
        header.magic = 0x50464E51  # 'PFNQ'
        header.meta_size = meta_size
        header.num_slots = num_slots
        header.slot_size = slot_size
        header.mode = QueueMode.from_str(mode)
        header.head = 0
        header.tail = 0
        
        # Initialize writer mutex
        Mutex(shm.buf[sizeof(NamedQueueHeader) - 64 : sizeof(NamedQueueHeader)], shared=True)
        

    @cached_property
    def _total_size(self) -> int:
        """Get the total size of the shared memory region."""
        return (
            sizeof(NamedQueueHeader)
            + self._header.num_slots * (sizeof(SlotHeader) + self._header.slot_size)
            + self._header.meta_size
        )

    def get_met(self) -> str:
        pass


if __name__ == "__main__":
    nq = NamedQueue("test_queue", num_slots=1024, slot_size=4096, mode="latest_cache")
    print(f"NamedQueue '{nq._name}' initialized.")
