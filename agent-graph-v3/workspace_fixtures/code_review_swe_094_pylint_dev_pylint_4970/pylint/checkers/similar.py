"""a similarities / code duplication command line tool and pylint checker

The algorithm is based on comparing the hash value of n successive lines of a file.
First the files are read and any line that doesn't fullfill requirement are removed (comments, docstrings...)
Those stripped lines are stored in the LineSet class which gives access to them.
Then each index of the stripped lines collection is associated with the hash of n successive entries of the stripped lines starting at the current index
(n is the minimum common lines option).
The common hashes between both linesets are then looked for. If there are matches, then the match indices in both linesets are stored and associated
with the corresponding couples (start line number/end line number) in both files.
This association is then postprocessed to handle the case of successive matches. For example if the minimum common lines setting is set to four, then
the hashes are computed with four lines. If one of match indices couple (12, 34) is the successor of another one (11, 33) then it means that there are
in fact five lines wich are common.
Once postprocessed the values of association table are the result looked for, i.e start and end lines numbers of common lines in both files.
"""
import copy
import functools
import itertools
import operator
import re
import sys
from collections import defaultdict
from getopt import getopt
from itertools import chain, groupby
from typing import (
    Any,
    Dict,
    FrozenSet,
    Generator,
    Iterable,
    List,
    NamedTuple,
    NewType,
    Set,
    TextIO,
    Tuple,
)
import astroid
from astroid import nodes
from pylint.checkers import BaseChecker, MapReduceMixin, table_lines_from_stats
from pylint.interfaces import IRawChecker
from pylint.reporters.ureports.nodes import Table
from pylint.utils import decoding_stream
class Similar:
    """finds copy-pasted lines of code in a project"""

    def __init__(
        self,
        min_lines: int = DEFAULT_MIN_SIMILARITY_LINE,
        ignore_comments: bool = False,
        ignore_docstrings: bool = False,
        ignore_imports: bool = False,
        ignore_signatures: bool = False,
    ) -> None:
        self.min_lines = min_lines
        self.ignore_comments = ignore_comments
        self.ignore_docstrings = ignore_docstrings
        self.ignore_imports = ignore_imports
        self.ignore_signatures = ignore_signatures
        self.linesets: List["LineSet"] = []

    def append_stream(self, streamid: str, stream: TextIO, encoding=None) -> None:
        """append a file to search for similarities"""
        if encoding is None:
            readlines = stream.readlines
        else:
            readlines = decoding_stream(stream, encoding).readlines
        try:
            self.linesets.append(
                LineSet(
                    streamid,
                    readlines(),
                    self.ignore_comments,
                    self.ignore_docstrings,
                    self.ignore_imports,
                    self.ignore_signatures,
                )
            )
        except UnicodeDecodeError:
            pass

    def run(self) -> None:
        """start looking for similarities and display results on stdout"""
        self._display_sims(self._compute_sims())

