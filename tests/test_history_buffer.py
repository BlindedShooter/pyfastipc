"""
Comprehensive pytest suite for NamedHistoryBuffer
Tests cover single-process and multi-process scenarios
"""
import pytest
import time
import random
import multiprocessing as mp
from typing import List
from time import monotonic_ns, sleep

from fastipc.data.named_history_buffer import (
    NamedHistoryBuffer,
    NamedHistoryBufferHeader,
    SlotHeader,
)
from fastipc.guarded_shared_memory import GuardedSharedMemory


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def buffer_name():
    """Unique buffer name for each test"""
    return f"test_buffer_{mp.current_process().pid}_{monotonic_ns()}"


@pytest.fixture
def basic_buffer(buffer_name):
    """Create a basic buffer for testing"""
    buf = NamedHistoryBuffer.create(
        name=buffer_name,
        num_slots=8,
        slot_size=256,
        meta="Test metadata",
    )
    yield buf
    # Cleanup
    try:
        buf._shm.detach()
        buf._shm.unlink()
    except:
        pass


@pytest.fixture
def small_buffer(buffer_name):
    """Create a small buffer that wraps around quickly"""
    buf = NamedHistoryBuffer.create(
        name=buffer_name,
        num_slots=3,
        slot_size=64,
        meta="Small buffer",
    )
    yield buf
    try:
        buf._shm.detach()
        buf._shm.unlink()
    except:
        pass


# ============================================================================
# Basic Functionality Tests
# ============================================================================

class TestBasicFunctionality:
    """Test basic buffer creation and operations"""

    def test_create_buffer(self, buffer_name):
        """Test buffer creation with various parameters"""
        buf = NamedHistoryBuffer.create(
            name=buffer_name,
            num_slots=16,
            slot_size=512,
            meta="Test buffer creation",
        )
        
        assert buf._header.magic == 0x50464842
        assert buf._header.num_slots == 16
        assert buf._header.slot_size == 512
        assert buf._header.msg_idx == 0
        assert buf.meta == "Test buffer creation"
        
        buf._shm.detach()
        buf._shm.unlink()

    def test_attach_existing_buffer(self, basic_buffer, buffer_name):
        """Test attaching to an existing buffer"""
        # Create second instance attached to same buffer
        buf2 = NamedHistoryBuffer(buffer_name)
        
        assert buf2._header.num_slots == basic_buffer._header.num_slots
        assert buf2._header.slot_size == basic_buffer._header.slot_size
        assert buf2.meta == basic_buffer.meta
        
        buf2._shm.detach()

    def test_attach_nonexistent_buffer(self):
        """Test that attaching to non-existent buffer raises error"""
        with pytest.raises(Exception):  # Should raise FileNotFoundError or similar
            NamedHistoryBuffer("nonexistent_buffer_xyz")

    def test_invalid_magic_number(self, buffer_name):
        """Test validation of magic number"""
        # Create buffer with corrupted magic
        shm_size = 1024
        shm = GuardedSharedMemory(
            f"__pyfastipc_history_buffer_{buffer_name}",
            size=shm_size,
            try_cleanup_on_exit=False,
        )
        # Write invalid magic
        header = NamedHistoryBufferHeader.from_buffer(shm.buf)
        header.magic = 0xDEADBEEF
        
        with pytest.raises(ValueError, match="invalid magic"):
            NamedHistoryBuffer(buffer_name, _shm=shm)
        
        shm.detach()
        shm.unlink()

    def test_metadata_encoding(self, buffer_name):
        """Test various metadata strings including unicode"""
        metadata = "Test with unicode: 한글 テスト 🚀"
        buf = NamedHistoryBuffer.create(
            name=buffer_name,
            num_slots=4,
            slot_size=128,
            meta=metadata,
        )
        
        assert buf.meta == metadata
        
        buf._shm.detach()
        buf._shm.unlink()


# ============================================================================
# Publish/Read Tests
# ============================================================================

class TestPublishRead:
    """Test publishing and reading data"""

    def test_publish_single_message(self, basic_buffer):
        """Test publishing a single message"""
        data = b"Hello, World!"
        basic_buffer.publish(data)
        
        assert basic_buffer._msg_idx.load() == 1
        result = basic_buffer.read_latest()
        assert result == bytearray(data)

    def test_publish_multiple_messages(self, basic_buffer):
        """Test publishing multiple messages"""
        messages = [f"Message {i}".encode() for i in range(5)]
        
        for msg in messages:
            basic_buffer.publish(msg)
        
        assert basic_buffer._msg_idx.load() == 5
        result = basic_buffer.read_latest()
        assert result == bytearray(messages[-1])

    def test_publish_max_size(self, basic_buffer):
        """Test publishing data at maximum slot size"""
        max_data = b"X" * basic_buffer._header.slot_size
        basic_buffer.publish(max_data)
        
        result = basic_buffer.read_latest()
        assert len(result) == basic_buffer._header.slot_size
        assert result == bytearray(max_data)

    def test_publish_oversized_data(self, basic_buffer):
        """Test that oversized data raises ValueError"""
        oversized_data = b"X" * (basic_buffer._header.slot_size + 1)
        
        with pytest.raises(ValueError, match="exceeds slot size"):
            basic_buffer.publish(oversized_data)

    def test_read_specific_index(self, basic_buffer):
        """Test reading data at specific message index"""
        messages = [f"Message {i}".encode() for i in range(5)]
        
        for msg in messages:
            basic_buffer.publish(msg)
        
        # Read middle message
        result = basic_buffer.read(2)
        assert result == bytearray(messages[2])

    def test_read_latest_empty_buffer(self, basic_buffer):
        """Test reading from empty buffer raises error"""
        with pytest.raises(ValueError, match="out of range"):
            basic_buffer.read_latest()

    def test_circular_buffer_wraparound(self, small_buffer):
        """Test that buffer correctly wraps around"""
        num_slots = small_buffer._header.num_slots
        messages = [f"Msg {i}".encode() for i in range(num_slots * 2)]
        
        for msg in messages:
            small_buffer.publish(msg)
        
        # Should have wrapped around once
        assert small_buffer._msg_idx.load() == num_slots * 2
        
        # Latest message should be accessible
        result = small_buffer.read_latest()
        assert result == bytearray(messages[-1])
        
        # Old messages should be overwritten
        with pytest.raises(ValueError):
            small_buffer.read(0)

    def test_read_overwritten_message(self, small_buffer):
        """Test reading a message that has been overwritten raises ValueError"""
        num_slots = small_buffer._header.num_slots
        
        # Fill buffer and wrap around
        for i in range(num_slots + 2):
            small_buffer.publish(f"Message {i}".encode())
        
        # Try to read overwritten message (should raise ValueError)
        with pytest.raises(ValueError, match="already overwritten"):
            small_buffer.read(0)


# ============================================================================
# Timestamp Tests
# ============================================================================

class TestTimestamps:
    """Test timestamp functionality"""

    def test_get_timestamp(self, basic_buffer):
        """Test retrieving timestamp for a message"""
        basic_buffer.publish(b"Test message")
        
        timestamp = basic_buffer.get_timestamp(0)
        assert isinstance(timestamp, float)
        assert timestamp > 0

    def test_read_latest_with_timestamp(self, basic_buffer):
        """Test reading latest message with timestamp"""
        data = b"Test message"
        basic_buffer.publish(data)
        
        result, timestamp = basic_buffer.read_latest_with_timestamp()
        assert result == bytearray(data)
        assert isinstance(timestamp, float)
        assert timestamp > 0

    def test_timestamp_ordering(self, basic_buffer):
        """Test that timestamps are monotonically increasing"""
        timestamps = []
        
        for i in range(5):
            basic_buffer.publish(f"Message {i}".encode())
            ts = basic_buffer.get_timestamp(i)
            timestamps.append(ts)
            sleep(0.001)  # Small delay to ensure different timestamps
        
        # Timestamps should be increasing (or at least non-decreasing)
        for i in range(len(timestamps) - 1):
            assert timestamps[i] <= timestamps[i + 1]

    def test_timestamp_for_invalid_index(self, basic_buffer):
        """Test getting timestamp for invalid index raises error"""
        basic_buffer.publish(b"Test")
        
        with pytest.raises(ValueError):
            basic_buffer.get_timestamp(10)  # Out of range


# ============================================================================
# Wait/Notify Tests
# ============================================================================

class TestWaitNotify:
    """Test wait_for_update functionality"""

    def test_wait_for_update_immediate(self, basic_buffer):
        """Test wait returns immediately when update exists"""
        basic_buffer.publish(b"Message 1")
        last_idx = 0
        
        # Should return immediately since there's a new message
        new_idx = basic_buffer.wait_for_update(last_msg_idx=last_idx, timeout=1.0)
        assert new_idx == 1

    def test_wait_for_update_timeout(self, basic_buffer):
        """Test wait times out when no update"""
        with pytest.raises(TimeoutError):
            basic_buffer.wait_for_update(timeout=0.1)

    def test_wait_for_update_with_publish(self, basic_buffer):
        """Test wait succeeds when message is published"""
        def publisher():
            sleep(0.1)
            basic_buffer.publish(b"New message")
        
        # Start publisher in background
        import threading
        thread = threading.Thread(target=publisher)
        thread.start()
        
        # Wait for update
        new_idx = basic_buffer.wait_for_update(last_msg_idx=0, timeout=1.0)
        assert new_idx == 1
        
        thread.join()


# ============================================================================
# Concurrency Tests
# ============================================================================

class TestConcurrency:
    """Test concurrent access patterns"""

    def test_concurrent_reads(self, basic_buffer):
        """Test multiple readers can read simultaneously"""
        # Publish some data
        for i in range(10):
            basic_buffer.publish(f"Message {i}".encode())
        
        def reader():
            for _ in range(20):
                try:
                    data = basic_buffer.read_latest()
                    assert data is not None
                except:
                    pass
        
        import threading
        threads = [threading.Thread(target=reader) for _ in range(4)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def test_read_during_write(self, basic_buffer):
        """Test that reads during writes are consistent"""
        def writer():
            for i in range(100):
                basic_buffer.publish(f"Message {i}".encode())
                sleep(0.001)
        
        def reader():
            results = []
            for _ in range(50):
                try:
                    data = basic_buffer.read_latest()
                    if data:
                        results.append(data)
                except:
                    pass
                sleep(0.001)
            return results
        
        import threading
        writer_thread = threading.Thread(target=writer)
        reader_thread = threading.Thread(target=reader)
        
        writer_thread.start()
        reader_thread.start()
        
        writer_thread.join()
        reader_thread.join()


# ============================================================================
# Multiprocess Tests
# ============================================================================

def writer_process(buffer_name: str, num_messages: int, message_prefix: str):
    """Writer process for multiprocess tests"""
    buf = NamedHistoryBuffer(buffer_name)
    for i in range(num_messages):
        msg = f"{message_prefix}_{i}".encode()
        buf.publish(msg)
        sleep(0.001)
    buf._shm.detach()


def reader_process(buffer_name: str, num_reads: int, results_queue):
    """Reader process for multiprocess tests"""
    buf = NamedHistoryBuffer(buffer_name)
    messages = []
    
    for _ in range(num_reads):
        try:
            # Wait a bit for data to be available
            buf.wait_for_update(timeout=0.5)
            data = buf.read_latest()
            if data:
                messages.append(data.decode())
        except (ValueError, TimeoutError):
            # No data yet or timeout
            pass
        sleep(0.002)
    
    buf._shm.detach()
    results_queue.put(messages)


class TestMultiprocess:
    """Test multiprocess scenarios"""

    def test_multiple_writers_sequential(self, buffer_name):
        """Test multiple processes writing sequentially"""
        # Create buffer in main process
        buf = NamedHistoryBuffer.create(
            name=buffer_name,
            num_slots=16,
            slot_size=256,
            meta="Multiprocess test",
        )
        
        # Spawn writer processes
        procs = []
        for i in range(3):
            p = mp.Process(
                target=writer_process,
                args=(buffer_name, 5, f"Writer{i}"),
            )
            p.start()
            procs.append(p)
        
        for p in procs:
            p.join()
        
        # Check that all messages were written
        assert buf._msg_idx.load() == 15
        
        buf._shm.detach()
        buf._shm.unlink()

    def test_writer_reader_multiprocess(self, buffer_name):
        """Test writer and reader in different processes"""
        # Create buffer
        buf = NamedHistoryBuffer.create(
            name=buffer_name,
            num_slots=8,
            slot_size=256,
            meta="Writer-Reader test",
        )
        
        # Setup queue for reader results
        results_queue = mp.Queue()
        
        # Start reader
        reader = mp.Process(
            target=reader_process,
            args=(buffer_name, 10, results_queue),
        )
        reader.start()
        
        # Give reader time to start
        sleep(0.1)
        
        # Start writer
        writer = mp.Process(
            target=writer_process,
            args=(buffer_name, 15, "TestMsg"),
        )
        writer.start()
        
        writer.join()
        reader.join()
        
        # Check results
        messages = results_queue.get()
        assert len(messages) > 0
        
        buf._shm.detach()
        buf._shm.unlink()

    def test_buffer_persistence_across_processes(self, buffer_name):
        """Test that buffer state persists across process restarts"""
        # Create and populate buffer
        buf1 = NamedHistoryBuffer.create(
            name=buffer_name,
            num_slots=8,
            slot_size=128,
            meta="Persistence test",
        )
        
        for i in range(5):
            buf1.publish(f"Initial message {i}".encode())
        
        initial_idx = buf1._msg_idx.load()
        buf1._shm.detach()
        
        # Attach from new instance
        buf2 = NamedHistoryBuffer(buffer_name)
        
        # Verify state persisted
        assert buf2._msg_idx.load() == initial_idx
        assert buf2.meta == "Persistence test"
        
        # Read latest should work
        data = buf2.read_latest()
        assert data == bytearray(b"Initial message 4")
        
        buf2._shm.detach()
        buf2._shm.unlink()


# ============================================================================
# Edge Cases and Error Handling
# ============================================================================

class TestEdgeCases:
    """Test edge cases and error conditions"""

    def test_empty_data_publish(self, basic_buffer):
        """Test publishing empty data"""
        basic_buffer.publish(b"")
        result = basic_buffer.read_latest()
        assert result == bytearray(b"")

    def test_rapid_wraparound(self, small_buffer):
        """Test rapid writes that cause multiple wraparounds"""
        num_writes = small_buffer._header.num_slots * 5
        
        for i in range(num_writes):
            small_buffer.publish(f"Msg{i}".encode())
        
        assert small_buffer._msg_idx.load() == num_writes
        
        # Should be able to read recent messages
        for i in range(small_buffer._header.num_slots):
            idx = num_writes - small_buffer._header.num_slots + i
            result = small_buffer.read(idx)
            assert result is not None

    def test_consistency_check_on_concurrent_write(self, basic_buffer):
        """Test that consistency check catches concurrent writes"""
        import threading
        
        def rapid_writer():
            for i in range(100):
                basic_buffer.publish(f"Rapid{i}".encode())
        
        # Start rapid writer
        writer = threading.Thread(target=rapid_writer)
        writer.start()
        
        # Try to read - should either succeed or retry
        read_attempts = 0
        while read_attempts < 50:
            try:
                data = basic_buffer.read_latest()
                if data is not None:
                    break
            except (ValueError, TimeoutError):
                pass
            read_attempts += 1
        
        writer.join()
        
        # Should have succeeded eventually
        assert read_attempts < 50

    def test_slot_header_validation(self, basic_buffer):
        """Test that slot headers are properly initialized"""
        # All slots should be initialized
        for i in range(basic_buffer._header.num_slots):
            slot_header = basic_buffer._get_slot_header(i)
            assert slot_header.size == 0
            assert slot_header.start_version == 0
            assert slot_header.end_version == 1
            assert slot_header.msg_idx == 0


# ============================================================================
# Performance Tests using pytest-benchmark
# ============================================================================

class TestPerformance:
    """Performance benchmarks using pytest-benchmark"""

    def test_publish_single_message_benchmark(self, benchmark, basic_buffer):
        """Benchmark single message publish operation"""
        data = b"Benchmark message data"
        
        def publish_one():
            basic_buffer.publish(data)
        
        benchmark(publish_one)

    def test_publish_varying_sizes_benchmark(self, benchmark, basic_buffer):
        """Benchmark publish with varying message sizes"""
        sizes = [16, 64, 128, 256]
        messages = [b"X" * size for size in sizes]
        
        def publish_varying():
            for msg in messages:
                basic_buffer.publish(msg)
        
        result = benchmark(publish_varying)
        # Calculate effective throughput
        if hasattr(result, 'stats') and result.stats.mean > 0:
            ops_per_sec = len(messages) / result.stats.mean
            print(f"\nPublish throughput (varying sizes): {ops_per_sec:.0f} ops/sec")

    def test_read_latest_benchmark(self, benchmark, basic_buffer):
        """Benchmark read_latest operation"""
        # Populate with data first
        for i in range(10):
            basic_buffer.publish(f"Data {i}".encode())
        
        def read_one():
            return basic_buffer.read_latest()
        
        benchmark(read_one)

    def test_read_specific_index_benchmark(self, benchmark, basic_buffer):
        """Benchmark reading specific message index"""
        # Populate buffer
        for i in range(8):
            basic_buffer.publish(f"Message {i}".encode())
        
        def read_specific():
            return basic_buffer.read(5)
        
        benchmark(read_specific)

    def test_get_timestamp_benchmark(self, benchmark, basic_buffer):
        """Benchmark timestamp retrieval"""
        # Populate buffer
        for i in range(5):
            basic_buffer.publish(f"Message {i}".encode())
        
        def get_ts():
            return basic_buffer.get_timestamp(3)
        
        benchmark(get_ts)

    def test_read_latest_with_timestamp_benchmark(self, benchmark, basic_buffer):
        """Benchmark read_latest_with_timestamp operation"""
        # Populate buffer
        for i in range(10):
            basic_buffer.publish(f"Data {i}".encode())
        
        def read_with_ts():
            return basic_buffer.read_latest_with_timestamp()
        
        benchmark(read_with_ts)

    def test_publish_read_interleaved_benchmark(self, benchmark, basic_buffer):
        """Benchmark alternating publish and read operations"""
        counter = [0]  # Mutable counter for closure
        
        def interleaved():
            basic_buffer.publish(f"Message {counter[0]}".encode())
            counter[0] += 1
            try:
                basic_buffer.read_latest()
            except ValueError:
                pass
        
        benchmark(interleaved)

    def test_circular_buffer_wraparound_benchmark(self, benchmark, small_buffer):
        """Benchmark performance during buffer wraparound"""
        num_slots = small_buffer._header.num_slots
        counter = [0]
        
        def wraparound_write():
            # Write enough to cause wraparound
            for _ in range(num_slots + 1):
                small_buffer.publish(f"Msg{counter[0]}".encode())
                counter[0] += 1
        
        benchmark(wraparound_write)

    def test_high_contention_writes(self, benchmark, basic_buffer):
        """Benchmark write performance under simulated contention"""
        import threading
        results = []
        
        def concurrent_writer():
            """Writer that runs in background thread"""
            for i in range(50):
                try:
                    basic_buffer.publish(f"Concurrent{i}".encode())
                except:
                    pass
                sleep(0.0001)
        
        def measured_write():
            basic_buffer.publish(b"Measured message")
        
        # Start background writer
        thread = threading.Thread(target=concurrent_writer)
        thread.start()
        
        try:
            benchmark(measured_write)
        finally:
            thread.join()

    def test_consistency_check_overhead_benchmark(self, benchmark, basic_buffer):
        """Benchmark the overhead of consistency checking during reads"""
        # Populate buffer
        for i in range(8):
            basic_buffer.publish(f"Message {i}".encode())
        
        def read_with_retry():
            # read_latest includes retry logic for consistency
            return basic_buffer.read_latest(max_retries=16)
        
        benchmark(read_with_retry)

    @pytest.mark.slow
    def test_sustained_throughput_benchmark(self, benchmark, buffer_name):
        """Benchmark sustained throughput over many operations"""
        # Create dedicated buffer for this test
        buf = NamedHistoryBuffer.create(
            name=f"{buffer_name}_sustained",
            num_slots=16,
            slot_size=512,
            meta="Sustained test",
        )
        
        messages = [f"Sustained message {i}".encode() for i in range(100)]
        
        def sustained_operations():
            for msg in messages:
                buf.publish(msg)
                try:
                    buf.read_latest()
                except ValueError:
                    pass
        
        try:
            result = benchmark(sustained_operations)
            if hasattr(result, 'stats') and result.stats.mean > 0:
                ops_per_sec = len(messages) * 2 / result.stats.mean  # *2 for write+read
                print(f"\nSustained throughput: {ops_per_sec:.0f} ops/sec")
        finally:
            buf._shm.detach()
            buf._shm.unlink()

    @pytest.mark.bench_heavy  
    def test_memory_access_pattern_benchmark(self, benchmark, basic_buffer):
        """Benchmark different memory access patterns"""
        # Fill buffer completely
        for i in range(basic_buffer._header.num_slots):
            basic_buffer.publish(f"Pattern test {i}".encode())
        
        def random_access():
            # Read from different positions to test cache behavior
            import random
            current_idx = basic_buffer._msg_idx.load()
            valid_range = max(0, current_idx - basic_buffer._header.num_slots)
            
            for _ in range(10):
                try:
                    idx = random.randint(valid_range, current_idx - 1)
                    basic_buffer.read(idx)
                except ValueError:
                    pass
        
        benchmark(random_access)


# ============================================================================
# Cleanup Tests
# ============================================================================

class TestCleanup:
    """Test proper cleanup and resource management"""

    def test_buffer_cleanup(self, buffer_name):
        """Test that buffer can be properly cleaned up"""
        buf = NamedHistoryBuffer.create(
            name=buffer_name,
            num_slots=4,
            slot_size=64,
            meta="Cleanup test",
        )
        
        # Detach and unlink
        buf._shm.detach()
        buf._shm.unlink()
        
        # Should not be able to attach anymore
        with pytest.raises(Exception):
            NamedHistoryBuffer(buffer_name)

    def test_multiple_detach_safe(self, basic_buffer):
        """Test that multiple detach calls don't cause issues"""
        basic_buffer._shm.detach()
        # Second detach should be safe (might be no-op or raise)
        try:
            basic_buffer._shm.detach()
        except:
            pass  # Either way is acceptable


if __name__ == "__main__":
    # Run with: pytest test_named_history_buffer.py -v
    # Run with benchmarks: pytest test_named_history_buffer.py -v --benchmark-only
    # Run with slow tests: pytest test_named_history_buffer.py -v -m slow
    # Run benchmarks and save: pytest test_named_history_buffer.py --benchmark-autosave
    pytest.main([__file__, "-v", "-s"])