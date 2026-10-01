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
class BaseExpression:
    """Base class for all query expressions."""

    def get_source_expressions(self):
        return []

    def set_source_expressions(self, exprs):
        assert not exprs

    def _parse_expressions(self, *expressions):
        return [
            arg
            if hasattr(arg, "resolve_expression")
            else (F(arg) if isinstance(arg, str) else Value(arg))
            for arg in expressions
        ]

    def as_sql(self, compiler, connection):
        """
        Responsible for returning a (sql, [params]) tuple to be included
        in the current query.

        Different backends can provide their own implementation, by
        providing an `as_{vendor}` method and patching the Expression:

        ```
        def override_as_sql(self, compiler, connection):
            # custom logic
            return super().as_sql(compiler, connection)
        setattr(Expression, 'as_' + connection.vendor, override_as_sql)
        ```

        Arguments:
         * compiler: the query compiler responsible for generating the query.
           Must have a compile method, returning a (sql, [params]) tuple.
           Calling compiler(value) will return a quoted `value`.

         * connection: the database connection used for the current query.

        Return: (sql, params)
          Where `sql` is a string containing ordered sql parameters to be
          replaced with the elements of the list `params`.
        """
        raise NotImplementedError("Subclasses must implement as_sql()")

    @cached_property
    def contains_aggregate(self):
        return any(
            expr and expr.contains_aggregate for expr in self.get_source_expressions()
        )

    @cached_property
    def contains_over_clause(self):
        return any(
            expr and expr.contains_over_clause for expr in self.get_source_expressions()
        )

    @cached_property
    def contains_column_references(self):
        return any(
            expr and expr.contains_column_references
            for expr in self.get_source_expressions()
        )

class CombinedExpression(SQLiteNumericMixin, Expression):
    def set_source_expressions(self, exprs):
        self.lhs, self.rhs = exprs

    def _resolve_output_field(self):
        # We avoid using super() here for reasons given in
        # Expression._resolve_output_field()
        combined_type = _resolve_combined_type(
            self.connector,
            type(self.lhs._output_field_or_none),
            type(self.rhs._output_field_or_none),
        )
        if combined_type is None:
            raise FieldError(
                f"Cannot infer type of {self.connector!r} expression involving these "
                f"types: {self.lhs.output_field.__class__.__name__}, "
                f"{self.rhs.output_field.__class__.__name__}. You must set "
                f"output_field."
            )
        return combined_type()

    def as_sql(self, compiler, connection):
        expressions = []
        expression_params = []
        sql, params = compiler.compile(self.lhs)
        expressions.append(sql)
        expression_params.extend(params)
        sql, params = compiler.compile(self.rhs)
        expressions.append(sql)
        expression_params.extend(params)
        # order of precedence
        expression_wrapper = "(%s)"
        sql = connection.ops.combine_expression(self.connector, expressions)
        return expression_wrapper % sql, expression_params

class DurationExpression(CombinedExpression):
    def compile(self, side, compiler, connection):
        try:
            output = side.output_field
        except FieldError:
            pass
        else:
            if output.get_internal_type() == "DurationField":
                sql, params = compiler.compile(side)
                return connection.ops.format_for_duration_arithmetic(sql), params
        return compiler.compile(side)

    def as_sql(self, compiler, connection):
        if connection.features.has_native_duration_field:
            return super().as_sql(compiler, connection)
        connection.ops.check_expression_support(self)
        expressions = []
        expression_params = []
        sql, params = self.compile(self.lhs, compiler, connection)
        expressions.append(sql)
        expression_params.extend(params)
        sql, params = self.compile(self.rhs, compiler, connection)
        expressions.append(sql)
        expression_params.extend(params)
        # order of precedence
        expression_wrapper = "(%s)"
        sql = connection.ops.combine_duration_expression(self.connector, expressions)
        return expression_wrapper % sql, expression_params

class TemporalSubtraction(CombinedExpression):
    output_field = fields.DurationField()

    def __init__(self, lhs, rhs):
        super().__init__(lhs, self.SUB, rhs)

    def as_sql(self, compiler, connection):
        connection.ops.check_expression_support(self)
        lhs = compiler.compile(self.lhs)
        rhs = compiler.compile(self.rhs)
        return connection.ops.subtract_temporals(
            self.lhs.output_field.get_internal_type(), lhs, rhs
        )
class ResolvedOuterRef(F):
    """
    An object that contains a reference to an outer query.

    In this case, the reference to the outer query has been resolved because
    the inner query has been used as a subquery.
    """

    contains_aggregate = False

    def as_sql(self, *args, **kwargs):
        raise ValueError(
            "This queryset contains a reference to an outer query and may "
            "only be used in a subquery."
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
@deconstructible(path="django.db.models.Func")
class Func(SQLiteNumericMixin, Expression):
    """An SQL function call."""

    def get_source_expressions(self):
        return self.source_expressions

    def set_source_expressions(self, exprs):
        self.source_expressions = exprs

    def resolve_expression(
        self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False
    ):
        c = self.copy()
        c.is_summary = summarize
        for pos, arg in enumerate(c.source_expressions):
            c.source_expressions[pos] = arg.resolve_expression(
                query, allow_joins, reuse, summarize, for_save
            )
        return c

    def as_sql(
        self,
        compiler,
        connection,
        function=None,
        template=None,
        arg_joiner=None,
        **extra_context,
    ):
        connection.ops.check_expression_support(self)
        sql_parts = []
        params = []
        for arg in self.source_expressions:
            try:
                arg_sql, arg_params = compiler.compile(arg)
            except EmptyResultSet:
                empty_result_set_value = getattr(
                    arg, "empty_result_set_value", NotImplemented
                )
                if empty_result_set_value is NotImplemented:
                    raise
                arg_sql, arg_params = compiler.compile(Value(empty_result_set_value))
            sql_parts.append(arg_sql)
            params.extend(arg_params)
        data = {**self.extra, **extra_context}
        # Use the first supplied value in this order: the parameter to this
        # method, a value supplied in __init__()'s **extra (the value in
        # `data`), or the value defined on the class.
        if function is not None:
            data["function"] = function
        else:
            data.setdefault("function", self.function)
        template = template or data.get("template", self.template)
        arg_joiner = arg_joiner or data.get("arg_joiner", self.arg_joiner)
        data["expressions"] = data["field"] = arg_joiner.join(sql_parts)
        return template % data, params

    def copy(self):
        copy = super().copy()
        copy.source_expressions = self.source_expressions[:]
        copy.extra = self.extra.copy()
        return copy
@deconstructible(path="django.db.models.Value")
class Value(SQLiteNumericMixin, Expression):
    """Represent a wrapped value as a node within an expression."""

    # Provide a default value for `for_save` in order to allow unresolved
    # instances to be compiled until a decision is taken in #25425.
    for_save = False

    def __init__(self, value, output_field=None):
        """
        Arguments:
         * value: the value this expression represents. The value will be
           added into the sql parameter list and properly quoted.

         * output_field: an instance of the model field type that this
           expression will return, such as IntegerField() or CharField().
        """
        super().__init__(output_field=output_field)
        self.value = value

    def __repr__(self):
        return f"{self.__class__.__name__}({self.value!r})"

    def as_sql(self, compiler, connection):
        connection.ops.check_expression_support(self)
        val = self.value
        output_field = self._output_field_or_none
        if output_field is not None:
            if self.for_save:
                val = output_field.get_db_prep_save(val, connection=connection)
            else:
                val = output_field.get_db_prep_value(val, connection=connection)
            if hasattr(output_field, "get_placeholder"):
                return output_field.get_placeholder(val, compiler, connection), [val]
        if val is None:
            # cx_Oracle does not always convert None to the appropriate
            # NULL type (like in case expressions using numbers), so we
            # use a literal SQL NULL
            return "NULL", []
        return "%s", [val]

    def resolve_expression(
        self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False
    ):
        c = super().resolve_expression(query, allow_joins, reuse, summarize, for_save)
        c.for_save = for_save
        return c

    def get_group_by_cols(self, alias=None):
        return []

class RawSQL(Expression):
    def __init__(self, sql, params, output_field=None):
        if output_field is None:
            output_field = fields.Field()
        self.sql, self.params = sql, params
        super().__init__(output_field=output_field)

    def __repr__(self):
        return "{}({}, {})".format(self.__class__.__name__, self.sql, self.params)

    def as_sql(self, compiler, connection):
        return "(%s)" % self.sql, self.params

    def get_group_by_cols(self, alias=None):
        return [self]

    def resolve_expression(
        self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False
    ):
        # Resolve parents fields used in raw SQL.
        if query.model:
            for parent in query.model._meta.get_parent_list():
                for parent_field in parent._meta.local_fields:
                    _, column_name = parent_field.get_attname_column()
                    if column_name.lower() in self.sql.lower():
                        query.resolve_ref(
                            parent_field.name, allow_joins, reuse, summarize
                        )
                        break
        return super().resolve_expression(
            query, allow_joins, reuse, summarize, for_save
        )
class Star(Expression):
    def __repr__(self):
        return "'*'"

    def as_sql(self, compiler, connection):
        return "*", []
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
        return "{}({})".format(self.__class__.__name__, ", ".join(identifiers))

    def as_sql(self, compiler, connection):
        alias, column = self.alias, self.target.column
        identifiers = (alias, column) if alias else (column,)
        sql = ".".join(map(compiler.quote_name_unless_alias, identifiers))
        return sql, []

    def relabeled_clone(self, relabels):
        if self.alias is None:
            return self
        return self.__class__(
            relabels.get(self.alias, self.alias), self.target, self.output_field
        )

    def get_group_by_cols(self, alias=None):
        return [self]

    def get_db_converters(self, connection):
        if self.target == self.output_field:
            return self.output_field.get_db_converters(connection)
        return self.output_field.get_db_converters(
            connection
        ) + self.target.get_db_converters(connection)
class Ref(Expression):
    """
    Reference to column alias of the query. For example, Ref('sum_cost') in
    qs.annotate(sum_cost=Sum('cost')) query.
    """

    def __repr__(self):
        return "{}({}, {})".format(self.__class__.__name__, self.refs, self.source)

    def get_source_expressions(self):
        return [self.source]

    def set_source_expressions(self, exprs):
        (self.source,) = exprs

    def resolve_expression(
        self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False
    ):
        # The sub-expression `source` has already been resolved, as this is
        # just a reference to the name of `source`.
        return self

    def relabeled_clone(self, relabels):
        return self

    def as_sql(self, compiler, connection):
        return connection.ops.quote_name(self.refs), []

    def get_group_by_cols(self, alias=None):
        return [self]
class OrderByList(Func):
    template = "ORDER BY %(expressions)s"

    def __init__(self, *expressions, **extra):
        expressions = (
            (
                OrderBy(F(expr[1:]), descending=True)
                if isinstance(expr, str) and expr[0] == "-"
                else expr
            )
            for expr in expressions
        )
        super().__init__(*expressions, **extra)

    def as_sql(self, *args, **kwargs):
        if not self.source_expressions:
            return "", ()
        return super().as_sql(*args, **kwargs)
@deconstructible(path="django.db.models.ExpressionWrapper")
class ExpressionWrapper(SQLiteNumericMixin, Expression):
    """
    An expression that can wrap another expression so that it can provide
    extra context to the inner expression, such as the output_field.
    """

    def __init__(self, expression, output_field):
        super().__init__(output_field=output_field)
        self.expression = expression

    def set_source_expressions(self, exprs):
        self.expression = exprs[0]

    def get_source_expressions(self):
        return [self.expression]

    def get_group_by_cols(self, alias=None):
        if isinstance(self.expression, Expression):
            expression = self.expression.copy()
            expression.output_field = self.output_field
            return expression.get_group_by_cols(alias=alias)
        # For non-expressions e.g. an SQL WHERE clause, the entire
        # `expression` must be included in the GROUP BY clause.
        return super().get_group_by_cols()

    def as_sql(self, compiler, connection):
        return compiler.compile(self.expression)

    def __repr__(self):
        return "{}({})".format(self.__class__.__name__, self.expression)
@deconstructible(path="django.db.models.When")
class When(Expression):

    def get_source_fields(self):
        # We're only interested in the fields of the result expressions.
        return [self.result._output_field_or_none]

    def resolve_expression(
        self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False
    ):
        c = self.copy()
        c.is_summary = summarize
        if hasattr(c.condition, "resolve_expression"):
            c.condition = c.condition.resolve_expression(
                query, allow_joins, reuse, summarize, False
            )
        c.result = c.result.resolve_expression(
            query, allow_joins, reuse, summarize, for_save
        )
        return c

    def as_sql(self, compiler, connection, template=None, **extra_context):
        connection.ops.check_expression_support(self)
        template_params = extra_context
        sql_params = []
        condition_sql, condition_params = compiler.compile(self.condition)
        template_params["condition"] = condition_sql
        sql_params.extend(condition_params)
        result_sql, result_params = compiler.compile(self.result)
        template_params["result"] = result_sql
        sql_params.extend(result_params)
        template = template or self.template
        return template % template_params, sql_params

    def get_group_by_cols(self, alias=None):
        # This is not a complete expression and cannot be used in GROUP BY.
        cols = []
        for source in self.get_source_expressions():
            cols.extend(source.get_group_by_cols())
        return cols
@deconstructible(path="django.db.models.Case")
class Case(SQLiteNumericMixin, Expression):
    """
    An SQL searched CASE expression:

        CASE
            WHEN n > 0
                THEN 'positive'
            WHEN n < 0
                THEN 'negative'
            ELSE 'zero'
        END
    """

    def resolve_expression(
        self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False
    ):
        c = self.copy()
        c.is_summary = summarize
        for pos, case in enumerate(c.cases):
            c.cases[pos] = case.resolve_expression(
                query, allow_joins, reuse, summarize, for_save
            )
        c.default = c.default.resolve_expression(
            query, allow_joins, reuse, summarize, for_save
        )
        return c

    def copy(self):
        c = super().copy()
        c.cases = c.cases[:]
        return c

    def as_sql(
        self, compiler, connection, template=None, case_joiner=None, **extra_context
    ):
        connection.ops.check_expression_support(self)
        if not self.cases:
            return compiler.compile(self.default)
        template_params = {**self.extra, **extra_context}
        case_parts = []
        sql_params = []
        for case in self.cases:
            try:
                case_sql, case_params = compiler.compile(case)
            except EmptyResultSet:
                continue
            case_parts.append(case_sql)
            sql_params.extend(case_params)
        default_sql, default_params = compiler.compile(self.default)
        if not case_parts:
            return default_sql, default_params
        case_joiner = case_joiner or self.case_joiner
        template_params["cases"] = case_joiner.join(case_parts)
        template_params["default"] = default_sql
        sql_params.extend(default_params)
        template = template or template_params.get("template", self.template)
        sql = template % template_params
        if self._output_field_or_none is not None:
            sql = connection.ops.unification_cast_sql(self.output_field) % sql
        return sql, sql_params

    def get_group_by_cols(self, alias=None):
        if not self.cases:
            return self.default.get_group_by_cols(alias)
        return super().get_group_by_cols(alias)
class Subquery(BaseExpression, Combinable):
    """
    An explicit subquery. It may contain OuterRef() references to the outer
    query which will be resolved when it is applied to that query.
    """

    def set_source_expressions(self, exprs):
        self.query = exprs[0]

    def _resolve_output_field(self):
        return self.query.output_field

    def copy(self):
        clone = super().copy()
        clone.query = clone.query.clone()
        return clone

    @property
    def external_aliases(self):
        return self.query.external_aliases

    def get_external_cols(self):
        return self.query.get_external_cols()

    def as_sql(self, compiler, connection, template=None, query=None, **extra_context):
        connection.ops.check_expression_support(self)
        template_params = {**self.extra, **extra_context}
        query = query or self.query
        subquery_sql, sql_params = query.as_sql(compiler, connection)
        template_params["subquery"] = subquery_sql[1:-1]

        template = template or template_params.get("template", self.template)
        sql = template % template_params
        return sql, sql_params

    def get_group_by_cols(self, alias=None):
        # If this expression is referenced by an alias for an explicit GROUP BY
        # through values() a reference to this expression and not the
        # underlying .query must be returned to ensure external column
        # references are not grouped against as well.
        if alias:
            return [Ref(alias, self)]
        return self.query.get_group_by_cols()
class Exists(Subquery):
    template = "EXISTS(%(subquery)s)"
    output_field = fields.BooleanField()

    def __init__(self, queryset, negated=False, **kwargs):
        self.negated = negated
        super().__init__(queryset, **kwargs)

    def __invert__(self):
        clone = self.copy()
        clone.negated = not self.negated
        return clone

    def as_sql(self, compiler, connection, template=None, **extra_context):
        query = self.query.exists(using=connection.alias)
        try:
            sql, params = super().as_sql(
                compiler,
                connection,
                template=template,
                query=query,
                **extra_context,
            )
        except EmptyResultSet:
            if self.negated:
                features = compiler.connection.features
                if not features.supports_boolean_expr_in_select_clause:
                    return "1=1", ()
                return compiler.compile(Value(True))
            raise
        if self.negated:
            sql = "NOT {}".format(sql)
        return sql, params

    def select_format(self, compiler, sql, params):
        # Wrap EXISTS() with a CASE WHEN expression if a database backend
        # (e.g. Oracle) doesn't support boolean expression in SELECT or GROUP
        # BY list.
        if not compiler.connection.features.supports_boolean_expr_in_select_clause:
            sql = "CASE WHEN {} THEN 1 ELSE 0 END".format(sql)
        return sql, params
@deconstructible(path="django.db.models.OrderBy")
class OrderBy(Expression):

    def __repr__(self):
        return "{}({}, descending={})".format(
            self.__class__.__name__, self.expression, self.descending
        )

    def set_source_expressions(self, exprs):
        self.expression = exprs[0]

    def get_source_expressions(self):
        return [self.expression]

    def as_sql(self, compiler, connection, template=None, **extra_context):
        template = template or self.template
        if connection.features.supports_order_by_nulls_modifier:
            if self.nulls_last:
                template = "%s NULLS LAST" % template
            elif self.nulls_first:
                template = "%s NULLS FIRST" % template
        else:
            if self.nulls_last and not (
                self.descending and connection.features.order_by_nulls_first
            ):
                template = "%%(expression)s IS NULL, %s" % template
            elif self.nulls_first and not (
                not self.descending and connection.features.order_by_nulls_first
            ):
                template = "%%(expression)s IS NOT NULL, %s" % template
        connection.ops.check_expression_support(self)
        expression_sql, params = compiler.compile(self.expression)
        placeholders = {
            "expression": expression_sql,
            "ordering": "DESC" if self.descending else "ASC",
            **extra_context,
        }
        params *= template.count("%(expression)s")
        return (template % placeholders).rstrip(), params

    def as_oracle(self, compiler, connection):
        # Oracle doesn't allow ORDER BY EXISTS() or filters unless it's wrapped
        # in a CASE WHEN.
        if connection.ops.conditional_expression_supported_in_where_clause(
            self.expression
        ):
            copy = self.copy()
            copy.expression = Case(
                When(self.expression, then=True),
                default=False,
            )
            return copy.as_sql(compiler, connection)
        return self.as_sql(compiler, connection)

    def get_group_by_cols(self, alias=None):
        cols = []
        for source in self.get_source_expressions():
            cols.extend(source.get_group_by_cols())
        return cols
class Window(SQLiteNumericMixin, Expression):

    def _resolve_output_field(self):
        return self.source_expression.output_field

    def get_source_expressions(self):
        return [self.source_expression, self.partition_by, self.order_by, self.frame]

    def set_source_expressions(self, exprs):
        self.source_expression, self.partition_by, self.order_by, self.frame = exprs

    def as_sql(self, compiler, connection, template=None):
        connection.ops.check_expression_support(self)
        if not connection.features.supports_over_clause:
            raise NotSupportedError("This backend does not support window expressions.")
        expr_sql, params = compiler.compile(self.source_expression)
        window_sql, window_params = [], ()

        if self.partition_by is not None:
            sql_expr, sql_params = self.partition_by.as_sql(
                compiler=compiler,
                connection=connection,
                template="PARTITION BY %(expressions)s",
            )
            window_sql.append(sql_expr)
            window_params += tuple(sql_params)

        if self.order_by is not None:
            order_sql, order_params = compiler.compile(self.order_by)
            window_sql.append(order_sql)
            window_params += tuple(order_params)

        if self.frame:
            frame_sql, frame_params = compiler.compile(self.frame)
            window_sql.append(frame_sql)
            window_params += tuple(frame_params)

        template = template or self.template

        return (
            template % {"expression": expr_sql, "window": " ".join(window_sql).strip()},
            (*params, *window_params),
        )

    def as_sqlite(self, compiler, connection):
        if isinstance(self.output_field, fields.DecimalField):
            # Casting to numeric must be outside of the window expression.
            copy = self.copy()
            source_expressions = copy.get_source_expressions()
            source_expressions[0].output_field = fields.FloatField()
            copy.set_source_expressions(source_expressions)
            return super(Window, copy).as_sqlite(compiler, connection)
        return self.as_sql(compiler, connection)

    def __str__(self):
        return "{} OVER ({}{}{})".format(
            str(self.source_expression),
            "PARTITION BY " + str(self.partition_by) if self.partition_by else "",
            str(self.order_by or ""),
            str(self.frame or ""),
        )

class WindowFrame(Expression):
    """
    Model the frame clause in window expressions. There are two types of frame
    clauses which are subclasses, however, all processing and validation (by no
    means intended to be complete) is done here. Thus, providing an end for a
    frame is optional (the default is UNBOUNDED FOLLOWING, which is the last
    row in the frame).
    """

    template = "%(frame_type)s BETWEEN %(start)s AND %(end)s"

    def __init__(self, start=None, end=None):
        self.start = Value(start)
        self.end = Value(end)

    def set_source_expressions(self, exprs):
        self.start, self.end = exprs

    def get_source_expressions(self):
        return [self.start, self.end]

    def as_sql(self, compiler, connection):
        connection.ops.check_expression_support(self)
        start, end = self.window_frame_start_end(
            connection, self.start.value, self.end.value
        )
        return (
            self.template
            % {
                "frame_type": self.frame_type,
                "start": start,
                "end": end,
            },
            [],
        )

    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self)

    def get_group_by_cols(self, alias=None):
        return []

