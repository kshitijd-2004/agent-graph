from django.db import models
from django.db.migrations.operations.base import Operation
from django.db.migrations.state import ModelState
from django.db.models.options import normalize_together
from django.utils.functional import cached_property
from .fields import (
    AddField, AlterField, FieldOperation, RemoveField, RenameField,
)
from .utils import field_references, get_references, resolve_relation
class ModelOperation(Operation):
    def __init__(self, name):
        self.name = name

    @cached_property
    def name_lower(self):
        return self.name.lower()

    def references_model(self, name, app_label):
        return name.lower() == self.name_lower

    def reduce(self, operation, app_label):
        return (
            super().reduce(operation, app_label) or
            not operation.references_model(self.name, app_label)
        )
class CreateModel(ModelOperation):
    """Create a model's table."""

    def references_model(self, name, app_label):
        name_lower = name.lower()
        if name_lower == self.name_lower:
            return True

        # Check we didn't inherit from the model
        reference_model_tuple = (app_label, name_lower)
        for base in self.bases:
            if (base is not models.Model and isinstance(base, (models.base.ModelBase, str)) and
                    resolve_relation(base, app_label) == reference_model_tuple):
                return True

        # Check we have no FKs/M2Ms with it
        for _name, field in self.fields:
            if field_references((app_label, self.name_lower), field, reference_model_tuple):
                return True
        return False

    def reduce(self, operation, app_label):
        if (isinstance(operation, DeleteModel) and
                self.name_lower == operation.name_lower and
                not self.options.get("proxy", False)):
            return []
        elif isinstance(operation, RenameModel) and self.name_lower == operation.old_name_lower:
            return [
                CreateModel(
                    operation.new_name,
                    fields=self.fields,
                    options=self.options,
                    bases=self.bases,
                    managers=self.managers,
                ),
            ]
        elif isinstance(operation, AlterModelOptions) and self.name_lower == operation.name_lower:
            return [
                CreateModel(
                    self.name,
                    fields=self.fields,
                    options={**self.options, **operation.options},
                    bases=self.bases,
                    managers=self.managers,
                ),
            ]
        elif isinstance(operation, AlterTogetherOptionOperation) and self.name_lower == operation.name_lower:
            return [
                CreateModel(
                    self.name,
                    fields=self.fields,
                    options={**self.options, **{operation.option_name: operation.option_value}},
                    bases=self.bases,
                    managers=self.managers,
                ),
            ]
        elif isinstance(operation, AlterOrderWithRespectTo) and self.name_lower == operation.name_lower:
            return [
                CreateModel(
                    self.name,
                    fields=self.fields,
                    options={**self.options, 'order_with_respect_to': operation.order_with_respect_to},
                    bases=self.bases,
                    managers=self.managers,
                ),
            ]
        elif isinstance(operation, FieldOperation) and self.name_lower == operation.model_name_lower:
            if isinstance(operation, AddField):
                return [
                    CreateModel(
                        self.name,
                        fields=self.fields + [(operation.name, operation.field)],
                        options=self.options,
                        bases=self.bases,
                        managers=self.managers,
                    ),
                ]
            elif isinstance(operation, AlterField):
                return [
                    CreateModel(
                        self.name,
                        fields=[
                            (n, operation.field if n == operation.name else v)
                            for n, v in self.fields
                        ],
                        options=self.options,
                        bases=self.bases,
                        managers=self.managers,
                    ),
                ]
            elif isinstance(operation, RemoveField):
                options = self.options.copy()
                for option_name in ('unique_together', 'index_together'):
                    option = options.pop(option_name, None)
                    if option:
                        option = set(filter(bool, (
                            tuple(f for f in fields if f != operation.name_lower) for fields in option
                        )))
                        if option:
                            options[option_name] = option
                order_with_respect_to = options.get('order_with_respect_to')
                if order_with_respect_to == operation.name_lower:
                    del options['order_with_respect_to']
                return [
                    CreateModel(
                        self.name,
                        fields=[
                            (n, v)
                            for n, v in self.fields
                            if n.lower() != operation.name_lower
                        ],
                        options=options,
                        bases=self.bases,
                        managers=self.managers,
                    ),
                ]
            elif isinstance(operation, RenameField):
                options = self.options.copy()
                for option_name in ('unique_together', 'index_together'):
                    option = options.get(option_name)
                    if option:
                        options[option_name] = {
                            tuple(operation.new_name if f == operation.old_name else f for f in fields)
                            for fields in option
                        }
                order_with_respect_to = options.get('order_with_respect_to')
                if order_with_respect_to == operation.old_name:
                    options['order_with_respect_to'] = operation.new_name
                return [
                    CreateModel(
                        self.name,
                        fields=[
                            (operation.new_name if n == operation.old_name else n, v)
                            for n, v in self.fields
                        ],
                        options=options,
                        bases=self.bases,
                        managers=self.managers,
                    ),
                ]
        return super().reduce(operation, app_label)
class RenameModel(ModelOperation):
    """Rename a model."""

    def references_model(self, name, app_label):
        return (
            name.lower() == self.old_name_lower or
            name.lower() == self.new_name_lower
        )

    def describe(self):
        return "Rename model %s to %s" % (self.old_name, self.new_name)

    @property
    def migration_name_fragment(self):
        return 'rename_%s_%s' % (self.old_name_lower, self.new_name_lower)

    def reduce(self, operation, app_label):
        if (isinstance(operation, RenameModel) and
                self.new_name_lower == operation.old_name_lower):
            return [
                RenameModel(
                    self.old_name,
                    operation.new_name,
                ),
            ]
        # Skip `ModelOperation.reduce` as we want to run `references_model`
        # against self.new_name.
        return (
            super(ModelOperation, self).reduce(operation, app_label) or
            not operation.references_model(self.new_name, app_label)
        )
class ModelOptionOperation(ModelOperation):
    def reduce(self, operation, app_label):
        if isinstance(operation, (self.__class__, DeleteModel)) and self.name_lower == operation.name_lower:
            return [operation]
        return super().reduce(operation, app_label)
