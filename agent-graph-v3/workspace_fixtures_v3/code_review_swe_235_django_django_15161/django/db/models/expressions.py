import copy
import datetime
import functools
import inspect
from decimal import Decimal
from uuid import UUID
from django.core.exceptions import EmptyResultSet, FieldError
from django.db import DatabaseError, NotSupportedError, connection
from django.db.models import fields
from django.db.models.constants import LOOKUP_SEP
from django.db.models.query_utils import Q
from django.utils.deconstruct import deconstructible
from django.utils.functional import cached_property
from django.utils.hashable import make_hashable
class BaseExpression:
    """Base class for all query expressions."""

    def get_lookup(self, lookup):
        return self.output_field.get_lookup(lookup)

    def get_transform(self, name):
        return self.output_field.get_transform(name)

    def relabeled_clone(self, change_map):
        clone = self.copy()
        clone.set_source_expressions([
            e.relabeled_clone(change_map) if e is not None else None
            for e in self.get_source_expressions()
        ])
        return clone

    def copy(self):
        return copy.copy(self)

    def get_group_by_cols(self, alias=None):
        if not self.contains_aggregate:
            return [self]
        cols = []
        for source in self.get_source_expressions():
            cols.extend(source.get_group_by_cols())
        return cols

    def get_source_fields(self):
        """Return the underlying field types used by this aggregate."""
        return [e._output_field_or_none for e in self.get_source_expressions()]

    def asc(self, **kwargs):
        return OrderBy(self, **kwargs)

class ResolvedOuterRef(F):
    """
    An object that contains a reference to an outer query.

    In this case, the reference to the outer query has been resolved because
    the inner query has been used as a subquery.
    """
    contains_aggregate = False

    def as_sql(self, *args, **kwargs):
        raise ValueError(
            'This queryset contains a reference to an outer query and may '
            'only be used in a subquery.'
        )

    def resolve_expression(self, *args, **kwargs):
        col = super().resolve_expression(*args, **kwargs)
        # FIXME: Rename possibly_multivalued to multivalued and fix detection
        # for non-multivalued JOINs (e.g. foreign key fields). This should take
        # into account only many-to-many and one-to-many relationships.
        col.possibly_multivalued = LOOKUP_SEP in self.name
        return col

    def relabeled_clone(self, relabels):
        return self

    def get_group_by_cols(self, alias=None):
        return []
class OuterRef(F):
    contains_aggregate = False

    def resolve_expression(self, *args, **kwargs):
        if isinstance(self.name, self.__class__):
            return self.name
        return ResolvedOuterRef(self.name)

    def relabeled_clone(self, relabels):
        return self
class Col(Expression):

    contains_column_references = True
    possibly_multivalued = False

    def __init__(self, alias, target, output_field=None):
        if output_field is None:
            output_field = target
        super().__init__(output_field=output_field)
        self.alias, self.target = alias, target

    def __repr__(self):
        alias, target = self.alias, self.target
        identifiers = (alias, str(target)) if alias else (str(target),)
        return '{}({})'.format(self.__class__.__name__, ', '.join(identifiers))

    def as_sql(self, compiler, connection):
        alias, column = self.alias, self.target.column
        identifiers = (alias, column) if alias else (column,)
        sql = '.'.join(map(compiler.quote_name_unless_alias, identifiers))
        return sql, []

    def relabeled_clone(self, relabels):
        if self.alias is None:
            return self
        return self.__class__(relabels.get(self.alias, self.alias), self.target, self.output_field)

    def get_group_by_cols(self, alias=None):
        return [self]

    def get_db_converters(self, connection):
        if self.target == self.output_field:
            return self.output_field.get_db_converters(connection)
        return (self.output_field.get_db_converters(connection) +
                self.target.get_db_converters(connection))
class Ref(Expression):
    """
    Reference to column alias of the query. For example, Ref('sum_cost') in
    qs.annotate(sum_cost=Sum('cost')) query.
    """
    def __init__(self, refs, source):
        super().__init__()
        self.refs, self.source = refs, source

    def __repr__(self):
        return "{}({}, {})".format(self.__class__.__name__, self.refs, self.source)

    def get_source_expressions(self):
        return [self.source]

    def set_source_expressions(self, exprs):
        self.source, = exprs

    def resolve_expression(self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False):
        # The sub-expression `source` has already been resolved, as this is
        # just a reference to the name of `source`.
        return self

    def relabeled_clone(self, relabels):
        return self

    def as_sql(self, compiler, connection):
        return connection.ops.quote_name(self.refs), []

    def get_group_by_cols(self, alias=None):
        return [self]
