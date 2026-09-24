"""
Default Django settings. Override these with settings in the module pointed to
by the DJANGO_SETTINGS_MODULE environment variable.
"""


# This is defined here as a do-nothing function because we can't import
# django.utils.translation -- that module depends on the settings.
def gettext_noop(s):
    return s


####################
# CORE             #
####################

DEBUG = False

# Whether the framework should propagate raw exceptions rather than catching
# them. This is useful under some testing situations and should never be used
# on a live site.
DEBUG_PROPAGATE_EXCEPTIONS = False

# People who get code error notifications.
# In the format [('Full Name', 'email@example.com'), ('Full Name', 'anotheremail@example.com')]
ADMINS = []

# List of IP addresses, as strings, that:
#   * See debug comments, when DEBUG is true
#   * Receive x-headers
