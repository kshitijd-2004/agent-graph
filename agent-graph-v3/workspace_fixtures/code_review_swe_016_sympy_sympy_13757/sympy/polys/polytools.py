"""User-friendly public interface to polynomial functions. """
from __future__ import print_function, division
from sympy.core import (
    S, Basic, Expr, I, Integer, Add, Mul, Dummy, Tuple
)
from sympy.core.mul import _keep_coeff
from sympy.core.symbol import Symbol
from sympy.core.basic import preorder_traversal
from sympy.core.relational import Relational
from sympy.core.sympify import sympify
from sympy.core.decorators import _sympifyit
from sympy.core.function import Derivative
from sympy.logic.boolalg import BooleanAtom
from sympy.polys.polyclasses import DMP
from sympy.polys.polyutils import (
    basic_from_dict,
    _sort_gens,
    _unify_gens,
    _dict_reorder,
    _dict_from_expr,
    _parallel_dict_from_expr,
)
from sympy.polys.rationaltools import together
from sympy.polys.rootisolation import dup_isolate_real_roots_list
from sympy.polys.groebnertools import groebner as _groebner
from sympy.polys.fglmtools import matrix_fglm
from sympy.polys.monomials import Monomial
from sympy.polys.orderings import monomial_key
from sympy.polys.polyerrors import (
    OperationNotSupported, DomainError,
    CoercionFailed, UnificationFailed,
    GeneratorsNeeded, PolynomialError,
    MultivariatePolynomialError,
    ExactQuotientFailed,
    PolificationFailed,
    ComputationFailed,
    GeneratorsError,
)
from sympy.utilities import group, sift, public, filldedent
import sympy.polys
import mpmath
from mpmath.libmp.libhyper import NoConvergence
from sympy.polys.domains import FF, QQ, ZZ
from sympy.polys.constructor import construct_domain
from sympy.polys import polyoptions as options
from sympy.core.compatibility import iterable, range, ordered
@public
class Poly(Expr):

    __slots__ = ['rep', 'gens']

    is_commutative = True
    is_Poly = True

    def __new__(cls, rep, *gens, **args):
        """Create a new polynomial instance out of something useful. """
        opt = options.build_options(gens, args)

        if 'order' in opt:
            raise NotImplementedError("'order' keyword is not implemented yet")

        if iterable(rep, exclude=str):
            if isinstance(rep, dict):
                return cls._from_dict(rep, opt)
            else:
                return cls._from_list(list(rep), opt)
        else:
            rep = sympify(rep)

            if rep.is_Poly:
                return cls._from_poly(rep, opt)
            else:
                return cls._from_expr(rep, opt)

    @classmethod
    def new(cls, rep, *gens):
        """Construct :class:`Poly` instance from raw representation. """
        if not isinstance(rep, DMP):
            raise PolynomialError(
                "invalid polynomial representation: %s" % rep)
        elif rep.lev != len(gens) - 1:
            raise PolynomialError("invalid arguments: %s, %s" % (rep, gens))

        obj = Basic.__new__(cls)

        obj.rep = rep
        obj.gens = gens

        return obj

