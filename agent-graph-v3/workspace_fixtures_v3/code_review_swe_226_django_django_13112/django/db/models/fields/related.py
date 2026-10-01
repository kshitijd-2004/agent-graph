import functools
import inspect
from functools import partial
from django import forms
from django.apps import apps
from django.conf import SettingsReference, settings
from django.core import checks, exceptions
from django.db import connection, router
from django.db.backends import utils
from django.db.models import Q
from django.db.models.constants import LOOKUP_SEP
from django.db.models.deletion import CASCADE, SET_DEFAULT, SET_NULL
from django.db.models.query_utils import PathInfo
from django.db.models.utils import make_model_tuple
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from . import Field
from .mixins import FieldCacheMixin
from .related_descriptors import (
    ForeignKeyDeferredAttribute, ForwardManyToOneDescriptor,
    ForwardOneToOneDescriptor, ManyToManyDescriptor,
    ReverseManyToOneDescriptor, ReverseOneToOneDescriptor,
)
from .related_lookups import (
    RelatedExact, RelatedGreaterThan, RelatedGreaterThanOrEqual, RelatedIn,
    RelatedIsNull, RelatedLessThan, RelatedLessThanOrEqual,
)
from .reverse_related import (
    ForeignObjectRel, ManyToManyRel, ManyToOneRel, OneToOneRel,
)
class RelatedField(FieldCacheMixin, Field):
    """Base class that all relational fields inherit from."""

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.remote_field.limit_choices_to:
            kwargs['limit_choices_to'] = self.remote_field.limit_choices_to
        if self.remote_field.related_name is not None:
            kwargs['related_name'] = self.remote_field.related_name
        if self.remote_field.related_query_name is not None:
            kwargs['related_query_name'] = self.remote_field.related_query_name
        return name, path, args, kwargs

    def get_forward_related_filter(self, obj):
        """
        Return the keyword arguments that when supplied to
        self.model.object.filter(), would select all instances related through
        this field to the remote obj. This is used to build the querysets
        returned by related descriptors. obj is an instance of
        self.related_field.model.
        """
        return {
            '%s__%s' % (self.name, rh_field.name): getattr(obj, rh_field.attname)
            for _, rh_field in self.related_fields
        }

class ForeignObject(RelatedField):
    """
    Abstraction of the ForeignKey relation to support multi-column relations.
    """

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs['on_delete'] = self.remote_field.on_delete
        kwargs['from_fields'] = self.from_fields
        kwargs['to_fields'] = self.to_fields

        if self.remote_field.parent_link:
            kwargs['parent_link'] = self.remote_field.parent_link
        if isinstance(self.remote_field.model, str):
            kwargs['to'] = self.remote_field.model.lower()
        else:
            kwargs['to'] = self.remote_field.model._meta.label_lower
        # If swappable is True, then see if we're actually pointing to the target
        # of a swap.
        swappable_setting = self.swappable_setting
        if swappable_setting is not None:
            # If it's already a settings reference, error
            if hasattr(kwargs['to'], "setting_name"):
                if kwargs['to'].setting_name != swappable_setting:
                    raise ValueError(
                        "Cannot deconstruct a ForeignKey pointing to a model "
                        "that is swapped in place of more than one model (%s and %s)"
                        % (kwargs['to'].setting_name, swappable_setting)
                    )
            # Set it
            kwargs['to'] = SettingsReference(
                kwargs['to'],
                swappable_setting,
            )
        return name, path, args, kwargs

    def resolve_related_fields(self):
        if not self.from_fields or len(self.from_fields) != len(self.to_fields):
            raise ValueError('Foreign Object from and to fields must be the same non-zero length')
        if isinstance(self.remote_field.model, str):
            raise ValueError('Related model %r cannot be resolved' % self.remote_field.model)
        related_fields = []
        for index in range(len(self.from_fields)):
            from_field_name = self.from_fields[index]
            to_field_name = self.to_fields[index]
            from_field = (
                self
                if from_field_name == RECURSIVE_RELATIONSHIP_CONSTANT
                else self.opts.get_field(from_field_name)
            )
            to_field = (self.remote_field.model._meta.pk if to_field_name is None
                        else self.remote_field.model._meta.get_field(to_field_name))
            related_fields.append((from_field, to_field))
        return related_fields

class ForeignKey(ForeignObject):
    """
    Provide a many-to-one relation by adding a column to the local model
    to hold the remote value.

    By default ForeignKey will target the pk of the remote model but this
    behavior can be changed by using the ``to_field`` argument.
    """

    def _check_unique(self, **kwargs):
        return [
            checks.Warning(
                'Setting unique=True on a ForeignKey has the same effect as using a OneToOneField.',
                hint='ForeignKey(unique=True) is usually better served by a OneToOneField.',
                obj=self,
                id='fields.W342',
            )
        ] if self.unique else []

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        del kwargs['to_fields']
        del kwargs['from_fields']
        # Handle the simpler arguments
        if self.db_index:
            del kwargs['db_index']
        else:
            kwargs['db_index'] = False
        if self.db_constraint is not True:
            kwargs['db_constraint'] = self.db_constraint
        # Rel needs more work.
        to_meta = getattr(self.remote_field.model, "_meta", None)
        if self.remote_field.field_name and (
                not to_meta or (to_meta.pk and self.remote_field.field_name != to_meta.pk.name)):
            kwargs['to_field'] = self.remote_field.field_name
        return name, path, args, kwargs

    def to_python(self, value):
        return self.target_field.to_python(value)

    @property
    def target_field(self):
        return self.foreign_related_fields[0]

class OneToOneField(ForeignKey):
    """
    A OneToOneField is essentially the same as a ForeignKey, with the exception
    that it always carries a "unique" constraint with it and the reverse
    relation always returns the object pointed to (since there will only ever
    be one), rather than returning a list.
    """

    # Field flags
    many_to_many = False
    many_to_one = False
    one_to_many = False
    one_to_one = True

    related_accessor_class = ReverseOneToOneDescriptor
    forward_related_accessor_class = ForwardOneToOneDescriptor
    rel_class = OneToOneRel

    description = _("One-to-one relationship")

    def __init__(self, to, on_delete, to_field=None, **kwargs):
        kwargs['unique'] = True
        super().__init__(to, on_delete, to_field=to_field, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if "unique" in kwargs:
            del kwargs['unique']
        return name, path, args, kwargs

    def formfield(self, **kwargs):
        if self.remote_field.parent_link:
            return None
        return super().formfield(**kwargs)

    def save_form_data(self, instance, data):
        if isinstance(data, self.remote_field.model):
            setattr(instance, self.name, data)
        else:
            setattr(instance, self.attname, data)
            # Remote field object must be cleared otherwise Model.save()
            # will reassign attname using the related object pk.
            if data is None:
                setattr(instance, self.name, data)

    def _check_unique(self, **kwargs):
        # Override ForeignKey since check isn't applicable here.
        return []
class ManyToManyField(RelatedField):
    """
    Provide a many-to-many relation by using an intermediary model that
    holds two ForeignKey fields pointed at the two sides of the relation.

    Unless a ``through`` model was provided, ManyToManyField will use the
    create_many_to_many_intermediary_model factory to automatically generate
    the intermediary model.
    """

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        # Handle the simpler arguments.
        if self.db_table is not None:
            kwargs['db_table'] = self.db_table
        if self.remote_field.db_constraint is not True:
            kwargs['db_constraint'] = self.remote_field.db_constraint
        # Rel needs more work.
        if isinstance(self.remote_field.model, str):
            kwargs['to'] = self.remote_field.model
        else:
            kwargs['to'] = "%s.%s" % (
                self.remote_field.model._meta.app_label,
                self.remote_field.model._meta.object_name,
            )
        if getattr(self.remote_field, 'through', None) is not None:
            if isinstance(self.remote_field.through, str):
                kwargs['through'] = self.remote_field.through
            elif not self.remote_field.through._meta.auto_created:
                kwargs['through'] = "%s.%s" % (
                    self.remote_field.through._meta.app_label,
                    self.remote_field.through._meta.object_name,
                )
        # If swappable is True, then see if we're actually pointing to the target
        # of a swap.
        swappable_setting = self.swappable_setting
        if swappable_setting is not None:
            # If it's already a settings reference, error.
            if hasattr(kwargs['to'], "setting_name"):
                if kwargs['to'].setting_name != swappable_setting:
                    raise ValueError(
                        "Cannot deconstruct a ManyToManyField pointing to a "
                        "model that is swapped in place of more than one model "
                        "(%s and %s)" % (kwargs['to'].setting_name, swappable_setting)
                    )

            kwargs['to'] = SettingsReference(
                kwargs['to'],
                swappable_setting,
            )
        return name, path, args, kwargs

