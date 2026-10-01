import copy
import json
import re
from functools import partial, update_wrapper
from urllib.parse import quote as urlquote
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin import helpers, widgets
from django.contrib.admin.checks import (
    BaseModelAdminChecks,
    InlineModelAdminChecks,
    ModelAdminChecks,
)
from django.contrib.admin.decorators import display
from django.contrib.admin.exceptions import DisallowedModelAdminToField
from django.contrib.admin.templatetags.admin_urls import add_preserved_filters
from django.contrib.admin.utils import (
    NestedObjects,
    construct_change_message,
    flatten_fieldsets,
    get_deleted_objects,
    lookup_spawns_duplicates,
    model_format_dict,
    model_ngettext,
    quote,
    unquote,
)
from django.contrib.admin.widgets import AutocompleteSelect, AutocompleteSelectMultiple
from django.contrib.auth import get_permission_codename
from django.core.exceptions import (
    FieldDoesNotExist,
    FieldError,
    PermissionDenied,
    ValidationError,
)
from django.core.paginator import Paginator
from django.db import models, router, transaction
from django.db.models.constants import LOOKUP_SEP
from django.forms.formsets import DELETION_FIELD_NAME, all_valid
from django.forms.models import (
    BaseInlineFormSet,
    inlineformset_factory,
    modelform_defines_fields,
    modelform_factory,
    modelformset_factory,
)
from django.forms.widgets import CheckboxSelectMultiple, SelectMultiple
from django.http import HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.template.response import SimpleTemplateResponse, TemplateResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.html import format_html
from django.utils.http import urlencode
from django.utils.safestring import mark_safe
from django.utils.text import (
    capfirst,
    format_lazy,
    get_text_list,
    smart_split,
    unescape_string_literal,
)
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.csrf import csrf_protect
from django.views.generic import RedirectView
class BaseModelAdmin(metaclass=forms.MediaDefiningClass):
    """Functionality common to both ModelAdmin and InlineAdmin."""

    def get_field_queryset(self, db, db_field, request):
        """
        If the ModelAdmin specifies ordering, the queryset should respect that
        ordering.  Otherwise don't specify the queryset, let the field decide
        (return None in that case).
        """
        related_admin = self.admin_site._registry.get(db_field.remote_field.model)
        if related_admin is not None:
            ordering = related_admin.get_ordering(request)
            if ordering is not None and ordering != ():
                return db_field.remote_field.model._default_manager.using(db).order_by(
                    *ordering
                )
        return None

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Get a form Field for a ForeignKey.
        """
        db = kwargs.get("using")

        if "widget" not in kwargs:
            if db_field.name in self.get_autocomplete_fields(request):
                kwargs["widget"] = AutocompleteSelect(
                    db_field, self.admin_site, using=db
                )
            elif db_field.name in self.raw_id_fields:
                kwargs["widget"] = widgets.ForeignKeyRawIdWidget(
                    db_field.remote_field, self.admin_site, using=db
                )
            elif db_field.name in self.radio_fields:
                kwargs["widget"] = widgets.AdminRadioSelect(
                    attrs={
                        "class": get_ul_class(self.radio_fields[db_field.name]),
                    }
                )
                kwargs["empty_label"] = _("None") if db_field.blank else None

        if "queryset" not in kwargs:
            queryset = self.get_field_queryset(db, db_field, request)
            if queryset is not None:
                kwargs["queryset"] = queryset

        return db_field.formfield(**kwargs)

