import copy
import datetime
import functools
import inspect
import warnings
from collections import defaultdict
from decimal import Decimal
from uuid import UUID
from django.core.exceptions import EmptyResultSet, FieldError
from django.db import DatabaseError, NotSupportedError, connection
from django.db.models import fields
from django.db.models.constants import LOOKUP_SEP
from django.db.models.query_utils import Q
from django.utils.deconstruct import deconstructible
from django.utils.deprecation import RemovedInDjango50Warning
from django.utils.functional import cached_property
from django.utils.hashable import make_hashable
@deconstructible
class Expression(BaseExpression, Combinable):
    """An expression that can be combined with other expressions."""

    def __eq__(self, other):
        if not isinstance(other, Expression):
            return NotImplemented
        return other.identity == self.identity

    def __hash__(self):
        return hash(self.identity)
@deconstructible(path="django.db.models.F")
class F(Combinable):
    """An object capable of resolving references to existing query objects."""
    def __repr__(self):
        return "{}({})".format(self.__class__.__name__, self.name)

    def resolve_expression(
        self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False
    ):
        return query.resolve_ref(self.name, allow_joins, reuse, summarize)

    def replace_expressions(self, replacements):
        return replacements.get(self, self)

    def asc(self, **kwargs):
        return OrderBy(self, **kwargs)

    def desc(self, **kwargs):
        return OrderBy(self, descending=True, **kwargs)

    def __eq__(self, other):
        return self.__class__ == other.__class__ and self.name == other.name

    def __hash__(self):
        return hash(self.name)
