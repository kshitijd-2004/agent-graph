from django.db.models.lookups import (
    Exact, GreaterThan, GreaterThanOrEqual, In, IsNull, LessThan,
    LessThanOrEqual,
)


class RelatedLookupMixin:
    def get_prep_lookup(self):
        if not isinstance(self.lhs, MultiColSource) and self.rhs_is_direct_value():
            # If we get here, we are dealing with single-column relations.
            self.rhs = get_normalized_value(self.rhs, self.lhs)[0]
            # We need to run the related field's get_prep_value(). Consider case
            # ForeignKey to IntegerField given value 'abc'. The ForeignKey itself
            # doesn't have validation for non-integers, so we must run validation
            # using the target field.
            if self.prepare_rhs and hasattr(self.lhs.output_field, 'get_path_info'):
                # Get the target field. We can safely assume there is only one
                # as we don't get to the direct value branch otherwise.
                target_field = self.lhs.output_field.get_path_info()[-1].target_fields[-1]
                self.rhs = target_field.get_prep_value(self.rhs)

        return super().get_prep_lookup()

    def as_sql(self, compiler, connection):
        if isinstance(self.lhs, MultiColSource):
            assert self.rhs_is_direct_value()
            self.rhs = get_normalized_value(self.rhs, self.lhs)
            from django.db.models.sql.where import WhereNode, AND
            root_constraint = WhereNode()
            for target, source, val in zip(self.lhs.targets, self.lhs.sources, self.rhs):
                lookup_class = target.get_lookup(self.lookup_name)
                root_constraint.add(
                    lookup_class(target.get_col(self.lhs.alias, source), val), AND)
            return root_constraint.as_sql(compiler, connection)
        return super().as_sql(compiler, connection)


class RelatedExact(RelatedLookupMixin, Exact):
    pass


class RelatedLessThan(RelatedLookupMixin, LessThan):
    pass


class RelatedGreaterThan(RelatedLookupMixin, GreaterThan):
    pass


class RelatedGreaterThanOrEqual(RelatedLookupMixin, GreaterThanOrEqual):
    pass


class RelatedLessThanOrEqual(RelatedLookupMixin, LessThanOrEqual):
    pass
