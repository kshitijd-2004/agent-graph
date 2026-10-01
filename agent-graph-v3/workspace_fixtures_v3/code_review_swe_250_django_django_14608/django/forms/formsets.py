from django.core.exceptions import ValidationError
from django.forms import Form
from django.forms.fields import BooleanField, IntegerField
from django.forms.utils import ErrorList
from django.forms.widgets import HiddenInput, NumberInput
from django.utils.functional import cached_property
from django.utils.html import html_safe
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _, ngettext
@html_safe
class BaseFormSet:
    """
    A collection of instances of the same Form class.
    """

    def _should_delete_form(self, form):
        """Return whether or not the form was marked for deletion."""
        return form.cleaned_data.get(DELETION_FIELD_NAME, False)

    def is_valid(self):
        """Return True if every form in self.forms is valid."""
        if not self.is_bound:
            return False
        # Accessing errors triggers a full clean the first time only.
        self.errors
        # List comprehension ensures is_valid() is called for all forms.
        # Forms due to be deleted shouldn't cause the formset to be invalid.
        forms_valid = all([
            form.is_valid() for form in self.forms
            if not (self.can_delete and self._should_delete_form(form))
        ])
        return forms_valid and not self.non_form_errors()

    def full_clean(self):
        """
        Clean all of self.data and populate self._errors and
        self._non_form_errors.
        """
        self._errors = []
        self._non_form_errors = self.error_class()
        empty_forms_count = 0

        if not self.is_bound:  # Stop further processing.
            return

        if not self.management_form.is_valid():
            error = ValidationError(
                self.error_messages['missing_management_form'],
                params={
                    'field_names': ', '.join(
                        self.management_form.add_prefix(field_name)
                        for field_name in self.management_form.errors
                    ),
                },
                code='missing_management_form',
            )
            self._non_form_errors.append(error)

        for i, form in enumerate(self.forms):
            # Empty forms are unchanged forms beyond those with initial data.
            if not form.has_changed() and i >= self.initial_form_count():
                empty_forms_count += 1
            # Accessing errors calls full_clean() if necessary.
            # _should_delete_form() requires cleaned_data.
            form_errors = form.errors
            if self.can_delete and self._should_delete_form(form):
                continue
            self._errors.append(form_errors)
        try:
            if (self.validate_max and
                    self.total_form_count() - len(self.deleted_forms) > self.max_num) or \
                    self.management_form.cleaned_data[TOTAL_FORM_COUNT] > self.absolute_max:
                raise ValidationError(ngettext(
                    "Please submit at most %d form.",
                    "Please submit at most %d forms.", self.max_num) % self.max_num,
                    code='too_many_forms',
                )
            if (self.validate_min and
                    self.total_form_count() - len(self.deleted_forms) - empty_forms_count < self.min_num):
                raise ValidationError(ngettext(
                    "Please submit at least %d form.",
                    "Please submit at least %d forms.", self.min_num) % self.min_num,
                    code='too_few_forms')
            # Give self.clean() a chance to do cross-form validation.
            self.clean()
        except ValidationError as e:
            self._non_form_errors = self.error_class(e.error_list)

    def clean(self):
        """
        Hook for doing any extra formset-wide cleaning after Form.clean() has
        been called on every form. Any ValidationError raised by this method
        will not be associated with a particular form; it will be accessible
        via formset.non_form_errors()
        """
        pass

    def has_changed(self):
        """Return True if data in any form differs from initial."""
        return any(form.has_changed() for form in self)

