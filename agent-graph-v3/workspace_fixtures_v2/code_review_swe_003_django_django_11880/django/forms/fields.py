"""
Field classes.
"""
import copy
import datetime
import math
import operator
import os
import re
import uuid
from decimal import Decimal, DecimalException
from io import BytesIO
from urllib.parse import urlsplit, urlunsplit
from django.core import validators
from django.core.exceptions import ValidationError
from django.forms.boundfield import BoundField
from django.forms.utils import from_current_timezone, to_current_timezone
from django.forms.widgets import (
    FILE_INPUT_CONTRADICTION, CheckboxInput, ClearableFileInput, DateInput,
    DateTimeInput, EmailInput, FileInput, HiddenInput, MultipleHiddenInput,
    NullBooleanSelect, NumberInput, Select, SelectMultiple,
    SplitDateTimeWidget, SplitHiddenDateTimeWidget, TextInput, TimeInput,
    URLInput,
)
from django.utils import formats
from django.utils.dateparse import parse_duration
from django.utils.duration import duration_string
from django.utils.ipv6 import clean_ipv6_address
from django.utils.translation import gettext_lazy as _, ngettext_lazy
class Field:

    def get_bound_field(self, form, field_name):
        """
        Return a BoundField instance that will be used when accessing the form
        field in a template.
        """
        return BoundField(form, self, field_name)

    def __deepcopy__(self, memo):
        result = copy.copy(self)
        memo[id(self)] = result
        result.widget = copy.deepcopy(self.widget, memo)
        result.validators = self.validators[:]
        return result
class ChoiceField(Field):
    widget = Select
    default_error_messages = {
        'invalid_choice': _('Select a valid choice. %(value)s is not one of the available choices.'),
    }

    def __init__(self, *, choices=(), **kwargs):
        super().__init__(**kwargs)
        self.choices = choices

    def __deepcopy__(self, memo):
        result = super().__deepcopy__(memo)
        result._choices = copy.deepcopy(self._choices, memo)
        return result

    def _get_choices(self):
        return self._choices

    def _set_choices(self, value):
        # Setting choices also sets the choices on the widget.
        # choices can be any iterable, but we call list() on it because
        # it will be consumed more than once.
        if callable(value):
            value = CallableChoiceIterator(value)
        else:
            value = list(value)

        self._choices = self.widget.choices = value

    choices = property(_get_choices, _set_choices)

class MultiValueField(Field):
    """
    Aggregate the logic of multiple Fields.

    Its clean() method takes a "decompressed" list of values, which are then
    cleaned into a single value according to self.fields. Each value in
    this list is cleaned by the corresponding field -- the first value is
    cleaned by the first field, the second value is cleaned by the second
    field, etc. Once all fields are cleaned, the list of clean values is
    "compressed" into a single value.

    Subclasses should not have to implement clean(). Instead, they must
    implement compress(), which takes a list of valid values and returns a
    "compressed" version of those values -- a single value.

    You'll probably want to use this with MultiWidget.
    """
    default_error_messages = {
        'invalid': _('Enter a list of values.'),
        'incomplete': _('Enter a complete value.'),
    }

    def __init__(self, fields, *, require_all_fields=True, **kwargs):
        self.require_all_fields = require_all_fields
        super().__init__(**kwargs)
        for f in fields:
            f.error_messages.setdefault('incomplete',
                                        self.error_messages['incomplete'])
            if self.disabled:
                f.disabled = True
            if self.require_all_fields:
                # Set 'required' to False on the individual fields, because the
                # required validation will be handled by MultiValueField, not
                # by those individual fields.
                f.required = False
        self.fields = fields

    def __deepcopy__(self, memo):
        result = super().__deepcopy__(memo)
        result.fields = tuple(x.__deepcopy__(memo) for x in self.fields)
        return result

    def validate(self, value):
        pass

