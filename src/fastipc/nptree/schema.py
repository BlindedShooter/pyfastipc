from functools import lru_cache

import numpy as np
import optree


NumpyTree = optree.PyTree[np.ndarray]
LayoutDescLeaf = str  # "{dtype_str}\t{dim0,dim1,...}\t{offset}"
LayoutTree = optree.PyTree[LayoutDescLeaf]


def build_nptree_layout(tree: NumpyTree, alignment: int = 8) -> LayoutTree:
    """
    Build a layout describing the structure and dtypes of a NumpyTree.

    :param tree: The NumpyTree to generate the layout for.
    :param alignment: Byte alignment between each array (default=8).
    :return: A layout PyTree representing the structure and dtypes.
    :raises: ValueError if alignment is not a positive power of two.
    """
    if alignment <= 0 or (alignment & (alignment - 1)) != 0:
        raise ValueError("Alignment must be a positive power of two.")
    offset = 0

    def _arr_desc(arr: np.ndarray) -> str:
        nonlocal offset
        if isinstance(arr, np.ndarray):
            ret = f"{arr.dtype.str}\t{','.join(map(str, arr.shape))}\t{offset}"
            offset += arr.nbytes
            offset = (offset + alignment - 1) // alignment * alignment  # align
            return ret
        else:
            raise TypeError(f"Unsupported type in NumpyTree: {type(arr)}")

    layout = optree.tree_map(_arr_desc, tree)
    return layout


# Helper to parse a layout leaf description
@lru_cache(maxsize=1024)
def _parse_arr_desc(arr_desc: str) -> tuple[np.dtype, tuple[int, ...], int, int]:
    """Parse layout leaf: '{dtype_str}\\t{dim0,dim1,...}\\t{offset}'."""
    dtype_str, shape_str, offset_str = arr_desc.split("\t")
    dtype = np.dtype(dtype_str)
    shape = tuple(map(int, shape_str.split(","))) if shape_str else ()
    offset = int(offset_str)
    size = int(np.prod(shape) * dtype.itemsize)
    return dtype, shape, offset, size


def calc_buffer_size(layout: LayoutTree) -> int:
    """
    Calculate the total buffer size required for a NumpyTree layout.

    :param layout: The NumpyTree layout.
    :return: Total buffer size in bytes.
    """
    max_offset = 0

    def _update_max_offset(arr_layout: str) -> None:
        nonlocal max_offset
        _, _, offset, size = _parse_arr_desc(arr_layout)
        end_offset = offset + size
        if end_offset > max_offset:
            max_offset = end_offset

    optree.tree_map(_update_max_offset, layout)
    return max_offset


def validate_layout(tree: NumpyTree, layout: LayoutTree) -> None:
    """
    Validate that `tree` is consistent with `layout` (dtype, shape, structure).
    Raises on mismatch.

    :param tree: The NumpyTree to validate.
    :param layout: The NumpyTree layout.
    :raises: TypeError, ValueError on mismatch.
    :return: None.
    """
    if optree.tree_structure(tree) != optree.tree_structure(layout):
        raise ValueError("NumpyTree structure does not match layout structure.")

    def _check(arr: np.ndarray, desc: str) -> None:
        dtype, shape, _, _ = _parse_arr_desc(desc)
        if arr.dtype != dtype:
            raise TypeError(f"dtype mismatch: layout={dtype}, arr={arr.dtype}")
        if arr.shape != shape:
            raise ValueError(f"shape mismatch: layout={shape}, arr={arr.shape}")

    optree.tree_map(_check, tree, layout)


def serialize_nptree(
    nptree: NumpyTree, layout: LayoutTree, buffer: bytearray | None = None
) -> bytearray:
    """
    Serialize a NumpyTree into a backing buffer according to the provided layout.

    :param nptree: The NumpyTree to serialize.
    :param layout: The NumpyTree layout.
    :param buffer: Optional backing buffer to use. If None, a new buffer is created.
    :return: The backing buffer containing the serialized data.
    :raises: ValueError if `nptree` does not match `layout` or buffer is too small.
    """
    if optree.tree_structure(nptree) != optree.tree_structure(layout):
        raise ValueError("NumpyTree structure does not match layout structure.")

    buffer_size = calc_buffer_size(layout)
    if buffer is None:
        buffer = bytearray(buffer_size)
    elif len(buffer) < buffer_size:
        raise ValueError("Provided buffer is smaller than required size.")

    def _serialize_leaf(arr: np.ndarray, arr_layout: str) -> None:
        _, _, offset, size = _parse_arr_desc(arr_layout)
        if size == 0:
            return  # nothing to copy, avoid issues with zero-sized arrays
        if not arr.flags["C_CONTIGUOUS"]:
            arr = np.ascontiguousarray(arr)
        buffer[offset : offset + size] = memoryview(arr).cast("B")

    optree.tree_map(_serialize_leaf, nptree, layout)
    return buffer


def deserialize_nptree(
    layout: LayoutTree, buffer: bytearray | None = None
) -> NumpyTree:
    """
    Materialize a NumpyTree view from a layout and a backing buffer.

    :param layout: The NumpyTree layout.
    :param buffer: The backing buffer containing the array data.
    :return: The reconstructed NumpyTree.
    :raises: ValueError if buffer is too small.
    """
    min_buffer_size = calc_buffer_size(layout)

    if buffer is None:
        buffer = bytearray(min_buffer_size)
    elif len(buffer) < min_buffer_size:
        raise ValueError("Provided buffer is smaller than required size.")

    def _desc_to_arr(arr_layout: str) -> np.ndarray:
        dtype, shape, offset, size = _parse_arr_desc(arr_layout)
        arr_buffer = memoryview(buffer)[offset : offset + size]
        return np.frombuffer(arr_buffer, dtype=dtype).reshape(shape)

    return optree.tree_map(_desc_to_arr, layout)


if __name__ == "__main__":
    # Example usage
    tree = {
        "a": np.array([1, 2, 3], dtype=np.int32),
        "b": np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64),
        "c": np.array(np.bool_(True)),
    }
    layout = build_nptree_layout(tree)
    print("Generated Layout:", layout)
    buffer = serialize_nptree(tree, layout)
    print("Serialized Buffer Size:", len(buffer))
    parsed_tree = deserialize_nptree(layout, buffer)
    print("Parsed Tree:", parsed_tree)
