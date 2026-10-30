from __future__ import annotations
import functools
import itertools
import math
import warnings
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Generic,
    Hashable,
    Iterator,
    Mapping,
    TypeVar,
)
import numpy as np
from . import dtypes, duck_array_ops, utils
from .arithmetic import CoarsenArithmetic
from .options import OPTIONS, _get_keep_attrs
from .pycompat import is_duck_dask_array
from .types import CoarsenBoundaryOptions, SideOptions, T_Xarray
from .utils import either_dict_or_kwargs
try:
    import bottleneck
except ImportError:
    # use numpy methods instead
    bottleneck = None
if TYPE_CHECKING:
    from .dataarray import DataArray
    from .dataset import Dataset

    RollingKey = Any
    _T = TypeVar("_T")
class DataArrayRolling(Rolling["DataArray"]):

    def __iter__(self) -> Iterator[tuple[DataArray, DataArray]]:
        if self.ndim > 1:
            raise ValueError("__iter__ is only supported for 1d-rolling")

        dim0 = self.dim[0]
        window0 = int(self.window[0])
        offset = (window0 + 1) // 2 if self.center[0] else 1
        stops = np.arange(offset, self.obj.sizes[dim0] + offset)
        starts = stops - window0
        starts[: window0 - offset] = 0

        for (label, start, stop) in zip(self.window_labels, starts, stops):
            window = self.obj.isel({dim0: slice(start, stop)})

            counts = window.count(dim=[dim0])
            window = window.where(counts >= self.min_periods)

            yield (label, window)

    def construct(
        self,
        window_dim: Hashable | Mapping[Any, Hashable] | None = None,
        stride: int | Mapping[Any, int] = 1,
        fill_value: Any = dtypes.NA,
        keep_attrs: bool | None = None,
        **window_dim_kwargs: Hashable,
    ) -> DataArray:
        """
        Convert this rolling object to xr.DataArray,
        where the window dimension is stacked as a new dimension

        Parameters
        ----------
        window_dim : Hashable or dict-like to Hashable, optional
            A mapping from dimension name to the new window dimension names.
        stride : int or mapping of int, default: 1
            Size of stride for the rolling window.
        fill_value : default: dtypes.NA
            Filling value to match the dimension size.
        keep_attrs : bool, default: None
            If True, the attributes (``attrs``) will be copied from the original
            object to the new one. If False, the new object will be returned
            without attributes. If None uses the global default.
        **window_dim_kwargs : Hashable, optional
            The keyword arguments form of ``window_dim`` {dim: new_name, ...}.

        Returns
        -------
        DataArray that is a view of the original array. The returned array is
        not writeable.

        Examples
        --------
        >>> da = xr.DataArray(np.arange(8).reshape(2, 4), dims=("a", "b"))

        >>> rolling = da.rolling(b=3)
        >>> rolling.construct("window_dim")
        <xarray.DataArray (a: 2, b: 4, window_dim: 3)>
        array([[[nan, nan,  0.],
                [nan,  0.,  1.],
                [ 0.,  1.,  2.],
                [ 1.,  2.,  3.]],
        <BLANKLINE>
               [[nan, nan,  4.],
                [nan,  4.,  5.],
                [ 4.,  5.,  6.],
                [ 5.,  6.,  7.]]])
        Dimensions without coordinates: a, b, window_dim

        >>> rolling = da.rolling(b=3, center=True)
        >>> rolling.construct("window_dim")
        <xarray.DataArray (a: 2, b: 4, window_dim: 3)>
        array([[[nan,  0.,  1.],
                [ 0.,  1.,  2.],
                [ 1.,  2.,  3.],
                [ 2.,  3., nan]],
        <BLANKLINE>
               [[nan,  4.,  5.],
                [ 4.,  5.,  6.],
                [ 5.,  6.,  7.],
                [ 6.,  7., nan]]])
        Dimensions without coordinates: a, b, window_dim

        """

        return self._construct(
            self.obj,
            window_dim=window_dim,
            stride=stride,
            fill_value=fill_value,
            keep_attrs=keep_attrs,
            **window_dim_kwargs,
        )

class DatasetRolling(Rolling["Dataset"]):

    def _numpy_or_bottleneck_reduce(
        self,
        array_agg_func,
        bottleneck_move_func,
        rolling_agg_func,
        keep_attrs,
        **kwargs,
    ):
        return self._dataset_implementation(
            functools.partial(
                DataArrayRolling._numpy_or_bottleneck_reduce,
                array_agg_func=array_agg_func,
                bottleneck_move_func=bottleneck_move_func,
                rolling_agg_func=rolling_agg_func,
            ),
            keep_attrs=keep_attrs,
            **kwargs,
        )

    def construct(
        self,
        window_dim: Hashable | Mapping[Any, Hashable] | None = None,
        stride: int | Mapping[Any, int] = 1,
        fill_value: Any = dtypes.NA,
        keep_attrs: bool | None = None,
        **window_dim_kwargs: Hashable,
    ) -> Dataset:
        """
        Convert this rolling object to xr.Dataset,
        where the window dimension is stacked as a new dimension

        Parameters
        ----------
        window_dim : str or mapping, optional
            A mapping from dimension name to the new window dimension names.
            Just a string can be used for 1d-rolling.
        stride : int, optional
            size of stride for the rolling window.
        fill_value : Any, default: dtypes.NA
            Filling value to match the dimension size.
        **window_dim_kwargs : {dim: new_name, ...}, optional
            The keyword arguments form of ``window_dim``.

        Returns
        -------
        Dataset with variables converted from rolling object.
        """

        from .dataset import Dataset

        keep_attrs = self._get_keep_attrs(keep_attrs)

        if window_dim is None:
            if len(window_dim_kwargs) == 0:
                raise ValueError(
                    "Either window_dim or window_dim_kwargs need to be specified."
                )
            window_dim = {d: window_dim_kwargs[str(d)] for d in self.dim}

        window_dims = self._mapping_to_list(
            window_dim, allow_default=False, allow_allsame=False  # type: ignore[arg-type]  # https://github.com/python/mypy/issues/12506
        )
        strides = self._mapping_to_list(stride, default=1)

        dataset = {}
        for key, da in self.obj.data_vars.items():
            # keeps rollings only for the dataset depending on self.dim
            dims = [d for d in self.dim if d in da.dims]
            if dims:
                wi = {d: window_dims[i] for i, d in enumerate(self.dim) if d in da.dims}
                st = {d: strides[i] for i, d in enumerate(self.dim) if d in da.dims}

                dataset[key] = self.rollings[key].construct(
                    window_dim=wi,
                    fill_value=fill_value,
                    stride=st,
                    keep_attrs=keep_attrs,
                )
            else:
                dataset[key] = da.copy()

            # as the DataArrays can be copied we need to delete the attrs
            if not keep_attrs:
                dataset[key].attrs = {}

        attrs = self.obj.attrs if keep_attrs else {}

        return Dataset(dataset, coords=self.obj.coords, attrs=attrs).isel(
            {d: slice(None, None, s) for d, s in zip(self.dim, strides)}
        )
class Coarsen(CoarsenArithmetic, Generic[T_Xarray]):
    """A object that implements the coarsen.

    See Also
    --------
    Dataset.coarsen
    DataArray.coarsen
    """

    def _get_keep_attrs(self, keep_attrs):
        if keep_attrs is None:
            keep_attrs = _get_keep_attrs(default=True)

        return keep_attrs

    def __repr__(self) -> str:
        """provide a nice str repr of our coarsen object"""

        attrs = [
            f"{k}->{getattr(self, k)}"
            for k in self._attributes
            if getattr(self, k, None) is not None
        ]
        return "{klass} [{attrs}]".format(
            klass=self.__class__.__name__, attrs=",".join(attrs)
        )

    def construct(
        self,
        window_dim=None,
        keep_attrs=None,
        **window_dim_kwargs,
    ) -> T_Xarray:
        """
        Convert this Coarsen object to a DataArray or Dataset,
        where the coarsening dimension is split or reshaped to two
        new dimensions.

        Parameters
        ----------
        window_dim: mapping
            A mapping from existing dimension name to new dimension names.
            The size of the second dimension will be the length of the
            coarsening window.
        keep_attrs: bool, optional
            Preserve attributes if True
        **window_dim_kwargs : {dim: new_name, ...}
            The keyword arguments form of ``window_dim``.

        Returns
        -------
        Dataset or DataArray with reshaped dimensions

        Examples
        --------
        >>> da = xr.DataArray(np.arange(24), dims="time")
        >>> da.coarsen(time=12).construct(time=("year", "month"))
        <xarray.DataArray (year: 2, month: 12)>
        array([[ 0,  1,  2,  3,  4,  5,  6,  7,  8,  9, 10, 11],
               [12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]])
        Dimensions without coordinates: year, month

        See Also
        --------
        DataArrayRolling.construct
        DatasetRolling.construct
        """

        from .dataarray import DataArray
        from .dataset import Dataset

        window_dim = either_dict_or_kwargs(
            window_dim, window_dim_kwargs, "Coarsen.construct"
        )
        if not window_dim:
            raise ValueError(
                "Either window_dim or window_dim_kwargs need to be specified."
            )

        bad_new_dims = tuple(
            win
            for win, dims in window_dim.items()
            if len(dims) != 2 or isinstance(dims, str)
        )
        if bad_new_dims:
            raise ValueError(
                f"Please provide exactly two dimension names for the following coarsening dimensions: {bad_new_dims}"
            )

        if keep_attrs is None:
            keep_attrs = _get_keep_attrs(default=True)

        missing_dims = set(window_dim) - set(self.windows)
        if missing_dims:
            raise ValueError(
                f"'window_dim' must contain entries for all dimensions to coarsen. Missing {missing_dims}"
            )
        extra_windows = set(self.windows) - set(window_dim)
        if extra_windows:
            raise ValueError(
                f"'window_dim' includes dimensions that will not be coarsened: {extra_windows}"
            )

        reshaped = Dataset()
        if isinstance(self.obj, DataArray):
            obj = self.obj._to_temp_dataset()
        else:
            obj = self.obj

        reshaped.attrs = obj.attrs if keep_attrs else {}

        for key, var in obj.variables.items():
            reshaped_dims = tuple(
                itertools.chain(*[window_dim.get(dim, [dim]) for dim in list(var.dims)])
            )
            if reshaped_dims != var.dims:
                windows = {w: self.windows[w] for w in window_dim if w in var.dims}
                reshaped_var, _ = var.coarsen_reshape(windows, self.boundary, self.side)
                attrs = var.attrs if keep_attrs else {}
                reshaped[key] = (reshaped_dims, reshaped_var, attrs)
            else:
                reshaped[key] = var

        should_be_coords = set(window_dim) & set(self.obj.coords)
        result = reshaped.set_coords(should_be_coords)
        if isinstance(self.obj, DataArray):
            return self.obj._from_temp_dataset(result)
        else:
            return result
