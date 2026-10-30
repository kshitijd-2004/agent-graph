from __future__ import annotations
import enum
import functools
import operator
from collections import Counter, defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import timedelta
from html import escape
from typing import TYPE_CHECKING, Any, Callable, Hashable, Iterable, Mapping
import numpy as np
import pandas as pd
from packaging.version import Version
from xarray.core import duck_array_ops
from xarray.core.nputils import NumpyVIndexAdapter
from xarray.core.options import OPTIONS
from xarray.core.pycompat import (
    array_type,
    integer_types,
    is_duck_dask_array,
    mod_version,
)
from xarray.core.types import T_Xarray
from xarray.core.utils import (
    NDArrayMixin,
    either_dict_or_kwargs,
    get_valid_numpy_dtype,
    to_0d_array,
)
if TYPE_CHECKING:
    from numpy.typing import DTypeLike

    from xarray.core.indexes import Index
    from xarray.core.variable import Variable
class ExplicitIndexer:
    """Base class for explicit indexer objects.

    ExplicitIndexer objects wrap a tuple of values given by their ``tuple``
    property. These tuples should always have length equal to the number of
    dimensions on the indexed array.

    Do not instantiate BaseIndexer objects directly: instead, use one of the
    sub-classes BasicIndexer, OuterIndexer or VectorizedIndexer.
    """

    __slots__ = ("_key",)

    def __init__(self, key):
        if type(self) is ExplicitIndexer:
            raise TypeError("cannot instantiate base ExplicitIndexer objects")
        self._key = tuple(key)

    @property
    def tuple(self):
        return self._key

    def __repr__(self):
        return f"{type(self).__name__}({self.tuple})"
class BasicIndexer(ExplicitIndexer):
    """Tuple for basic indexing.

    All elements should be int or slice objects. Indexing follows NumPy's
    rules for basic indexing: each axis is independently sliced and axes
    indexed with an integer are dropped from the result.
    """

    __slots__ = ()

    def __init__(self, key):
        if not isinstance(key, tuple):
            raise TypeError(f"key must be a tuple: {key!r}")

        new_key = []
        for k in key:
            if isinstance(k, integer_types):
                k = int(k)
            elif isinstance(k, slice):
                k = as_integer_slice(k)
            else:
                raise TypeError(
                    f"unexpected indexer type for {type(self).__name__}: {k!r}"
                )
            new_key.append(k)

        super().__init__(new_key)
class OuterIndexer(ExplicitIndexer):
    """Tuple for outer/orthogonal indexing.

    All elements should be int, slice or 1-dimensional np.ndarray objects with
    an integer dtype. Indexing is applied independently along each axis, and
    axes indexed with an integer are dropped from the result. This type of
    indexing works like MATLAB/Fortran.
    """

    __slots__ = ()

    def __init__(self, key):
        if not isinstance(key, tuple):
            raise TypeError(f"key must be a tuple: {key!r}")

        new_key = []
        for k in key:
            if isinstance(k, integer_types):
                k = int(k)
            elif isinstance(k, slice):
                k = as_integer_slice(k)
            elif isinstance(k, np.ndarray):
                if not np.issubdtype(k.dtype, np.integer):
                    raise TypeError(
                        f"invalid indexer array, does not have integer dtype: {k!r}"
                    )
                if k.ndim != 1:
                    raise TypeError(
                        f"invalid indexer array for {type(self).__name__}; must have "
                        f"exactly 1 dimension: {k!r}"
                    )
                k = np.asarray(k, dtype=np.int64)
            else:
                raise TypeError(
                    f"unexpected indexer type for {type(self).__name__}: {k!r}"
                )
            new_key.append(k)

        super().__init__(new_key)
class VectorizedIndexer(ExplicitIndexer):
    """Tuple for vectorized indexing.

    All elements should be slice or N-dimensional np.ndarray objects with an
    integer dtype and the same number of dimensions. Indexing follows proposed
    rules for np.ndarray.vindex, which matches NumPy's advanced indexing rules
    (including broadcasting) except sliced axes are always moved to the end:
    https://github.com/numpy/numpy/pull/6256
    """

    __slots__ = ()

    def __init__(self, key):
        if not isinstance(key, tuple):
            raise TypeError(f"key must be a tuple: {key!r}")

        new_key = []
        ndim = None
        for k in key:
            if isinstance(k, slice):
                k = as_integer_slice(k)
            elif isinstance(k, np.ndarray):
                if not np.issubdtype(k.dtype, np.integer):
                    raise TypeError(
                        f"invalid indexer array, does not have integer dtype: {k!r}"
                    )
                if ndim is None:
                    ndim = k.ndim
                elif ndim != k.ndim:
                    ndims = [k.ndim for k in key if isinstance(k, np.ndarray)]
                    raise ValueError(
                        "invalid indexer key: ndarray arguments "
                        f"have different numbers of dimensions: {ndims}"
                    )
                k = np.asarray(k, dtype=np.int64)
            else:
                raise TypeError(
                    f"unexpected indexer type for {type(self).__name__}: {k!r}"
                )
            new_key.append(k)

        super().__init__(new_key)
class ImplicitToExplicitIndexingAdapter(NDArrayMixin):
    """Wrap an array, converting tuples into the indicated explicit indexer."""

    __slots__ = ("array", "indexer_cls")

    def __init__(self, array, indexer_cls=BasicIndexer):
        self.array = as_indexable(array)
        self.indexer_cls = indexer_cls

    def __array__(self, dtype=None):
        return np.asarray(self.array, dtype=dtype)

    def __getitem__(self, key):
        key = expanded_indexer(key, self.ndim)
        result = self.array[self.indexer_cls(key)]
        if isinstance(result, ExplicitlyIndexed):
            return type(self)(result, self.indexer_cls)
        else:
            # Sometimes explicitly indexed arrays return NumPy arrays or
            # scalars.
            return result
class LazilyIndexedArray(ExplicitlyIndexedNDArrayMixin):
    """Wrap an array to make basic and outer indexing lazy."""

    __slots__ = ("array", "key")

    def __init__(self, array, key=None):
        """
        Parameters
        ----------
        array : array_like
            Array like object to index.
        key : ExplicitIndexer, optional
            Array indexer. If provided, it is assumed to already be in
            canonical expanded form.
        """
        if isinstance(array, type(self)) and key is None:
            # unwrap
            key = array.key
            array = array.array

        if key is None:
            key = BasicIndexer((slice(None),) * array.ndim)

        self.array = as_indexable(array)
        self.key = key

    def _updated_key(self, new_key):
        iter_new_key = iter(expanded_indexer(new_key.tuple, self.ndim))
        full_key = []
        for size, k in zip(self.array.shape, self.key.tuple):
            if isinstance(k, integer_types):
                full_key.append(k)
            else:
                full_key.append(_index_indexer_1d(k, next(iter_new_key), size))
        full_key = tuple(full_key)

        if all(isinstance(k, integer_types + (slice,)) for k in full_key):
            return BasicIndexer(full_key)
        return OuterIndexer(full_key)

class LazilyVectorizedIndexedArray(ExplicitlyIndexedNDArrayMixin):
    """Wrap an array to make vectorized indexing lazy."""

    __slots__ = ("array", "key")

    def __init__(self, array, key):
        """
        Parameters
        ----------
        array : array_like
            Array like object to index.
        key : VectorizedIndexer
        """
        if isinstance(key, (BasicIndexer, OuterIndexer)):
            self.key = _outer_to_vectorized_indexer(key, array.shape)
        else:
            self.key = _arrayize_vectorized_indexer(key, array.shape)
        self.array = as_indexable(array)

    @property
    def shape(self) -> tuple[int, ...]:
        return np.broadcast(*self.key.tuple).shape

    def __array__(self, dtype=None):
        return np.asarray(self.array[self.key], dtype=None)

    def _updated_key(self, new_key):
        return _combine_indexers(self.key, self.shape, new_key)

    def __getitem__(self, indexer):
        # If the indexed array becomes a scalar, return LazilyIndexedArray
        if all(isinstance(ind, integer_types) for ind in indexer.tuple):
            key = BasicIndexer(tuple(k[indexer.tuple] for k in self.key.tuple))
            return LazilyIndexedArray(self.array, key)
        return type(self)(self.array, self._updated_key(indexer))

class CopyOnWriteArray(ExplicitlyIndexedNDArrayMixin):
    __slots__ = ("array", "_copied")

    def __init__(self, array):
        self.array = as_indexable(array)
        self._copied = False

    def _ensure_copied(self):
        if not self._copied:
            self.array = as_indexable(np.array(self.array))
            self._copied = True

    def __array__(self, dtype=None):
        return np.asarray(self.array, dtype=dtype)

    def __getitem__(self, key):
        return type(self)(_wrap_numpy_scalars(self.array[key]))

    def transpose(self, order):
        return self.array.transpose(order)

    def __setitem__(self, key, value):
        self._ensure_copied()
        self.array[key] = value

class MemoryCachedArray(ExplicitlyIndexedNDArrayMixin):
    __slots__ = ("array",)

    def __init__(self, array):
        self.array = _wrap_numpy_scalars(as_indexable(array))

    def _ensure_cached(self):
        if not isinstance(self.array, NumpyIndexingAdapter):
            self.array = NumpyIndexingAdapter(np.asarray(self.array))

    def __array__(self, dtype=None):
        self._ensure_cached()
        return np.asarray(self.array, dtype=dtype)

    def __getitem__(self, key):
        return type(self)(_wrap_numpy_scalars(self.array[key]))

    def transpose(self, order):
        return self.array.transpose(order)

    def __setitem__(self, key, value):
        self.array[key] = value
class NumpyIndexingAdapter(ExplicitlyIndexedNDArrayMixin):
    """Wrap a NumPy array to use explicit indexing."""

    __slots__ = ("array",)

    def __init__(self, array):
        # In NumpyIndexingAdapter we only allow to store bare np.ndarray
        if not isinstance(array, np.ndarray):
            raise TypeError(
                "NumpyIndexingAdapter only wraps np.ndarray. "
                "Trying to wrap {}".format(type(array))
            )
        self.array = array

    def _indexing_array_and_key(self, key):
        if isinstance(key, OuterIndexer):
            array = self.array
            key = _outer_to_numpy_indexer(key, self.array.shape)
        elif isinstance(key, VectorizedIndexer):
            array = NumpyVIndexAdapter(self.array)
            key = key.tuple
        elif isinstance(key, BasicIndexer):
            array = self.array
            # We want 0d slices rather than scalars. This is achieved by
            # appending an ellipsis (see
            # https://numpy.org/doc/stable/reference/arrays.indexing.html#detailed-notes).
            key = key.tuple + (Ellipsis,)
        else:
            raise TypeError(f"unexpected key type: {type(key)}")

        return array, key

class NdArrayLikeIndexingAdapter(NumpyIndexingAdapter):
    __slots__ = ("array",)

    def __init__(self, array):
        if not hasattr(array, "__array_function__"):
            raise TypeError(
                "NdArrayLikeIndexingAdapter must wrap an object that "
                "implements the __array_function__ protocol"
            )
        self.array = array
class ArrayApiIndexingAdapter(ExplicitlyIndexedNDArrayMixin):
    """Wrap an array API array to use explicit indexing."""

    __slots__ = ("array",)

    def __init__(self, array):
        if not hasattr(array, "__array_namespace__"):
            raise TypeError(
                "ArrayApiIndexingAdapter must wrap an object that "
                "implements the __array_namespace__ protocol"
            )
        self.array = array

    def __getitem__(self, key):
        if isinstance(key, BasicIndexer):
            return self.array[key.tuple]
        elif isinstance(key, OuterIndexer):
            # manual orthogonal indexing (implemented like DaskIndexingAdapter)
            key = key.tuple
            value = self.array
            for axis, subkey in reversed(list(enumerate(key))):
                value = value[(slice(None),) * axis + (subkey, Ellipsis)]
            return value
        else:
            if isinstance(key, VectorizedIndexer):
                raise TypeError("Vectorized indexing is not supported")
            else:
                raise TypeError(f"Unrecognized indexer: {key}")

class DaskIndexingAdapter(ExplicitlyIndexedNDArrayMixin):
    """Wrap a dask array to support explicit indexing."""

    __slots__ = ("array",)

    def __init__(self, array):
        """This adapter is created in Variable.__getitem__ in
        Variable._broadcast_indexes.
        """
        self.array = array

class PandasIndexingAdapter(ExplicitlyIndexedNDArrayMixin):
    """Wrap a pandas.Index to preserve dtypes and handle explicit indexing."""

    __slots__ = ("array", "_dtype")

    def __init__(self, array: pd.Index, dtype: DTypeLike = None):
        from xarray.core.indexes import safe_cast_to_index

        self.array = safe_cast_to_index(array)

        if dtype is None:
            self._dtype = get_valid_numpy_dtype(array)
        else:
            self._dtype = np.dtype(dtype)

    @property
    def dtype(self) -> np.dtype:
        return self._dtype

    def __array__(self, dtype: DTypeLike = None) -> np.ndarray:
        if dtype is None:
            dtype = self.dtype
        array = self.array
        if isinstance(array, pd.PeriodIndex):
            with suppress(AttributeError):
                # this might not be public API
                array = array.astype("object")
        return np.asarray(array.values, dtype=dtype)

    @property
    def shape(self) -> tuple[int, ...]:
        return (len(self.array),)

class PandasMultiIndexingAdapter(PandasIndexingAdapter):
    """Handles explicit indexing for a pandas.MultiIndex.

    This allows creating one instance for each multi-index level while
    preserving indexing efficiency (memoized + might reuse another instance with
    the same multi-index).

    """

    __slots__ = ("array", "_dtype", "level", "adapter")

    def __init__(
        self,
        array: pd.MultiIndex,
        dtype: DTypeLike = None,
        level: str | None = None,
    ):
        super().__init__(array, dtype)
        self.level = level

    def __array__(self, dtype: DTypeLike = None) -> np.ndarray:
        if self.level is not None:
            return self.array.get_level_values(self.level).values
        else:
            return super().__array__(dtype)

    def _convert_scalar(self, item):
        if isinstance(item, tuple) and self.level is not None:
            idx = tuple(self.array.names).index(self.level)
            item = item[idx]
        return super()._convert_scalar(item)

    def __getitem__(self, indexer):
        result = super().__getitem__(indexer)
        if isinstance(result, type(self)):
            result.level = self.level

        return result

