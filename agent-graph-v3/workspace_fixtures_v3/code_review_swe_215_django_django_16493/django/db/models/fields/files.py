import datetime
import posixpath
from django import forms
from django.core import checks
from django.core.files.base import File
from django.core.files.images import ImageFile
from django.core.files.storage import Storage, default_storage
from django.core.files.utils import validate_file_name
from django.db.models import signals
from django.db.models.fields import Field
from django.db.models.query_utils import DeferredAttribute
from django.db.models.utils import AltersData
from django.utils.translation import gettext_lazy as _
class FileField(Field):

    # The class to wrap instance attributes in. Accessing the file object off
    # the instance will always return an instance of attr_class.

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if kwargs.get("max_length") == 100:
            del kwargs["max_length"]
        kwargs["upload_to"] = self.upload_to
        if self.storage is not default_storage:
            kwargs["storage"] = getattr(self, "_storage_callable", self.storage)
        return name, path, args, kwargs

    def get_internal_type(self):
        return "FileField"

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        # Need to convert File objects provided via a form to string for
        # database insertion.
        if value is None:
            return None
        return str(value)

    def pre_save(self, model_instance, add):
        file = super().pre_save(model_instance, add)
        if file and not file._committed:
            # Commit the file to storage prior to saving the model
            file.save(file.name, file.file, save=False)
        return file

    def contribute_to_class(self, cls, name, **kwargs):
        super().contribute_to_class(cls, name, **kwargs)
        setattr(cls, self.attname, self.descriptor_class(self))

