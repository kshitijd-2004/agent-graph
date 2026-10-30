"""
The :mod:`sklearn.compose._column_transformer` module implements utilities
to work with heterogeneous data and to apply different transformers to
different columns.
"""
from numbers import Integral, Real
from itertools import chain
from collections import Counter
import numpy as np
from scipy import sparse
from ..base import clone, TransformerMixin
from ..utils._estimator_html_repr import _VisualBlock
from ..pipeline import _fit_transform_one, _transform_one, _name_estimators
from ..preprocessing import FunctionTransformer
from ..utils import Bunch
from ..utils import _safe_indexing
from ..utils import _get_column_indices
from ..utils._param_validation import HasMethods, Interval, StrOptions, Hidden
from ..utils._set_output import _get_output_config, _safe_set_output
from ..utils import check_pandas_support
from ..utils.metaestimators import _BaseComposition
from ..utils.validation import check_array, check_is_fitted, _check_feature_names_in
from ..utils.validation import _num_samples
from ..utils.parallel import delayed, Parallel
class ColumnTransformer(TransformerMixin, _BaseComposition):

    @_transformers.setter
    def _transformers(self, value):
        try:
            self.transformers = [
                (name, trans, col)
                for ((name, trans), (_, _, col)) in zip(value, self.transformers)
            ]
        except (TypeError, ValueError):
            self.transformers = value

    def set_output(self, *, transform=None):
        """Set the output container when `"transform"` and `"fit_transform"` are called.

        Calling `set_output` will set the output of all estimators in `transformers`
        and `transformers_`.

        Parameters
        ----------
        transform : {"default", "pandas"}, default=None
            Configure output of `transform` and `fit_transform`.

            - `"default"`: Default output format of a transformer
            - `"pandas"`: DataFrame output
            - `None`: Transform configuration is unchanged

        Returns
        -------
        self : estimator instance
            Estimator instance.
        """
        super().set_output(transform=transform)
        transformers = (
            trans
            for _, trans, _ in chain(
                self.transformers, getattr(self, "transformers_", [])
            )
            if trans not in {"passthrough", "drop"}
        )
        for trans in transformers:
            _safe_set_output(trans, transform=transform)

        return self

    def get_params(self, deep=True):
        """Get parameters for this estimator.

        Returns the parameters given in the constructor as well as the
        estimators contained within the `transformers` of the
        `ColumnTransformer`.

        Parameters
        ----------
        deep : bool, default=True
            If True, will return the parameters for this estimator and
            contained subobjects that are estimators.

        Returns
        -------
        params : dict
            Parameter names mapped to their values.
        """
        return self._get_params("_transformers", deep=deep)
