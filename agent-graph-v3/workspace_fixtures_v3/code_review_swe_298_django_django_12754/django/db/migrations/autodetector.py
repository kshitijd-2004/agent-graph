import functools
import re
from itertools import chain
from django.conf import settings
from django.db import models
from django.db.migrations import operations
from django.db.migrations.migration import Migration
from django.db.migrations.operations.models import AlterModelOptions
from django.db.migrations.optimizer import MigrationOptimizer
from django.db.migrations.questioner import MigrationQuestioner
from django.db.migrations.utils import (
    COMPILED_REGEX_TYPE, RegexObject, get_migration_name_timestamp,
)
from django.utils.topological_sort import stable_topological_sort
class MigrationAutodetector:
    """
    Take a pair of ProjectStates and compare them to see what the first would
    need doing to make it match the second (the second usually being the
    project's current state).

    Note that this naturally operates on entire projects at a time,
    as it's likely that changes interact (for example, you can't
    add a ForeignKey without having a migration to add the table it
    depends on first). A user interface may offer single-app usage
    if it wishes, with the caveat that it may not always be possible.
    """

    def generate_created_models(self):
        """
        Find all new models (both managed and unmanaged) and make create
        operations for them as well as separate operations to create any
        foreign key or M2M relationships (these are optimized later, if
        possible).

        Defer any model options that refer to collections of fields that might
        be deferred (e.g. unique_together, index_together).
        """
        old_keys = self.old_model_keys | self.old_unmanaged_keys
        added_models = self.new_model_keys - old_keys
        added_unmanaged_models = self.new_unmanaged_keys - old_keys
        all_added_models = chain(
            sorted(added_models, key=self.swappable_first_key, reverse=True),
            sorted(added_unmanaged_models, key=self.swappable_first_key, reverse=True)
        )
        for app_label, model_name in all_added_models:
            model_state = self.to_state.models[app_label, model_name]
            model_opts = self.new_apps.get_model(app_label, model_name)._meta
            # Gather related fields
            related_fields = {}
            primary_key_rel = None
            for field in model_opts.local_fields:
                if field.remote_field:
                    if field.remote_field.model:
                        if field.primary_key:
                            primary_key_rel = field.remote_field.model
                        elif not field.remote_field.parent_link:
                            related_fields[field.name] = field
                    # through will be none on M2Ms on swapped-out models;
                    # we can treat lack of through as auto_created=True, though.
                    if (getattr(field.remote_field, "through", None) and
                            not field.remote_field.through._meta.auto_created):
                        related_fields[field.name] = field
            for field in model_opts.local_many_to_many:
                if field.remote_field.model:
                    related_fields[field.name] = field
                if getattr(field.remote_field, "through", None) and not field.remote_field.through._meta.auto_created:
                    related_fields[field.name] = field
            # Are there indexes/unique|index_together to defer?
            indexes = model_state.options.pop('indexes')
            constraints = model_state.options.pop('constraints')
            unique_together = model_state.options.pop('unique_together', None)
            index_together = model_state.options.pop('index_together', None)
            order_with_respect_to = model_state.options.pop('order_with_respect_to', None)
            # Depend on the deletion of any possible proxy version of us
            dependencies = [
                (app_label, model_name, None, False),
            ]
            # Depend on all bases
            for base in model_state.bases:
                if isinstance(base, str) and "." in base:
                    base_app_label, base_name = base.split(".", 1)
                    dependencies.append((base_app_label, base_name, None, True))
            # Depend on the other end of the primary key if it's a relation
            if primary_key_rel:
                dependencies.append((
                    primary_key_rel._meta.app_label,
                    primary_key_rel._meta.object_name,
                    None,
                    True
                ))
            # Generate creation operation
            self.add_operation(
                app_label,
                operations.CreateModel(
                    name=model_state.name,
                    fields=[d for d in model_state.fields.items() if d[0] not in related_fields],
                    options=model_state.options,
                    bases=model_state.bases,
                    managers=model_state.managers,
                ),
                dependencies=dependencies,
                beginning=True,
            )

            # Don't add operations which modify the database for unmanaged models
            if not model_opts.managed:
                continue

            # Generate operations for each related field
            for name, field in sorted(related_fields.items()):
                dependencies = self._get_dependencies_for_foreign_key(field)
                # Depend on our own model being created
                dependencies.append((app_label, model_name, None, True))
                # Make operation
                self.add_operation(
                    app_label,
                    operations.AddField(
                        model_name=model_name,
                        name=name,
                        field=field,
                    ),
                    dependencies=list(set(dependencies)),
                )
            # Generate other opns
            related_dependencies = [
                (app_label, model_name, name, True)
                for name in sorted(related_fields)
            ]
            related_dependencies.append((app_label, model_name, None, True))
            for index in indexes:
                self.add_operation(
                    app_label,
                    operations.AddIndex(
                        model_name=model_name,
                        index=index,
                    ),
                    dependencies=related_dependencies,
                )
            for constraint in constraints:
                self.add_operation(
                    app_label,
                    operations.AddConstraint(
                        model_name=model_name,
                        constraint=constraint,
                    ),
                    dependencies=related_dependencies,
                )
            if unique_together:
                self.add_operation(
                    app_label,
                    operations.AlterUniqueTogether(
                        name=model_name,
                        unique_together=unique_together,
                    ),
                    dependencies=related_dependencies
                )
            if index_together:
                self.add_operation(
                    app_label,
                    operations.AlterIndexTogether(
                        name=model_name,
                        index_together=index_together,
                    ),
                    dependencies=related_dependencies
                )
            if order_with_respect_to:
                self.add_operation(
                    app_label,
                    operations.AlterOrderWithRespectTo(
                        name=model_name,
                        order_with_respect_to=order_with_respect_to,
                    ),
                    dependencies=[
                        (app_label, model_name, order_with_respect_to, True),
                        (app_label, model_name, None, True),
                    ]
                )

            # Fix relationships if the model changed from a proxy model to a
            # concrete model.
            if (app_label, model_name) in self.old_proxy_keys:
                for related_object in model_opts.related_objects:
                    self.add_operation(
                        related_object.related_model._meta.app_label,
                        operations.AlterField(
                            model_name=related_object.related_model._meta.object_name,
                            name=related_object.field.name,
                            field=related_object.field,
                        ),
                        dependencies=[(app_label, model_name, None, True)],
                    )

