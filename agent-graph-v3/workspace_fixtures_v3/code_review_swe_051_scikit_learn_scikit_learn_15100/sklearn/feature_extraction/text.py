"""
The :mod:`sklearn.feature_extraction.text` submodule gathers utilities to
build feature vectors from text documents.
"""
import array
from collections import defaultdict
from collections.abc import Mapping
from functools import partial
import numbers
from operator import itemgetter
import re
import unicodedata
import warnings
import numpy as np
import scipy.sparse as sp
from ..base import BaseEstimator, TransformerMixin
from ..preprocessing import normalize
from .hashing import FeatureHasher
from .stop_words import ENGLISH_STOP_WORDS
from ..utils.validation import check_is_fitted, check_array, FLOAT_DTYPES
from ..utils import _IS_32BIT
from ..utils.fixes import _astype_copy_false
from ..exceptions import ChangedBehaviorWarning, NotFittedError


def strip_accents_unicode(s):
    """Transform accentuated unicode symbols into their simple counterpart

    Warning: the python-level loop and join operations make this
    implementation 20 times slower than the strip_accents_ascii basic
    normalization.

    Parameters
    ----------
    s : string
        The string to strip

    See also
    --------
    strip_accents_ascii
        Remove accentuated char for any unicode symbol that has a direct
        ASCII equivalent.
    """
    normalized = unicodedata.normalize('NFKD', s)
    if normalized == s:
        return s
    else:
        return ''.join([c for c in normalized if not unicodedata.combining(c)])


def strip_accents_ascii(s):
    """Transform accentuated unicode symbols into ascii or nothing

    Warning: this solution is only suited for languages that have a direct
    transliteration to ASCII symbols.

    Parameters
    ----------
    s : string
        The string to strip

    See also
    --------
    strip_accents_unicode
        Remove accentuated char for any unicode symbol.
    """
    nkfd_form = unicodedata.normalize('NFKD', s)
    return nkfd_form.encode('ASCII', 'ignore').decode('ASCII')
