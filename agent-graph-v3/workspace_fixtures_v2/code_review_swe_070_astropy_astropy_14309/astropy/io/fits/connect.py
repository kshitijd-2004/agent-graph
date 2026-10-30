import os
import re
import warnings
from copy import deepcopy
import numpy as np
from astropy import units as u
from astropy.io import registry as io_registry
from astropy.table import Column, MaskedColumn, Table, meta, serialize
from astropy.time import Time
from astropy.utils.data_info import serialize_context_as
from astropy.utils.exceptions import AstropyDeprecationWarning, AstropyUserWarning
from astropy.utils.misc import NOT_OVERWRITING_MSG
from . import BinTableHDU, GroupsHDU, HDUList, TableHDU
from . import append as fits_append
from .column import KEYWORD_NAMES, _fortran_to_python_format
from .convenience import table_to_hdu
from .hdu.hdulist import FITS_SIGNATURE
from .hdu.hdulist import fitsopen as fits_open
from .util import first
REMOVE_KEYWORDS = [
    "XTENSION",
    "BITPIX",
    "NAXIS",
    "NAXIS1",
    "NAXIS2",
    "PCOUNT",
    "GCOUNT",
    "TFIELDS",
    "THEAP",
]

# Column-specific keywords regex
COLUMN_KEYWORD_REGEXP = "(" + "|".join(KEYWORD_NAMES) + ")[0-9]+"


def is_column_keyword(keyword):
    return re.match(COLUMN_KEYWORD_REGEXP, keyword) is not None


def is_fits(origin, filepath, fileobj, *args, **kwargs):
    """
    Determine whether `origin` is a FITS file.

    Parameters
    ----------
    origin : str or readable file-like
        Path or file object containing a potential FITS file.

    Returns
    -------
    is_fits : bool
        Returns `True` if the given file is a FITS file.
    """
    if fileobj is not None:
        pos = fileobj.tell()
        sig = fileobj.read(30)
        fileobj.seek(pos)
        return sig == FITS_SIGNATURE
    elif filepath is not None:
        if filepath.lower().endswith(
            (".fits", ".fits.gz", ".fit", ".fit.gz", ".fts", ".fts.gz")
        ):
            return True
    return isinstance(args[0], (HDUList, TableHDU, BinTableHDU, GroupsHDU))


