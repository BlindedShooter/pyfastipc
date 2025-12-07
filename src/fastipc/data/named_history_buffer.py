from __future__ import annotations

from time import monotonic_ns

from fastipc.guarded_shared_memory import GuardedSharedMemory
from fastipc._primitives import Mutex
from ctypes import Structure, c_int64, c_uint64, c_ubyte, sizeof


class NamedHistoryBufferHeader(Structure):
    _pack_ = 64  # align to cache line size
    _fields_ = [
        ("magic", c_uint64),
        ("meta_size", c_uint64),
        ("num_slots", c_uint64),  # number of slots in the circular buffer
        ("slot_size", c_uint64),  # size of each slot's payload
        ("msg_idx", c_int64,), # message id (index = id % buffer_size) of the next slot to write
        ("_padding", c_ubyte * (64 - sizeof(c_uint64) * 5)),  # padding to cache line size
        ("mutex", c_ubyte * 64),  # embedded mutex for writers
    ]
    magic: c_uint64
    meta_size: c_uint64
    num_slots: c_uint64
    slot_size: c_uint64 
    msg_idx: c_int64
    
    def calc_total_size(self) -> int:
        return (
            sizeof(NamedHistoryBufferHeader)
            + self.num_slots * (sizeof(SlotHeader) + self.slot_size)
            + (self.meta_size // 64 + 1) * 64  # align meta to cache line size
        )
    
    def calc_meta_offset(self) -> int:
        return sizeof(NamedHistoryBufferHeader)
    
    def calc_slot_offset(self, slot_index: int) -> int:
        return sizeof(NamedHistoryBufferHeader) + slot_index * (sizeof(SlotHeader) + self.slot_size)

    def validate_magic(self) -> None:
        if self.magic != 0x50464842:  # 'PFHB'
            raise ValueError("Invalid NamedHistoryBuffer magic number")


class SlotHeader(Structure):
    _pack_ = 64  # align to cache line size
    _fields_ = [
        ("size", c_uint64),  # payload size (smaller than slot_size)
        ("start_version", c_uint64),  # monotonic_ns
        ("end_version", c_uint64),  # monotonic_ns
        ("_padding", c_ubyte * (64 - sizeof(c_uint64) * 3)),  # padding to cache line size
    ]
    size: c_uint64
    start_version: c_uint64
    end_version: c_uint64


class NamedHistoryBuffer:
    """
    A named, cross-process history buffer backed by shared memory.
    
    Layout:
    - NamedHistoryBufferHeader at offset 0x00
    - SlotHeaders at offset 0x40
    - Metadata 
    - And payloads
    
    """
    def __init__(self, name: str, *, _shm: GuardedSharedMemory | None = None) -> None:
        self._name = name
        
        if _shm is None:
            # Attach to existing shared memory
            tmp_shm = GuardedSharedMemory(
                f"__pyfastipc_history_buffer_{name}", size=sizeof(NamedHistoryBufferHeader), attach_only=True, try_cleanup_on_exit=False
            )  # attach only, will raise if not exists
            header = NamedHistoryBufferHeader.from_buffer(tmp_shm.buf)
            # Validate magic number
            if header.magic != 0x50464842:  # 'PFHB'
                raise ValueError("Shared memory segment has invalid magic number")
            total_size = header.calc_total_size()
            tmp_shm.detach()
            _shm = GuardedSharedMemory(
                f"__pyfastipc_history_buffer_{name}", size=total_size, attach_only=True, try_cleanup_on_exit=False
            )
        self._attach(_shm)
    
    @classmethod
    def create(
        cls,
        name: str,
        num_slots: int,
        slot_size: int,
        meta: str = "",
    ) -> "NamedHistoryBuffer":
        """
        Create a new NamedHistoryBuffer with the specified parameters.
        
        :param name: Symbolic name for the shared memory region.
        :param num_slots: Number of slots in the circular buffer.
        :param slot_size: Size of each slot's payload in bytes.
        :param meta: Optional metadata string to store in the buffer.
        :return: An instance of NamedHistoryBuffer.
        """
        meta_encoded = meta.encode("utf-8")
        shm_size = (
            sizeof(NamedHistoryBufferHeader)
            + num_slots * (sizeof(SlotHeader) + slot_size)
            + (len(meta_encoded) // 64 + 1) * 64  # align meta to cache line size
        )
        shm = GuardedSharedMemory(f"__pyfastipc_history_buffer_{name}", size=shm_size, try_cleanup_on_exit=False)
        header = NamedHistoryBufferHeader.from_buffer(shm.buf)
        header.magic = 0x50464842  # 'PFHB'
        header.meta_size = len(meta_encoded)
        header.num_slots = num_slots
        header.slot_size = slot_size
        header.msg_idx = 0
        
        # Initialize mutex
        shm.buf[sizeof(NamedHistoryBufferHeader) - 64 : sizeof(NamedHistoryBufferHeader)] = b"\x00" * 64
        mutex = Mutex(shm.buf[sizeof(NamedHistoryBufferHeader) - 64 : sizeof(NamedHistoryBufferHeader)], shared=True)
        mutex.force_release()
        
        # Write meta
        meta_offset = header.calc_meta_offset()
        shm.buf[meta_offset : meta_offset + len(meta_encoded)] = meta_encoded
        return cls(name, _shm=shm)

    def _attach(self, shm: GuardedSharedMemory) -> None:
        self._shm = shm
        self._header = NamedHistoryBufferHeader.from_buffer(self._shm.buf)
        if self._header.magic != 0x50464842:  # 'PFHB'
            raise ValueError("Shared memory segment has invalid magic number")
        self._writer_mutex = Mutex(
            self._shm.buf[sizeof(NamedHistoryBufferHeader) - 64 : sizeof(NamedHistoryBufferHeader)],
        )
    
    def get_meta(self) -> str:
        """
        Retrieve the metadata string stored in the history buffer.
        
        :return: Metadata string.
        """
        meta_offset = self._header.calc_meta_offset()
        meta_bytes = self._shm.buf[meta_offset : meta_offset + self._header.meta_size]
        return meta_bytes.tobytes().decode("utf-8")

    def get_slot_header(self, slot_index: int) -> SlotHeader:
        """
        Retrieve the SlotHeader for the specified slot index.
        
        :param slot_index: Index of the slot.
        :return: SlotHeader instance.
        """
        slot_offset = self._header.calc_slot_offset(slot_index)
        return SlotHeader.from_buffer(self._shm.buf, slot_offset)
    
    def publish(self, data: bytes) -> None:
        """
        Publish data to the next slot in the history buffer.
        
        :param data: Data bytes to publish (must be <= slot_size).
        :raises ValueError: If data size exceeds slot_size.
        """
        if len(data) > self._header.slot_size:
            raise ValueError(f"Data size {len(data)} exceeds slot size {self._header.slot_size}")
        
        self._writer_mutex.acquire()
        try:
            msg_idx = self._header.msg_idx
            slot_index = msg_idx % self._header.num_slots
            slot_header = self.get_slot_header(slot_index)
            
            # Update slot header
            slot_header.size = len(data)
            slot_header.start_version = monotonic_ns()
            # Write data
            payload_offset = self._header.calc_slot_offset(slot_index) + sizeof(SlotHeader)
            self._shm.buf[payload_offset : payload_offset + len(data)] = data
            slot_header.end_version = monotonic_ns()
            
            # Advance message index
            self._header.msg_idx += 1
        finally:
            self._writer_mutex.release()

    def get_latest(self, copy: bool = True) -> bytes:
        """
        Retrieve the latest entry from the history buffer.
        
        :return: Data bytes for the latest entry.
        """
        msg_idx = self._header.msg_idx
        if msg_idx == 0:
            raise ValueError("No entries in history buffer")
        slot_index = (msg_idx - 1) % self._header.num_slots
        slot_header = self.get_slot_header(slot_index)
        payload_offset = self._header.calc_slot_offset(slot_index) + sizeof(SlotHeader)
        data = self._shm.buf[payload_offset : payload_offset + slot_header.size]
        
        if copy:
            # Need to check start_version and end_version to ensure data consistency
            return data.tobytes()
        else:
            return data

if __name__ == "__main__":
    #hb = NamedHistoryBuffer.create("test_buffer", num_slots=8, slot_size=256, meta="Test History Buffer ASDADSDSDASDADS")
    hb = NamedHistoryBuffer("test_buffer")
    
    print("Metadata:", hb.get_meta())