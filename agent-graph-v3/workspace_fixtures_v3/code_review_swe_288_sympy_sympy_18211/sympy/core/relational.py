from __future__ import print_function, division
from sympy.utilities.exceptions import SymPyDeprecationWarning
from .add import _unevaluated_Add, Add
from .basic import S
from .compatibility import ordered
from .basic import Basic
from .expr import Expr
from .evalf import EvalfMixin
from .sympify import _sympify
from .parameters import global_parameters
from sympy.logic.boolalg import Boolean, BooleanAtom
class Relational(Boolean, EvalfMixin):

    def _eval_trigsimp(self, **opts):
        from sympy.simplify import trigsimp
        return self.func(trigsimp(self.lhs, **opts), trigsimp(self.rhs, **opts))

    def expand(self, **kwargs):
        args = (arg.expand(**kwargs) for arg in self.args)
        return self.func(*args)

    def __nonzero__(self):
        raise TypeError("cannot determine truth value of Relational")

    __bool__ = __nonzero__

    def _eval_as_set(self):
        # self is univariate and periodicity(self, x) in (0, None)
        from sympy.solvers.inequalities import solve_univariate_inequality
        syms = self.free_symbols
        assert len(syms) == 1
        x = syms.pop()
        return solve_univariate_inequality(self, x, relational=False)

    @property
    def binary_symbols(self):
        # override where necessary
        return set()
