# tests/test_nptree_schema.py

import numpy as np
import optree
import pytest

from fastipc.nptree.schema import (
    build_nptree_layout,
    calc_buffer_size,
    validate_layout,
    serialize_nptree,
    deserialize_nptree,
)


def _trees_equal(t1, t2) -> bool:
    """Compare two NumpyTree structures and values for equality."""
    if optree.tree_structure(t1) != optree.tree_structure(t2):
        return False

    def _eq(a, b):
        assert isinstance(a, np.ndarray)
        assert isinstance(b, np.ndarray)
        if a.dtype == np.bool_:
            return bool(a == b)
        if np.issubdtype(a.dtype, np.floating):
            return np.allclose(a, b, atol=0, rtol=0)
        return np.array_equal(a, b)

    leaves1, leaves2 = optree.tree_leaves(t1), optree.tree_leaves(t2)
    return all(_eq(a, b) for a, b in zip(leaves1, leaves2))


def test_roundtrip_basic():
    tree = {
        "a": np.array([1, 2, 3], dtype=np.int32),
        "b": np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64),
        "c": np.array(np.bool_(True)),
    }

    layout = build_nptree_layout(tree)
    validate_layout(tree, layout)  # should not raise

    buf = serialize_nptree(tree, layout)
    assert len(buf) == calc_buffer_size(layout)

    restored = deserialize_nptree(layout, buf)
    assert _trees_equal(tree, restored)


def test_alignment_effect():
    tree = {
        "x": np.zeros((3,), dtype=np.int32),
        "y": np.zeros((5,), dtype=np.float32),
    }

    layout_align1 = build_nptree_layout(tree, alignment=1)
    layout_align64 = build_nptree_layout(tree, alignment=64)

    size1 = calc_buffer_size(layout_align1)
    size64 = calc_buffer_size(layout_align64)

    # Larger alignment should not shrink the buffer
    assert size64 >= size1

    # For alignment=64, every array offset must be 64-byte aligned
    from fastipc.nptree.schema import _parse_arr_desc  # if it's not exported, move helper or re-parse

    offsets = []
    for desc in optree.tree_leaves(layout_align64):
        _, _, offset, _ = _parse_arr_desc(desc)
        offsets.append(offset)

    assert all(o % 64 == 0 for o in offsets)



def test_zero_dim_and_empty_arrays():
    tree = {
        "scalar": np.array(7, dtype=np.int32),          # 0-D array
        "empty": np.zeros((0,), dtype=np.float32),      # empty 1-D
        "matrix": np.zeros((2, 0), dtype=np.float64),   # 2x0 array
    }

    layout = build_nptree_layout(tree)
    buf = serialize_nptree(tree, layout)
    restored = deserialize_nptree(layout, buf)
    assert _trees_equal(tree, restored)


def test_validate_layout_structure_mismatch_raises():
    tree1 = {"a": np.array([1, 2, 3], dtype=np.int32)}
    tree2 = [np.array([1, 2, 3], dtype=np.int32)]  # different structure

    layout = build_nptree_layout(tree1)

    with pytest.raises(ValueError):
        validate_layout(tree2, layout)


def test_validate_layout_dtype_and_shape_mismatch_raises():
    tree = {"a": np.array([1, 2, 3], dtype=np.int32)}
    layout = build_nptree_layout(tree)

    # dtype mismatch
    bad_tree_dtype = {"a": np.array([1, 2, 3], dtype=np.int64)}
    with pytest.raises(TypeError):
        validate_layout(bad_tree_dtype, layout)

    # shape mismatch
    bad_tree_shape = {"a": np.array([1, 2], dtype=np.int32)}
    with pytest.raises(ValueError):
        validate_layout(bad_tree_shape, layout)


def test_serialize_reuses_provided_buffer_and_size_checks():
    tree = {
        "a": np.arange(10, dtype=np.int32),
        "b": np.arange(6, dtype=np.float64).reshape(2, 3),
    }

    layout = build_nptree_layout(tree)
    buf_size = calc_buffer_size(layout)

    # Provided buffer is reused when large enough
    buf = bytearray(buf_size)
    out = serialize_nptree(tree, layout, buffer=buf)
    assert out is buf

    # Too-small buffer should raise
    small_buf = bytearray(buf_size - 1)
    with pytest.raises(ValueError):
        serialize_nptree(tree, layout, buffer=small_buf)


def test_deserialize_allocates_when_buffer_none():
    tree = {"a": np.arange(5, dtype=np.int32)}
    layout = build_nptree_layout(tree)

    # normal roundtrip
    buf = serialize_nptree(tree, layout)
    restored = deserialize_nptree(layout, buf)
    assert _trees_equal(tree, restored)

    # If buffer=None, deserialize must allocate a new one of correct size
    restored2 = deserialize_nptree(layout, None)

    # Shape and dtype must match even though contents are uninitialized (zeroed)
    assert optree.tree_structure(restored2) == optree.tree_structure(tree)
    assert restored2["a"].dtype == tree["a"].dtype
    assert restored2["a"].shape == tree["a"].shape
