import functools
import re
from collections import defaultdict
from itertools import chain
from django.conf import settings
from django.db import models
from django.db.migrations import operations
from django.db.migrations.migration import Migration
from django.db.migrations.operations.models import AlterModelOptions
from django.db.migrations.optimizer import MigrationOptimizer
from django.db.migrations.questioner import MigrationQuestioner
from django.db.migrations.utils import (
    COMPILED_REGEX_TYPE,
    RegexObject,
    resolve_relation,
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

    def generate_removed_constraints(self):
        for (
            app_label,
            model_name,
        ), alt_constraints in self.altered_constraints.items():
            for constraint in alt_constraints["removed_constraints"]:
                self.add_operation(
                    app_label,
                    operations.RemoveConstraint(
                        model_name=model_name,
                        name=constraint.name,
                    ),
                )

    @staticmethod
    def _get_dependencies_for_foreign_key(app_label, model_name, field, project_state):
        remote_field_model = None
        if hasattr(field.remote_field, "model"):
            remote_field_model = field.remote_field.model
        else:
            relations = project_state.relations[app_label, model_name]
            for (remote_app_label, remote_model_name), fields in relations.items():
                if any(
                    field == related_field.remote_field
                    for related_field in fields.values()
                ):
                    remote_field_model = f"{remote_app_label}.{remote_model_name}"
                    break
        # Account for FKs to swappable models
        swappable_setting = getattr(field, "swappable_setting", None)
        if swappable_setting is not None:
            dep_app_label = "__setting__"
            dep_object_name = swappable_setting
        else:
            dep_app_label, dep_object_name = resolve_relation(
                remote_field_model,
                app_label,
                model_name,
            )
        dependencies = [(dep_app_label, dep_object_name, None, True)]
        if getattr(field.remote_field, "through", None):
            through_app_label, through_object_name = resolve_relation(
                remote_field_model,
                app_label,
                model_name,
            )
            dependencies.append((through_app_label, through_object_name, None, True))
        return dependencies

