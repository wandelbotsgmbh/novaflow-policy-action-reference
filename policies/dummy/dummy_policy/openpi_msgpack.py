"""msgpack with numpy arrays, wire-compatible with openpi-client's ``msgpack_numpy``.

Same tags (``__ndarray__``, ``__npgeneric__``) and the same ``bytes`` keys as
``openpi_client/msgpack_numpy.py`` (github.com/Physical-Intelligence/openpi), so
a server written against openpi-client decodes what this sends and vice versa.
Arrays travel as raw buffers, not as lists of numbers.

``policies/dummy/dummy_policy/openpi_msgpack.py`` is a verbatim copy (the policy
server is its own container); ``tests/test_vendoring.py`` fails if they drift.
"""

from __future__ import annotations

from typing import Any

import msgpack
import numpy as np


def _pack_array(obj: Any) -> Any:
    if isinstance(obj, (np.ndarray, np.generic)) and obj.dtype.kind in ("V", "O", "c"):
        raise ValueError(f"Unsupported dtype: {obj.dtype}")
    if isinstance(obj, np.ndarray):
        return {
            b"__ndarray__": True,
            b"data": obj.tobytes(),
            b"dtype": obj.dtype.str,
            b"shape": obj.shape,
        }
    if isinstance(obj, np.generic):
        return {
            b"__npgeneric__": True,
            b"data": obj.item(),
            b"dtype": obj.dtype.str,
        }
    return obj


def _unpack_array(obj: dict[bytes, Any]) -> Any:
    if b"__ndarray__" in obj:
        return np.ndarray(
            buffer=obj[b"data"], dtype=np.dtype(obj[b"dtype"]), shape=obj[b"shape"]
        )
    if b"__npgeneric__" in obj:
        return np.dtype(obj[b"dtype"]).type(obj[b"data"])
    return obj


def packb(obj: Any) -> bytes:
    return msgpack.packb(obj, default=_pack_array)


def unpackb(data: bytes) -> Any:
    return msgpack.unpackb(data, object_hook=_unpack_array)
