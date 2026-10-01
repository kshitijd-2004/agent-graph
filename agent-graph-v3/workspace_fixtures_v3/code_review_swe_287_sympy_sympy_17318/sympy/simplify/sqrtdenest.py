from __future__ import print_function, division
from sympy.core import S, sympify, Mul, Add, Expr
from sympy.core.compatibility import range
from sympy.core.function import expand_mul, count_ops, _mexpand
from sympy.core.symbol import Dummy
from sympy.functions import sqrt, sign, root
from sympy.polys import Poly, PolynomialError
from sympy.utilities import default_sort_key


def _sqrt_match(p):
    """Return [a, b, r] for p.match(a + b*sqrt(r)) where, in addition to
    matching, sqrt(r) also has then maximal sqrt_depth among addends of p.

    Examples
    ========

    >>> from sympy.functions.elementary.miscellaneous import sqrt
    >>> from sympy.simplify.sqrtdenest import _sqrt_match
    >>> _sqrt_match(1 + sqrt(2) + sqrt(2)*sqrt(3) +  2*sqrt(1+sqrt(5)))
    [1 + sqrt(2) + sqrt(6), 2, 1 + sqrt(5)]
    """
    from sympy.simplify.radsimp import split_surds

    p = _mexpand(p)
    if p.is_Number:
        res = (p, S.Zero, S.Zero)
    elif p.is_Add:
        pargs = sorted(p.args, key=default_sort_key)
        if all((x**2).is_Rational for x in pargs):
            r, b, a = split_surds(p)
            res = a, b, r
            return list(res)
        # to make the process canonical, the argument is included in the tuple
        # so when the max is selected, it will be the largest arg having a
        # given depth
        v = [(sqrt_depth(x), x, i) for i, x in enumerate(pargs)]
        nmax = max(v, key=default_sort_key)
        if nmax[0] == 0:
            res = []
        else:
            # select r
            depth, _, i = nmax
            r = pargs.pop(i)
            v.pop(i)
            b = S.One
            if r.is_Mul:
                bv = []
                rv = []
                for x in r.args:
                    if sqrt_depth(x) < depth:
                        bv.append(x)
                    else:
                        rv.append(x)
                b = Mul._from_args(bv)
                r = Mul._from_args(rv)
            # collect terms comtaining r
            a1 = []
            b1 = [b]
            for x in v:
                if x[0] < depth:
                    a1.append(x[1])
                else:
                    x1 = x[1]
                    if x1 == r:
                        b1.append(1)
                    else:
                        if x1.is_Mul:
                            x1args = list(x1.args)
                            if r in x1args:
                                x1args.remove(r)
                                b1.append(Mul(*x1args))
                            else:
                                a1.append(x[1])
                        else:
                            a1.append(x[1])
            a = Add(*a1)
            b = Add(*b1)
            res = (a, b, r**2)
    else:
        b, r = p.as_coeff_Mul()
        if is_sqrt(r):
            res = (S.Zero, b, r**2)
        else:
            res = []
    return list(res)


class SqrtdenestStopIteration(StopIteration):
    pass


