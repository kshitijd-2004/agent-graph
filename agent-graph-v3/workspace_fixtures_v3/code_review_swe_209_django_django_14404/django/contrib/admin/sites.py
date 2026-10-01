import re
from functools import update_wrapper
from weakref import WeakSet
from django.apps import apps
from django.conf import settings
from django.contrib.admin import ModelAdmin, actions
from django.contrib.admin.views.autocomplete import AutocompleteJsonView
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.core.exceptions import ImproperlyConfigured
from django.db.models.base import ModelBase
from django.http import (
    Http404, HttpResponsePermanentRedirect, HttpResponseRedirect,
)
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, Resolver404, resolve, reverse
from django.utils.decorators import method_decorator
from django.utils.functional import LazyObject
from django.utils.module_loading import import_string
from django.utils.text import capfirst
from django.utils.translation import gettext as _, gettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.common import no_append_slash
from django.views.decorators.csrf import csrf_protect
from django.views.i18n import JavaScriptCatalog
class AdminSite:
    """
    An AdminSite object encapsulates an instance of the Django admin application, ready
    to be hooked in to your URLconf. Models are registered with the AdminSite using the
    register() method, and the get_urls() method can then be used to access Django view
    functions that present a full admin interface for the collection of registered
    models.
    """

    def autocomplete_view(self, request):
        return AutocompleteJsonView.as_view(admin_site=self)(request)

    @no_append_slash
    def catch_all_view(self, request, url):
        if settings.APPEND_SLASH and not url.endswith('/'):
            urlconf = getattr(request, 'urlconf', None)
            path = '%s/' % request.path_info
            try:
                match = resolve(path, urlconf)
            except Resolver404:
                pass
            else:
                if getattr(match.func, 'should_append_slash', True):
                    return HttpResponsePermanentRedirect(path)
        raise Http404

