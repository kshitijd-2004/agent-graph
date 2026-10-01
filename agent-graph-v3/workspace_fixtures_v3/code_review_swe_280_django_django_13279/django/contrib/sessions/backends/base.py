import base64
import logging
import string
import warnings
from datetime import datetime, timedelta
from django.conf import settings
from django.contrib.sessions.exceptions import SuspiciousSession
from django.core import signing
from django.core.exceptions import SuspiciousOperation
from django.utils import timezone
from django.utils.crypto import (
    constant_time_compare, get_random_string, salted_hmac,
)
from django.utils.deprecation import RemovedInDjango40Warning
from django.utils.module_loading import import_string
from django.utils.translation import LANGUAGE_SESSION_KEY
class SessionBase:
    """
    Base class for all Session classes.
    """

    def set_test_cookie(self):
        self[self.TEST_COOKIE_NAME] = self.TEST_COOKIE_VALUE

    def test_cookie_worked(self):
        return self.get(self.TEST_COOKIE_NAME) == self.TEST_COOKIE_VALUE

    def delete_test_cookie(self):
        del self[self.TEST_COOKIE_NAME]

    def _hash(self, value):
        # RemovedInDjango40Warning: pre-Django 3.1 format will be invalid.
        key_salt = "django.contrib.sessions" + self.__class__.__name__
        return salted_hmac(key_salt, value).hexdigest()

    def encode(self, session_dict):
        "Return the given session dictionary serialized and encoded as a string."
        return signing.dumps(
            session_dict, salt=self.key_salt, serializer=self.serializer,
            compress=True,
        )

    def decode(self, session_data):
        try:
            return signing.loads(session_data, salt=self.key_salt, serializer=self.serializer)
        # RemovedInDjango40Warning: when the deprecation ends, handle here
        # exceptions similar to what _legacy_decode() does now.
        except Exception:
            return self._legacy_decode(session_data)

    def _legacy_decode(self, session_data):
        # RemovedInDjango40Warning: pre-Django 3.1 format will be invalid.
        encoded_data = base64.b64decode(session_data.encode('ascii'))
        try:
            # could produce ValueError if there is no ':'
            hash, serialized = encoded_data.split(b':', 1)
            expected_hash = self._hash(serialized)
            if not constant_time_compare(hash.decode(), expected_hash):
                raise SuspiciousSession("Session data corrupted")
            else:
                return self.serializer().loads(serialized)
        except Exception as e:
            # ValueError, SuspiciousOperation, unpickling exceptions. If any of
            # these happen, just return an empty dictionary (an empty session).
            if isinstance(e, SuspiciousOperation):
                logger = logging.getLogger('django.security.%s' % e.__class__.__name__)
                logger.warning(str(e))
            return {}

    def update(self, dict_):
        self._session.update(dict_)
        self.modified = True

    def has_key(self, key):
        return key in self._session

    def keys(self):
        return self._session.keys()

    def values(self):
        return self._session.values()

    def items(self):
        return self._session.items()

