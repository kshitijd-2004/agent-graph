"""
    sphinx.ext.autodoc
    ~~~~~~~~~~~~~~~~~~

    Automatically insert docstrings for functions, classes or whole modules into
    the doctree, thus avoiding duplication between docstrings and documentation
    for those who like elaborate docstrings.

    :copyright: Copyright 2007-2020 by the Sphinx team, see AUTHORS.
    :license: BSD, see LICENSE for details.
"""
import re
import warnings
from inspect import Parameter, Signature
from types import ModuleType
from typing import (Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple, Type,
                    TypeVar, Union)
from docutils.statemachine import StringList
import sphinx
from sphinx.application import Sphinx
from sphinx.config import ENUM, Config
from sphinx.deprecation import (RemovedInSphinx40Warning, RemovedInSphinx50Warning,
                                RemovedInSphinx60Warning)
from sphinx.environment import BuildEnvironment
from sphinx.ext.autodoc.importer import (ClassAttribute, get_class_members, get_object_members,
                                         import_module, import_object)
from sphinx.ext.autodoc.mock import mock
from sphinx.locale import _, __
from sphinx.pycode import ModuleAnalyzer, PycodeError
from sphinx.util import inspect, logging
from sphinx.util.docstrings import extract_metadata, prepare_docstring
from sphinx.util.inspect import (evaluate_signature, getdoc, object_description, safe_getattr,
                                 stringify_signature)
from sphinx.util.typing import get_type_hints, restify
from sphinx.util.typing import stringify as stringify_typehint
if False:
    # For type annotation
    from typing import Type  # NOQA # for python3.5.1

    from sphinx.ext.autodoc.directive import DocumenterBridge
class ModuleDocumenter(Documenter):
    """
    Specialized Documenter subclass for modules.
    """

    def add_directive_header(self, sig: str) -> None:
        Documenter.add_directive_header(self, sig)

        sourcename = self.get_sourcename()

        # add some module-specific options
        if self.options.synopsis:
            self.add_line('   :synopsis: ' + self.options.synopsis, sourcename)
        if self.options.platform:
            self.add_line('   :platform: ' + self.options.platform, sourcename)
        if self.options.deprecated:
            self.add_line('   :deprecated:', sourcename)

    def get_module_members(self) -> Dict[str, ObjectMember]:
        """Get members of target module."""
        if self.analyzer:
            attr_docs = self.analyzer.attr_docs
        else:
            attr_docs = {}

        members = {}  # type: Dict[str, ObjectMember]
        for name in dir(self.object):
            try:
                value = safe_getattr(self.object, name, None)
                docstring = attr_docs.get(('', name), [])
                members[name] = ObjectMember(name, value, docstring="\n".join(docstring))
            except AttributeError:
                continue

        # annotation only member (ex. attr: int)
        try:
            for name in inspect.getannotations(self.object):
                if name not in members:
                    docstring = attr_docs.get(('', name), [])
                    members[name] = ObjectMember(name, INSTANCEATTR,
                                                 docstring="\n".join(docstring))
        except AttributeError:
            pass

        return members

from sphinx.ext.autodoc.deprecated import DataDeclarationDocumenter  # NOQA
from sphinx.ext.autodoc.deprecated import GenericAliasDocumenter  # NOQA
from sphinx.ext.autodoc.deprecated import InstanceAttributeDocumenter  # NOQA
from sphinx.ext.autodoc.deprecated import SingledispatchFunctionDocumenter  # NOQA
from sphinx.ext.autodoc.deprecated import SingledispatchMethodDocumenter  # NOQA
from sphinx.ext.autodoc.deprecated import SlotsAttributeDocumenter  # NOQA
from sphinx.ext.autodoc.deprecated import TypeVarDocumenter  # NOQA
