import json
from django import forms
from django.contrib.admin.utils import (
    display_for_field, flatten_fieldsets, help_text_for_field, label_for_field,
    lookup_field, quote,
)
from django.core.exceptions import ObjectDoesNotExist
from django.db.models.fields.related import (
    ForeignObjectRel, ManyToManyRel, OneToOneField,
)
from django.forms.utils import flatatt
from django.template.defaultfilters import capfirst, linebreaksbr
from django.urls import NoReverseMatch, reverse
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext, gettext_lazy as _
class AdminReadonlyField:

    def label_tag(self):
        attrs = {}
        if not self.is_first:
            attrs["class"] = "inline"
        label = self.field['label']
        return format_html('<label{}>{}{}</label>', flatatt(attrs), capfirst(label), self.form.label_suffix)

    def get_admin_url(self, remote_field, remote_obj):
        url_name = 'admin:%s_%s_change' % (
            remote_field.model._meta.app_label,
            remote_field.model._meta.model_name,
        )
        try:
            url = reverse(url_name, args=[quote(remote_obj.pk)])
            return format_html('<a href="{}">{}</a>', url, remote_obj)
        except NoReverseMatch:
            return str(remote_obj)

