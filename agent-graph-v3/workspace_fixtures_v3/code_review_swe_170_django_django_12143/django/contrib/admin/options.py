import copy
import json
import operator
import re
from functools import partial, reduce, update_wrapper
from urllib.parse import quote as urlquote
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin import helpers, widgets
from django.contrib.admin.checks import (
    BaseModelAdminChecks, InlineModelAdminChecks, ModelAdminChecks,
)
from django.contrib.admin.exceptions import DisallowedModelAdminToField
from django.contrib.admin.templatetags.admin_urls import add_preserved_filters
from django.contrib.admin.utils import (
    NestedObjects, construct_change_message, flatten_fieldsets,
    get_deleted_objects, lookup_needs_distinct, model_format_dict,
    model_ngettext, quote, unquote,
)
from django.contrib.admin.views.autocomplete import AutocompleteJsonView
from django.contrib.admin.widgets import (
    AutocompleteSelect, AutocompleteSelectMultiple,
)
from django.contrib.auth import get_permission_codename
from django.core.exceptions import (
    FieldDoesNotExist, FieldError, PermissionDenied, ValidationError,
)
from django.core.paginator import Paginator
from django.db import models, router, transaction
from django.db.models.constants import LOOKUP_SEP
from django.db.models.fields import BLANK_CHOICE_DASH
from django.forms.formsets import DELETION_FIELD_NAME, all_valid
from django.forms.models import (
    BaseInlineFormSet, inlineformset_factory, modelform_defines_fields,
    modelform_factory, modelformset_factory,
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
from django.utils.text import capfirst, format_lazy, get_text_list
from django.utils.translation import gettext as _, ngettext
from django.views.decorators.csrf import csrf_protect
from django.views.generic import RedirectView
class ModelAdmin(BaseModelAdmin):
    """Encapsulate all admin options and functionality for a given model."""

    def autocomplete_view(self, request):
        return AutocompleteJsonView.as_view(model_admin=self)(request)

    def add_view(self, request, form_url='', extra_context=None):
        return self.changeform_view(request, None, form_url, extra_context)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        return self.changeform_view(request, object_id, form_url, extra_context)

    def _get_edited_object_pks(self, request, prefix):
        """Return POST data values of list_editable primary keys."""
        pk_pattern = re.compile(r'{}-\d+-{}$'.format(prefix, self.model._meta.pk.name))
        return [value for key, value in request.POST.items() if pk_pattern.match(key)]

    def _get_list_editable_queryset(self, request, prefix):
        """
        Based on POST data, return a queryset of the objects that were edited
        via list_editable.
        """
        object_pks = self._get_edited_object_pks(request, prefix)
        queryset = self.get_queryset(request)
        validate = queryset.model._meta.pk.to_python
        try:
            for pk in object_pks:
                validate(pk)
        except ValidationError:
            # Disable the optimization if the POST data was tampered with.
            return queryset
        return queryset.filter(pk__in=object_pks)

