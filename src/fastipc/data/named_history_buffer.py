from __future__ import annotations

from time import time, monotonic_ns
from functools import cached_property
from hashlib import md5

from fastipc.guarded_shared_memory import GuardedSharedMemory
from fastipc._primitives import Mutex, FutexWord, AtomicU64
from ctypes import Structure, c_uint64, c_ubyte, sizeof


class NamedHistoryBufferHeader(Structure):
    _fields_ = [
        ("magic", c_uint64),
        ("meta_size", c_uint64),
        ("num_slots", c_uint64),  # number of slots in the circular buffer
        ("slot_size", c_uint64),  # size of each slot's payload
        ("meta_md5", c_ubyte * 16),  # metadata MD5 checksum
        (
            "_padding",
            c_ubyte * (64 - sizeof(c_uint64) * 6),
        ),  # padding to 64B cache line size
        ("reserved", c_ubyte * 192),  # reserved for future use
        # total: 256B
        ("mutex", c_ubyte * 64),  # embedded mutex for writers
        ("msg_idx", c_uint64),  # message id of the next slot to write.
        ("_padding2", c_ubyte * (64 - sizeof(c_uint64))),
        # 192B
        ("header_md5", c_ubyte * 16),  # header MD5 checksum (only first 256B)
        ("_padding3", c_ubyte * (64 - 16)),  # padding to align 64B
    ]
    magic: c_uint64
    meta_size: c_uint64
    num_slots: c_uint64
    slot_size: c_uint64
    meta_md5: bytearray
    msg_idx: c_uint64
    header_md5: bytearray

    def calc_total_size(self) -> int:
        return (
            sizeof(NamedHistoryBufferHeader)
            + self.num_slots * (sizeof(SlotHeader) + self.slot_size)
            + self.meta_size
        )

    def calc_meta_offset(self) -> int:
        return sizeof(NamedHistoryBufferHeader) + self.num_slots * (
            sizeof(SlotHeader) + self.slot_size
        )

    def calc_slot_offset(self, slot_index: int) -> int:
        return sizeof(NamedHistoryBufferHeader) + slot_index * (
            sizeof(SlotHeader) + self.slot_size
        )

    def validate_magic(self) -> None:
        if self.magic != 0x50464842:  # 'PFHB'
            raise ValueError("Invalid NamedHistoryBuffer magic number")


class SlotHeader(Structure):
    _fields_ = [
        ("size", c_uint64),  # payload size (smaller than slot_size)
        ("start_version", c_uint64),  # monotonic_ns
        ("end_version", c_uint64),  # monotonic_ns
        ("msg_idx", c_uint64),  # message id
        (
            "_padding",
            c_ubyte * (64 - sizeof(c_uint64) * 4),
        ),  # padding to cache line size
    ]
    size: c_uint64
    start_version: c_uint64
    end_version: c_uint64
    msg_idx: c_uint64


class NamedHistoryBuffer:
    """
    A named, cross-process history buffer backed by shared memory.

    Layout:
    - NamedHistoryBufferHeader at offset 0x00
    - [SlotHeaders + payloads] * num_slots
    - Metadata string at the end

    """

    def __init__(self, name: str, *, _shm: GuardedSharedMemory | None = None) -> None:
        self._name = name

        if _shm is None:
            # Attach to existing shared memory
            tmp_shm = GuardedSharedMemory(
                f"__pyfastipc_history_buffer_{name}",
                size=sizeof(NamedHistoryBufferHeader),
                attach_only=True,
                try_cleanup_on_exit=False,
            )  # attach only, will raise if not exists
            header = NamedHistoryBufferHeader.from_buffer(tmp_shm.buf)
            # Validate magic number
            if header.magic != 0x50464842:  # 'PFHB'
                raise ValueError("Shared memory segment has invalid magic number")
            total_size = header.calc_total_size()
            tmp_shm.detach()
            _shm = GuardedSharedMemory(
                f"__pyfastipc_history_buffer_{name}",
                size=total_size,
                attach_only=True,
                try_cleanup_on_exit=False,
            )
        self._attach(_shm)
        self._is_closed = False

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
        meta_md5 = md5(meta_encoded).digest()
        shm_size = (
            sizeof(NamedHistoryBufferHeader)
            + num_slots * (sizeof(SlotHeader) + slot_size)
            + len(meta_encoded)
        )
        shm = GuardedSharedMemory(
            f"__pyfastipc_history_buffer_{name}",
            size=shm_size,
            try_cleanup_on_exit=False,
        )
        shm.buf[: sizeof(NamedHistoryBufferHeader)] = b"\x00" * sizeof(
            NamedHistoryBufferHeader
        )
        header = NamedHistoryBufferHeader.from_buffer(shm.buf)
        header.magic = 0x50464842  # 'PFHB'
        header.meta_size = len(meta_encoded)
        header.num_slots = num_slots
        header.slot_size = slot_size
        header.meta_md5[:] = meta_md5

        header_bytes = bytes(shm.buf[:64])
        header_md5 = md5(header_bytes).digest()
        header.header_md5[:] = header_md5

        # Initialize mutex
        mutex = Mutex(shm.buf[64:128], shared=True)
        mutex.force_release()

        # Write meta
        meta_offset = header.calc_meta_offset()
        shm.buf[meta_offset : meta_offset + len(meta_encoded)] = meta_encoded

        # Initialize reader futex
        header.msg_idx = 0

        # Initialize SlotHeaders
        for slot_idx in range(num_slots):
            slot_offset = header.calc_slot_offset(slot_idx)
            slot_header = SlotHeader.from_buffer(shm.buf, slot_offset)
            slot_header.size = 0
            slot_header.start_version = 0
            slot_header.end_version = 1
            slot_header.msg_idx = 0

        return cls(name, _shm=shm)
    
    def validate_md5(self) -> None:
        """
        Validate the MD5 checksums of the header and metadata.

        :raises ValueError: If the MD5 checksums do not match.
        """
        # Validate header MD5
        header_bytes = bytes(self._shm.buf[:64])
        expected_header_md5 = md5(header_bytes).digest()
        actual_header_md5 = bytes(self._header.header_md5)
        print("Expected header MD5:", expected_header_md5)
        print("Actual header MD5:  ", actual_header_md5)
        if expected_header_md5 != actual_header_md5:
            raise ValueError("Header MD5 checksum does not match")

        # Validate metadata MD5
        meta_offset = self._header.calc_meta_offset()
        meta_bytes = self._shm.buf[meta_offset : meta_offset + self._header.meta_size]
        expected_meta_md5 = md5(meta_bytes).digest()
        actual_meta_md5 = bytes(self._header.meta_md5)
        if expected_meta_md5 != actual_meta_md5:
            raise ValueError("Metadata MD5 checksum does not match")

    def _attach(self, shm: GuardedSharedMemory) -> None:
        self._shm = shm
        self._header = NamedHistoryBufferHeader.from_buffer(self._shm.buf)
        if self._header.magic != 0x50464842:  # 'PFHB'
            raise ValueError("Shared memory segment has invalid magic number")
        self.validate_md5()
    
        self._writer_mutex = Mutex(
            self._shm.buf[256:320],
            shared=True,
        )
        self._msg_idx = AtomicU64(self._shm.buf[320:328])
        # Futex for notifying readers
        # Assumes little-endian architecture. (Futex on LSB)
        self._reader_futex = FutexWord(shm.buf[320:324], shared=True)

    def _get_slot_header(self, slot_index: int) -> SlotHeader:
        """
        Retrieve the SlotHeader for the specified slot index.

        :param slot_index: Index of the slot.
        :return: SlotHeader instance.
        """
        slot_offset = self._header.calc_slot_offset(slot_index)
        return SlotHeader.from_buffer(self._shm.buf, slot_offset)

    def wait_for_update(
        self, last_msg_idx: int | None = None, timeout: float | None = None
    ) -> int:
        """
        Wait for a new update in the history buffer.

        :param last_msg_idx: The last known message index. If None, uses the current msg_idx.
        :param timeout: Optional timeout in seconds.
        :return: The new message index after the update.
        """
        if last_msg_idx is None:
            last_msg_idx = self._msg_idx.load()

        timeout_per_wait_ns = int(timeout * 1e9 // 10) if timeout is not None else None
        for _ in range(
            10
        ):  # Would almost never loop, but just in case of spurious wakeups
            current_msg_idx = self._msg_idx.load()
            if current_msg_idx != last_msg_idx:
                return current_msg_idx
            self._reader_futex.wait(
                expected=last_msg_idx,
                timeout_ns=timeout_per_wait_ns,
            )
        else:
            raise TimeoutError("Timeout waiting for update in NamedHistoryBuffer")

    @cached_property
    def meta(self) -> str:
        """
        Retrieve the metadata string stored in the history buffer.

        :return: Metadata string.
        """
        meta_offset = self._header.calc_meta_offset()
        meta_bytes = self._shm.buf[meta_offset : meta_offset + self._header.meta_size]
        return meta_bytes.tobytes().decode("utf-8")

    def publish(self, data: bytes) -> None:
        """
        Publish data to the next slot in the history buffer.

        :param data: Data bytes to publish (must be <= slot_size).
        :raises ValueError: If data size exceeds slot_size.
        :raises ValueError: If the buffer is closed.
        """
        if self.closed:
            raise ValueError("Cannot publish to a closed NamedHistoryBuffer")

        if len(data) > self._header.slot_size:
            raise ValueError(
                f"Data size {len(data)} exceeds slot size {self._header.slot_size}"
            )

        with self._writer_mutex:
            msg_idx = self._msg_idx.load()
            slot_index = msg_idx % self._header.num_slots
            slot_header = self._get_slot_header(slot_index)

            # Update slot header
            slot_header.size = len(data)
            version = monotonic_ns()
            slot_header.start_version = version
            # Write data
            payload_offset = self._header.calc_slot_offset(slot_index) + sizeof(
                SlotHeader
            )
            self._shm.buf[payload_offset : payload_offset + len(data)] = data
            slot_header.end_version = version
            slot_header.msg_idx = msg_idx

            # Advance message index
            self._msg_idx.store(msg_idx + 1)
            # Notify readers (aligned 8-byte write is implicitly atomic)
            self._reader_futex.wake(-1)  # wake all waiters

    def _check_slot_index(self, msg_idx: int) -> int:
        current_msg_idx = self._msg_idx.load()
        if msg_idx >= current_msg_idx:
            raise ValueError("msg_idx is out of range (not yet published)")
        if msg_idx < max(0, current_msg_idx - self._header.num_slots):
            raise ValueError("msg_idx is out of range (already overwritten or invalid)")
        return msg_idx % self._header.num_slots

    def get_timestamp(self, msg_idx: int) -> float:
        """
        Retrieve the timestamp (monotonic_ns) of the entry at the specified message index.

        :param msg_idx: Message index to retrieve timestamp for.
        :return: Timestamp of the specified entry.
        :raises ValueError: If msg_idx is out of range.
        :raises ValueError: If msg_idx slot is already overwritten.
        :raises ValueError: If the buffer is closed.
        """
        if self.closed:
            raise ValueError("Cannot get timestamp from a closed buffer")

        if msg_idx < 0:
            raise ValueError("msg_idx must be non-negative")

        slot_index = self._check_slot_index(msg_idx)
        slot_header = self._get_slot_header(slot_index)
        if slot_header.msg_idx != msg_idx:
            raise ValueError("msg_idx is out of range (already overwritten)")
        # Map monotonic_ns to approximate POSIX timestamp
        # offset ~= time() - monotonic_ns() / 1e9
        timestamp = time() - monotonic_ns() / 1e9 + slot_header.end_version / 1e9
        return timestamp

    def read(self, msg_idx: int) -> bytearray | None:
        """
        Retrieve the entry at the specified message index from the history buffer.

        :param msg_idx: Message index to retrieve.
        :return: Data bytes for the specified entry, or None if inconsistent read.
        :raises ValueError: If msg_idx is out of range.
        :raises ValueError: If the buffer is closed.
        """
        if self.closed:
            raise ValueError("Cannot read from a closed buffer")
        slot_index = self._check_slot_index(msg_idx)
        slot_header = self._get_slot_header(slot_index)

        if slot_header.msg_idx != msg_idx:
            return None  # already overwritten
        end_version_at_start = slot_header.end_version
        payload_offset = self._header.calc_slot_offset(slot_index) + sizeof(SlotHeader)
        data = self._shm.buf[payload_offset : payload_offset + slot_header.size]

        # Need to check start_version and end_version to ensure data consistency
        ret = bytearray(data)  # make a copy of memoryview
        if end_version_at_start == slot_header.start_version:
            return ret
        else:
            return None  # inconsistent read

    def read_latest(self, max_retries: int = 16) -> bytearray:
        """
        Retrieve the latest entry from the history buffer.

        :return: Data bytes for the latest entry.
        :raises TimeoutError: If unable to read a consistent latest entry after retries.
        :raises ValueError: If no entries have been published yet.
        :raises ValueError: If the buffer is closed.
        """
        if self.closed:
            raise ValueError("Cannot read from a closed buffer")

        for _ in range(max_retries):
            latest_idx = self._msg_idx.load() - 1
            data = self.read(latest_idx)  # check and raise anything here
            if data is not None:
                return data

        raise TimeoutError(
            f"Failed to read latest entry consistently after {max_retries} retries"
        )

    def read_latest_with_timestamp(
        self, max_retries: int = 16
    ) -> tuple[bytearray, float]:
        """
        Retrieve the latest entry and its timestamp from the history buffer.

        :return: Tuple of (data bytes, timestamp).
        :raises TimeoutError: If unable to read a consistent latest entry after retries.
        :raises ValueError: If no entries have been published yet.
        """
        for _ in range(max_retries):
            latest_idx = self._msg_idx.load() - 1
            timestamp = self.get_timestamp(latest_idx)  # read timestamp first
            data = self.read(latest_idx)
            if data is not None:
                return data, timestamp
        raise TimeoutError(
            f"Failed to read latest entry consistently after {max_retries} retries"
        )

    def close(self, try_unlink: bool = False) -> None:
        """Close the shared memory segment."""
        self._shm.detach()
        if try_unlink:
            self._shm.close()
        self._is_closed = True

    @property
    def closed(self) -> bool:
        """Check if the shared memory segment is closed."""
        return self._is_closed


if __name__ == "__main__":
    meta_string = "Test History Buffer ASDADSDSDASDADS"
    hb = NamedHistoryBuffer.create(
        "test_buffer2", num_slots=8, slot_size=256, meta=meta_string
    )
    # hb = NamedHistoryBuffer("test_buffer2")
    meta_md5 = md5(meta_string.encode("utf-8")).digest()

    print("Metadata:", hb.meta)

    print("Meta hash:    ", hb._shm.buf[32:48].tobytes())
    print("Expected hash:", meta_md5)
    print("=============")

    print("Header MD5:    ", hb._shm.buf[192:208].tobytes())
    hb.close(try_unlink=True)
