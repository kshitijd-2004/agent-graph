import numpy as np
from . import dtypes, nputils, utils
from .duck_array_ops import _dask_or_eager_func, count, fillna, isnull, where_method
from .pycompat import dask_array_type

try:
    import dask.array as dask_array

    from . import dask_array_compat
except ImportError:
    dask_array = None
    dask_array_compat = None  # type: ignore


def _replace_nan(a, val):
    """
    replace nan in a by val, and returns the replaced array and the nan
    position
    """
    mask = isnull(a)
    return where_method(val, mask, a), mask


def _maybe_null_out(result, axis, mask, min_count=1):
    """
    xarray version of pandas.core.nanops._maybe_null_out
    """
    if hasattr(axis, "__len__"):  # if tuple or list
        raise ValueError(
            "min_count is not available for reduction with more than one dimensions."
        )

    if axis is not None and getattr(result, "ndim", False):
        null_mask = (mask.shape[axis] - mask.sum(axis) - min_count) < 0
        if null_mask.any():
            dtype, fill_value = dtypes.maybe_promote(result.dtype)
            result = result.astype(dtype)
            result[null_mask] = fill_value

    elif getattr(result, "dtype", None) not in dtypes.NAT_TYPES:
        null_mask = mask.size - mask.sum()
        if null_mask < min_count:
            result = np.nan

    return result


def _nan_argminmax_object(func, fill_value, value, axis=None, **kwargs):
    """ In house nanargmin, nanargmax for object arrays. Always return integer
    type
    """
    valid_count = count(value, axis=axis)
    value = fillna(value, fill_value)
    data = _dask_or_eager_func(func)(value, axis=axis, **kwargs)

    # TODO This will evaluate dask arrays and might be costly.
    if (valid_count == 0).any():
        raise ValueError("All-NaN slice encountered")

    return data


