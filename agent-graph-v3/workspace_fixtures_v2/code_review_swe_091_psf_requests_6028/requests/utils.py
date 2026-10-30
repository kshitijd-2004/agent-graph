"""
requests.utils
~~~~~~~~~~~~~~

This module provides utility functions that are used within Requests
that are also useful for external consumption.
"""
import codecs
import contextlib
import io
import os
import re
import socket
import struct
import sys
import tempfile
import warnings
import zipfile
from collections import OrderedDict
from urllib3.util import make_headers
from urllib3.util import parse_url
from .__version__ import __version__
from . import certs
from ._internal_utils import to_native_string
from .compat import parse_http_list as _parse_list_header
from .compat import (
    quote, urlparse, bytes, str, unquote, getproxies,
    proxy_bypass, urlunparse, basestring, integer_types, is_py3,
    proxy_bypass_environment, getproxies_environment, Mapping)
from .cookies import cookiejar_from_dict
from .structures import CaseInsensitiveDict
from .exceptions import (
    InvalidURL, InvalidHeader, FileModeWarning, UnrewindableBodyError)


def prepend_scheme_if_needed(url, new_scheme):
    """Given a URL that may or may not have a scheme, prepend the given scheme.
    Does not replace a present scheme with the one provided as an argument.

    :rtype: str
    """
    parsed = parse_url(url)
    scheme, auth, host, port, path, query, fragment = parsed

    # A defect in urlparse determines that there isn't a netloc present in some
    # urls. We previously assumed parsing was overly cautious, and swapped the
    # netloc and path. Due to a lack of tests on the original defect, this is
    # maintained with parse_url for backwards compatibility.
    netloc = parsed.netloc
    if not netloc:
        netloc, path = path, netloc

    if scheme is None:
        scheme = new_scheme
    if path is None:
        path = ''

    return urlunparse((scheme, netloc, path, '', query, fragment))


def get_auth_from_url(url):
    """Given a url with authentication components, extract them into a tuple of
    username,password.

    :rtype: (str,str)
    """
    parsed = urlparse(url)

    try:
        auth = (unquote(parsed.username), unquote(parsed.password))
    except (AttributeError, TypeError):
        auth = ('', '')

    return auth


# Moved outside of function to avoid recompile every call
_CLEAN_HEADER_REGEX_BYTE = re.compile(b'^\\S[^\\r\\n]*$|^$')
