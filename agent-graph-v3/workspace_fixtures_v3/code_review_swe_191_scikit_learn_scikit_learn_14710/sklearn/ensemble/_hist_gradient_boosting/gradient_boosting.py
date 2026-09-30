"""Fast Gradient Boosting decision trees for classification and regression."""
from abc import ABC, abstractmethod
from functools import partial
import numpy as np
from timeit import default_timer as time
from ...base import (BaseEstimator, RegressorMixin, ClassifierMixin,
                     is_classifier)
from ...utils import check_X_y, check_random_state, check_array, resample
from ...utils.validation import check_is_fitted
from ...utils.multiclass import check_classification_targets
from ...metrics import check_scoring
from ...model_selection import train_test_split
from ...preprocessing import LabelEncoder
from ._gradient_boosting import _update_raw_predictions
from .common import Y_DTYPE, X_DTYPE, X_BINNED_DTYPE
from .binning import _BinMapper
from .grower import TreeGrower
from .loss import _LOSSES
class BaseHistGradientBoosting(BaseEstimator, ABC):
    """Base class for histogram-based gradient boosting estimators."""
    def _get_small_trainset(self, X_binned_train, y_train, seed):
        """Compute the indices of the subsample set and return this set.

        For efficiency, we need to subsample the training set to compute scores
        with scorers.
        """
        subsample_size = 10000
        if X_binned_train.shape[0] > subsample_size:
            indices = np.arange(X_binned_train.shape[0])
            stratify = y_train if is_classifier(self) else None
            indices = resample(indices, n_samples=subsample_size,
                               replace=False, random_state=seed,
                               stratify=stratify)
            X_binned_small_train = X_binned_train[indices]
            y_small_train = y_train[indices]
            X_binned_small_train = np.ascontiguousarray(X_binned_small_train)
            return X_binned_small_train, y_small_train
        else:
            return X_binned_train, y_train

    def _check_early_stopping_scorer(self, X_binned_small_train, y_small_train,
                                     X_binned_val, y_val):
        """Check if fitting should be early-stopped based on scorer.

        Scores are computed on validation data or on training data.
        """
        self.train_score_.append(
            self.scorer_(self, X_binned_small_train, y_small_train)
        )

        if self._use_validation_data:
            self.validation_score_.append(
                self.scorer_(self, X_binned_val, y_val)
            )
            return self._should_stop(self.validation_score_)
        else:
            return self._should_stop(self.train_score_)

