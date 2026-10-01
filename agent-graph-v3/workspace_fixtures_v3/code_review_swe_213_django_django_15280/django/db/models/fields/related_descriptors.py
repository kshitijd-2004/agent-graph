from django.core.exceptions import FieldError
from django.db import connections, router, transaction
from django.db.models import Q, signals
from django.db.models.query import QuerySet
from django.db.models.query_utils import DeferredAttribute
from django.db.models.utils import resolve_callables
from django.utils.functional import cached_property
class ForwardManyToOneDescriptor:
    """
    Accessor to the related object on the forward side of a many-to-one or
    one-to-one (via ForwardOneToOneDescriptor subclass) relation.

    In the example::

        class Child(Model):
            parent = ForeignKey(Parent, related_name='children')

    ``Child.parent`` is a ``ForwardManyToOneDescriptor`` instance.
    """

    def is_cached(self, instance):
        return self.field.is_cached(instance)

    def get_queryset(self, **hints):
        return self.field.remote_field.model._base_manager.db_manager(hints=hints).all()

    def get_prefetch_queryset(self, instances, queryset=None):
        if queryset is None:
            queryset = self.get_queryset()
        queryset._add_hints(instance=instances[0])

        rel_obj_attr = self.field.get_foreign_related_value
        instance_attr = self.field.get_local_related_value
        instances_dict = {instance_attr(inst): inst for inst in instances}
        related_field = self.field.foreign_related_fields[0]
        remote_field = self.field.remote_field

        # FIXME: This will need to be revisited when we introduce support for
        # composite fields. In the meantime we take this practical approach to
        # solve a regression on 1.6 when the reverse manager in hidden
        # (related_name ends with a '+'). Refs #21410.
        # The check for len(...) == 1 is a special case that allows the query
        # to be join-less and smaller. Refs #21760.
        if remote_field.is_hidden() or len(self.field.foreign_related_fields) == 1:
            query = {'%s__in' % related_field.name: {instance_attr(inst)[0] for inst in instances}}
        else:
            query = {'%s__in' % self.field.related_query_name(): instances}
        queryset = queryset.filter(**query)

        # Since we're going to assign directly in the cache,
        # we must manage the reverse relation cache manually.
        if not remote_field.multiple:
            for rel_obj in queryset:
                instance = instances_dict[rel_obj_attr(rel_obj)]
                remote_field.set_cached_value(rel_obj, instance)
        return queryset, rel_obj_attr, instance_attr, True, self.field.get_cache_name(), False

    def get_object(self, instance):
        qs = self.get_queryset(instance=instance)
        # Assuming the database enforces foreign keys, this won't fail.
        return qs.get(self.field.get_reverse_related_filter(instance))

class ReverseOneToOneDescriptor:
    """
    Accessor to the related object on the reverse side of a one-to-one
    relation.

    In the example::

        class Restaurant(Model):
            place = OneToOneField(Place, related_name='restaurant')

    ``Place.restaurant`` is a ``ReverseOneToOneDescriptor`` instance.
    """

    def is_cached(self, instance):
        return self.related.is_cached(instance)

    def get_queryset(self, **hints):
        return self.related.related_model._base_manager.db_manager(hints=hints).all()

    def get_prefetch_queryset(self, instances, queryset=None):
        if queryset is None:
            queryset = self.get_queryset()
        queryset._add_hints(instance=instances[0])

        rel_obj_attr = self.related.field.get_local_related_value
        instance_attr = self.related.field.get_foreign_related_value
        instances_dict = {instance_attr(inst): inst for inst in instances}
        query = {'%s__in' % self.related.field.name: instances}
        queryset = queryset.filter(**query)

        # Since we're going to assign directly in the cache,
        # we must manage the reverse relation cache manually.
        for rel_obj in queryset:
            instance = instances_dict[rel_obj_attr(rel_obj)]
            self.related.field.set_cached_value(rel_obj, instance)
        return queryset, rel_obj_attr, instance_attr, True, self.related.get_cache_name(), False

def create_reverse_many_to_one_manager(superclass, rel):
    """
    Create a manager for the reverse side of a many-to-one relation.

    This manager subclasses another manager, generally the default manager of
    the related model, and adds behaviors specific to many-to-one relations.
    """
    class RelatedManager(superclass):

        def _remove_prefetched_objects(self):
            try:
                self.instance._prefetched_objects_cache.pop(self.field.remote_field.get_cache_name())
            except (AttributeError, KeyError):
                pass  # nothing to clear from cache

        def get_queryset(self):
            try:
                return self.instance._prefetched_objects_cache[self.field.remote_field.get_cache_name()]
            except (AttributeError, KeyError):
                queryset = super().get_queryset()
                return self._apply_rel_filters(queryset)

        def get_prefetch_queryset(self, instances, queryset=None):
            if queryset is None:
                queryset = super().get_queryset()

            queryset._add_hints(instance=instances[0])
            queryset = queryset.using(queryset._db or self._db)

            rel_obj_attr = self.field.get_local_related_value
            instance_attr = self.field.get_foreign_related_value
            instances_dict = {instance_attr(inst): inst for inst in instances}
            query = {'%s__in' % self.field.name: instances}
            queryset = queryset.filter(**query)

            # Since we just bypassed this class' get_queryset(), we must manage
            # the reverse relation manually.
            for rel_obj in queryset:
                instance = instances_dict[rel_obj_attr(rel_obj)]
                setattr(rel_obj, self.field.name, instance)
            cache_name = self.field.remote_field.get_cache_name()
            return queryset, rel_obj_attr, instance_attr, False, cache_name, False

def create_forward_many_to_many_manager(superclass, rel, reverse):
    """
    Create a manager for the either side of a many-to-many relation.

    This manager subclasses another manager, generally the default manager of
    the related model, and adds behaviors specific to many-to-many relations.
    """
    class ManyRelatedManager(superclass):

        def _remove_prefetched_objects(self):
            try:
                self.instance._prefetched_objects_cache.pop(self.prefetch_cache_name)
            except (AttributeError, KeyError):
                pass  # nothing to clear from cache

        def get_queryset(self):
            try:
                return self.instance._prefetched_objects_cache[self.prefetch_cache_name]
            except (AttributeError, KeyError):
                queryset = super().get_queryset()
                return self._apply_rel_filters(queryset)

        def get_prefetch_queryset(self, instances, queryset=None):
            if queryset is None:
                queryset = super().get_queryset()

            queryset._add_hints(instance=instances[0])
            queryset = queryset.using(queryset._db or self._db)

            query = {'%s__in' % self.query_field_name: instances}
            queryset = queryset._next_is_sticky().filter(**query)

            # M2M: need to annotate the query in order to get the primary model
            # that the secondary model was actually related to. We know that
            # there will already be a join on the join table, so we can just add
            # the select.

            # For non-autocreated 'through' models, can't assume we are
            # dealing with PK values.
            fk = self.through._meta.get_field(self.source_field_name)
            join_table = fk.model._meta.db_table
            connection = connections[queryset.db]
            qn = connection.ops.quote_name
            queryset = queryset.extra(select={
                '_prefetch_related_val_%s' % f.attname:
                '%s.%s' % (qn(join_table), qn(f.column)) for f in fk.local_related_fields})
            return (
                queryset,
                lambda result: tuple(
                    getattr(result, '_prefetch_related_val_%s' % f.attname)
                    for f in fk.local_related_fields
                ),
                lambda inst: tuple(
                    f.get_db_prep_value(getattr(inst, f.attname), connection)
                    for f in fk.foreign_related_fields
                ),
                False,
                self.prefetch_cache_name,
                False,
            )

        def add(self, *objs, through_defaults=None):
            self._remove_prefetched_objects()
            db = router.db_for_write(self.through, instance=self.instance)
            with transaction.atomic(using=db, savepoint=False):
                self._add_items(
                    self.source_field_name, self.target_field_name, *objs,
                    through_defaults=through_defaults,
                )
                # If this is a symmetrical m2m relation to self, add the mirror
                # entry in the m2m table.
                if self.symmetrical:
                    self._add_items(
                        self.target_field_name,
                        self.source_field_name,
                        *objs,
                        through_defaults=through_defaults,
                    )
        add.alters_data = True

