"""
XML serializer.
"""
import json
from xml.dom import pulldom
from xml.sax import handler
from xml.sax.expatreader import ExpatParser as _ExpatParser
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.serializers import base
from django.db import DEFAULT_DB_ALIAS, models
from django.utils.xmlutils import SimplerXMLGenerator, UnserializableContentError
class Serializer(base.Serializer):
    """Serialize a QuerySet to XML."""

    def handle_m2m_field(self, obj, field):
        """
        Handle a ManyToManyField. Related objects are only serialized as
        references to the object's PK (i.e. the related *data* is not dumped,
        just the relation).
        """
        if field.remote_field.through._meta.auto_created:
            self._start_relational_field(field)
            if self.use_natural_foreign_keys and hasattr(
                field.remote_field.model, "natural_key"
            ):
                # If the objects in the m2m have a natural key, use it
                def handle_m2m(value):
                    natural = value.natural_key()
                    # Iterable natural keys are rolled out as subelements
                    self.xml.startElement("object", {})
                    for key_value in natural:
                        self.xml.startElement("natural", {})
                        self.xml.characters(str(key_value))
                        self.xml.endElement("natural")
                    self.xml.endElement("object")

                def queryset_iterator(obj, field):
                    return getattr(obj, field.name).iterator()

            else:

                def handle_m2m(value):
                    self.xml.addQuickElement("object", attrs={"pk": str(value.pk)})

                def queryset_iterator(obj, field):
                    return getattr(obj, field.name).only("pk").iterator()

            m2m_iter = getattr(obj, "_prefetched_objects_cache", {}).get(
                field.name,
                queryset_iterator(obj, field),
            )
            for relobj in m2m_iter:
                handle_m2m(relobj)

            self.xml.endElement("field")

    def _start_relational_field(self, field):
        """Output the <field> element for relational fields."""
        self.indent(2)
        self.xml.startElement(
            "field",
            {
                "name": field.name,
                "rel": field.remote_field.__class__.__name__,
                "to": str(field.remote_field.model._meta),
            },
        )
