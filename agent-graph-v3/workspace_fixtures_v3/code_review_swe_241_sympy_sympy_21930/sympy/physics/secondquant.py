"""
Second quantization operators and states for bosons.

This follow the formulation of Fetter and Welecka, "Quantum Theory
of Many-Particle Systems."
"""
from collections import defaultdict
from sympy import (Add, Basic, cacheit, Dummy, Expr, Function, I,
                   KroneckerDelta, Mul, Pow, S, sqrt, Symbol, sympify, Tuple,
                   zeros)
from sympy.printing.str import StrPrinter
from sympy.utilities.iterables import has_dups
from sympy.utilities import default_sort_key
class AntiSymmetricTensor(TensorSymbol):
    """Stores upper and lower indices in separate Tuple's.

    Each group of indices is assumed to be antisymmetric.

    Examples
    ========

    >>> from sympy import symbols
    >>> from sympy.physics.secondquant import AntiSymmetricTensor
    >>> i, j = symbols('i j', below_fermi=True)
    >>> a, b = symbols('a b', above_fermi=True)
    >>> AntiSymmetricTensor('v', (a, i), (b, j))
    AntiSymmetricTensor(v, (a, i), (b, j))
    >>> AntiSymmetricTensor('v', (i, a), (b, j))
    -AntiSymmetricTensor(v, (a, i), (b, j))

    As you can see, the indices are automatically sorted to a canonical form.

    """

    def _latex(self, printer):
        return "%s^{%s}_{%s}" % (
            self.symbol,
            "".join([ i.name for i in self.args[1]]),
            "".join([ i.name for i in self.args[2]])
        )

    @property
    def symbol(self):
        """
        Returns the symbol of the tensor.

        Examples
        ========

        >>> from sympy import symbols
        >>> from sympy.physics.secondquant import AntiSymmetricTensor
        >>> i, j = symbols('i,j', below_fermi=True)
        >>> a, b = symbols('a,b', above_fermi=True)
        >>> AntiSymmetricTensor('v', (a, i), (b, j))
        AntiSymmetricTensor(v, (a, i), (b, j))
        >>> AntiSymmetricTensor('v', (a, i), (b, j)).symbol
        v

        """
        return self.args[0]
class AnnihilateBoson(BosonicOperator, Annihilator):
    """
    Bosonic annihilation operator.

    Examples
    ========

    >>> from sympy.physics.secondquant import B
    >>> from sympy.abc import x
    >>> B(x)
    AnnihilateBoson(x)
    """

    def __repr__(self):
        return "AnnihilateBoson(%s)" % self.state

    def _latex(self, printer):
        return "b_{%s}" % self.state.name
class CreateBoson(BosonicOperator, Creator):
    """
    Bosonic creation operator.
    """

    def __repr__(self):
        return "CreateBoson(%s)" % self.state

    def _latex(self, printer):
        return "b^\\dagger_{%s}" % self.state.name
class AnnihilateFermion(FermionicOperator, Annihilator):
    """
    Fermionic annihilation operator.
    """

    def __repr__(self):
        return "AnnihilateFermion(%s)" % self.state

    def _latex(self, printer):
        return "a_{%s}" % self.state.name
class CreateFermion(FermionicOperator, Creator):
    """
    Fermionic creation operator.
    """

    def __repr__(self):
        return "CreateFermion(%s)" % self.state

    def _latex(self, printer):
        return "a^\\dagger_{%s}" % self.state.name
class Commutator(Function):

    def __repr__(self):
        return "Commutator(%s,%s)" % (self.args[0], self.args[1])

    def __str__(self):
        return "[%s,%s]" % (self.args[0], self.args[1])

    def _latex(self, printer):
        return "\\left[%s,%s\\right]" % tuple([
            printer._print(arg) for arg in self.args])
class NO(Expr):

    def get_subNO(self, i):
        """
        Returns a NO() without FermionicOperator at index i.

        Examples
        ========

        >>> from sympy import symbols
        >>> from sympy.physics.secondquant import F, NO
        >>> p, q, r = symbols('p,q,r')

        >>> NO(F(p)*F(q)*F(r)).get_subNO(1)
        NO(AnnihilateFermion(p)*AnnihilateFermion(r))

        """
        arg0 = self.args[0]  # it's a Mul by definition of how it's created
        mul = arg0._new_rawargs(*(arg0.args[:i] + arg0.args[i + 1:]))
        return NO(mul)

    def _latex(self, printer):
        return "\\left\\{%s\\right\\}" % printer._print(self.args[0])

    def __repr__(self):
        return "NO(%s)" % self.args[0]

    def __str__(self):
        return ":%s:" % self.args[0]
class PermutationOperator(Expr):
    """
    Represents the index permutation operator P(ij).

    P(ij)*f(i)*g(j) = f(i)*g(j) - f(j)*g(i)
    """

    def _latex(self, printer):
        return "P(%s%s)" % self.args
