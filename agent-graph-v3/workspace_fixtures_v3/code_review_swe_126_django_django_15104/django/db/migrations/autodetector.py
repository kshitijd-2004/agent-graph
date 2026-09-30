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
    COMPILED_REGEX_TYPE, RegexObject, resolve_relation,
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

    def only_relation_agnostic_fields(self, fields):
        """
        Return a definition of the fields that ignores field names and
        what related fields actually relate to. Used for detecting renames (as
        the related fields change during renames).
        """
        fields_def = []
        for name, field in sorted(fields.items()):
            deconstruction = self.deep_deconstruct(field)
            if field.remote_field and field.remote_field.model:
                del deconstruction[2]['to']
            fields_def.append(deconstruction)
        return fields_def

