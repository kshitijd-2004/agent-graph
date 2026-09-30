from __future__ import annotations
import warnings
from contextlib import suppress
from html import escape
from textwrap import dedent
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Hashable,
    Iterable,
    Iterator,
    Mapping,
    TypeVar,
    Union,
    overload,
)
import numpy as np
import pandas as pd
from . import dtypes, duck_array_ops, formatting, formatting_html, ops
from .npcompat import DTypeLike, DTypeLikeSave
from .options import OPTIONS, _get_keep_attrs
from .pycompat import is_duck_dask_array
from .rolling_exp import RollingExp
from .utils import Frozen, either_dict_or_kwargs, is_scalar
try:
    import cftime
except ImportError:
    cftime = None
if TYPE_CHECKING:
    from .dataarray import DataArray
    from .dataset import Dataset
    from .indexes import Index
    from .types import ScalarOrArray, T_DataWithCoords, T_Xarray
    from .variable import Variable
    from .weighted import Weighted


def get_chunksizes(
    variables: Iterable[Variable],
) -> Mapping[Any, tuple[int, ...]]:

    chunks: dict[Any, tuple[int, ...]] = {}
    for v in variables:
        if hasattr(v.data, "chunks"):
            for dim, c in v.chunksizes.items():
                if dim in chunks and c != chunks[dim]:
                    raise ValueError(
                        f"Object has inconsistent chunks along dimension {dim}. "
                        "This can be fixed by calling unify_chunks()."
                    )
                chunks[dim] = c
    return Frozen(chunks)


def is_np_datetime_like(dtype: DTypeLike) -> bool:
    """Check if a dtype is a subclass of the numpy datetime types"""
    return np.issubdtype(dtype, np.datetime64) or np.issubdtype(dtype, np.timedelta64)


def is_np_timedelta_like(dtype: DTypeLike) -> bool:
    """Check whether dtype is of the timedelta64 dtype."""
    return np.issubdtype(dtype, np.timedelta64)


