"""
A "grab bag" of relatively small general-purpose utilities that don't have
a clear module/package to live in.
"""
import abc
import contextlib
import difflib
import inspect
import json
import os
import signal
import sys
import traceback
import unicodedata
import locale
import threading
import re
import urllib.request
from itertools import zip_longest
from contextlib import contextmanager
from collections import defaultdict, OrderedDict
if sys.platform == 'win32':
    import ctypes

    def _has_hidden_attribute(filepath):
        """
        Returns True if the given filepath has the hidden attribute on
        MS-Windows.  Based on a post here:
        http://stackoverflow.com/questions/284115/cross-platform-hidden-file-detection
        """
        if isinstance(filepath, bytes):
            filepath = filepath.decode(sys.getfilesystemencoding())
        try:
            attrs = ctypes.windll.kernel32.GetFileAttributesW(filepath)
            result = bool(attrs & 2) and attrs != -1
        except AttributeError:
            result = False
        return result
else:
    def _has_hidden_attribute(filepath):
        return False
class InheritDocstrings(type):
    def __init__(cls, name, bases, dct):
        def is_public_member(key):
            return (
                (key.startswith('__') and key.endswith('__')
                 and len(key) > 4) or
                not key.startswith('_'))

        for key, val in dct.items():
            if (inspect.isfunction(val) and
                is_public_member(key) and
                val.__doc__ is None):
                for base in cls.__mro__[1:]:
                    super_method = getattr(base, key, None)
                    if super_method is not None:
                        val.__doc__ = super_method.__doc__
                        break

        super().__init__(name, bases, dct)
