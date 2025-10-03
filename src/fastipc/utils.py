import os


def align_to_cacheline_size(size: int) -> int:
    """Align a size in bytes to the next cache line boundary."""
    cache_line_size = int(os.environ.get("CACHE_LINE_SIZE", 64))
    return (size + cache_line_size - 1) // cache_line_size * cache_line_size
