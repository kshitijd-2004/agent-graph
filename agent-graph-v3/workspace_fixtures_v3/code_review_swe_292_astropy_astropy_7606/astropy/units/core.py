"""
Core units classes and functions
"""
import inspect
import operator
import textwrap
import warnings
import numpy as np
from ..utils.decorators import lazyproperty
from ..utils.exceptions import AstropyWarning
from ..utils.misc import isiterable, InheritDocstrings
from .utils import (is_effectively_unity, sanitize_scale, validate_power,
                    resolve_fractions)
from . import format as unit_format
class UnitBase(metaclass=InheritDocstrings):
    """
    Abstract base class for units.

    Most of the arithmetic operations on units are defined in this
    base class.

    Should not be instantiated by users directly.
    """

    def __hash__(self):
        # This must match the hash used in CompositeUnit for a unit
        # with only one base and no scale or power.
        return hash((str(self.scale), self.name, str('1')))

    def __eq__(self, other):
        if self is other:
            return True

        try:
            other = Unit(other, parse_strict='silent')
        except (ValueError, UnitsError, TypeError):
            return False

        # Other is Unit-like, but the test below requires it is a UnitBase
        # instance; if it is not, give up (so that other can try).
        if not isinstance(other, UnitBase):
            return NotImplemented

        try:
            return is_effectively_unity(self._to(other))
        except UnitsError:
            return False

    def __ne__(self, other):
        return not (self == other)

    def __le__(self, other):
        scale = self._to(Unit(other))
        return scale <= 1. or is_effectively_unity(scale)

    def __ge__(self, other):
        scale = self._to(Unit(other))
        return scale >= 1. or is_effectively_unity(scale)

    def __lt__(self, other):
        return not (self >= other)

    def __gt__(self, other):
        return not (self <= other)

    def __neg__(self):
        return self * -1.
class UnrecognizedUnit(IrreducibleUnit):
    """
    A unit that did not parse correctly.  This allows for
    roundtripping it as a string, but no unit operations actually work
    on it.

    Parameters
    ----------
    st : str
        The name of the unit.
    """

    def __bytes__(self):
        return self.name.encode('ascii', 'replace')

    def __str__(self):
        return self.name

    def to_string(self, format=None):
        return self.name

    def _unrecognized_operator(self, *args, **kwargs):
        raise ValueError(
            "The unit {0!r} is unrecognized, so all arithmetic operations "
            "with it are invalid.".format(self.name))

    __pow__ = __div__ = __rdiv__ = __truediv__ = __rtruediv__ = __mul__ = \
        __rmul__ = __lt__ = __gt__ = __le__ = __ge__ = __neg__ = \
        _unrecognized_operator

    def __eq__(self, other):
        other = Unit(other, parse_strict='silent')
        return isinstance(other, UnrecognizedUnit) and self.name == other.name

    def __ne__(self, other):
        return not (self == other)

    def is_equivalent(self, other, equivalencies=None):
        self._normalize_equivalencies(equivalencies)
        return self == other

    def _get_converter(self, other, equivalencies=None):
        self._normalize_equivalencies(equivalencies)
        raise ValueError(
            "The unit {0!r} is unrecognized.  It can not be converted "
            "to other units.".format(self.name))

    def get_format_name(self, format):
        return self.name

    def is_unity(self):
        return False
