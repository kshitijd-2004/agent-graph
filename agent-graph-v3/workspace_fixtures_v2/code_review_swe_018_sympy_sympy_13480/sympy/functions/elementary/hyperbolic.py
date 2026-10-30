from __future__ import print_function, division
from sympy.core import S, sympify, cacheit
from sympy.core.add import Add
from sympy.core.function import Function, ArgumentIndexError, _coeff_isneg
from sympy.functions.elementary.miscellaneous import sqrt
from sympy.functions.elementary.exponential import exp, log
from sympy.functions.combinatorial.factorials import factorial, RisingFactorial
class sinh(HyperbolicFunction):
    r"""
    The hyperbolic sine function, `\frac{e^x - e^{-x}}{2}`.

    * sinh(x) -> Returns the hyperbolic sine of x

    See Also
    ========

    cosh, tanh, asinh
    """

    def fdiff(self, argindex=1):
        """
        Returns the first derivative of this function.
        """
        if argindex == 1:
            return cosh(self.args[0])
        else:
            raise ArgumentIndexError(self, argindex)

    def inverse(self, argindex=1):
        """
        Returns the inverse of this function.
        """
        return asinh

    @classmethod
    def eval(cls, arg):
        from sympy import sin

        arg = sympify(arg)

        if arg.is_Number:
            if arg is S.NaN:
                return S.NaN
            elif arg is S.Infinity:
                return S.Infinity
            elif arg is S.NegativeInfinity:
                return S.NegativeInfinity
            elif arg is S.Zero:
                return S.Zero
            elif arg.is_negative:
                return -cls(-arg)
        else:
            if arg is S.ComplexInfinity:
                return S.NaN

            i_coeff = arg.as_coefficient(S.ImaginaryUnit)

            if i_coeff is not None:
                return S.ImaginaryUnit * sin(i_coeff)
            else:
                if _coeff_isneg(arg):
                    return -cls(-arg)

            if arg.is_Add:
                x, m = _peeloff_ipi(arg)
                if m:
                    return sinh(m)*cosh(x) + cosh(m)*sinh(x)

            if arg.func == asinh:
                return arg.args[0]

            if arg.func == acosh:
                x = arg.args[0]
                return sqrt(x - 1) * sqrt(x + 1)

            if arg.func == atanh:
                x = arg.args[0]
                return x/sqrt(1 - x**2)

            if arg.func == acoth:
                x = arg.args[0]
                return 1/(sqrt(x - 1) * sqrt(x + 1))

    @staticmethod
    @cacheit
    def taylor_term(n, x, *previous_terms):
        """
        Returns the next term in the Taylor series expansion.
        """
        if n < 0 or n % 2 == 0:
            return S.Zero
        else:
            x = sympify(x)

            if len(previous_terms) > 2:
                p = previous_terms[-2]
                return p * x**2 / (n*(n - 1))
            else:
                return x**(n) / factorial(n)

    def _eval_conjugate(self):
        return self.func(self.args[0].conjugate())
class cosh(HyperbolicFunction):
    r"""
    The hyperbolic cosine function, `\frac{e^x + e^{-x}}{2}`.

    * cosh(x) -> Returns the hyperbolic cosine of x

    See Also
    ========

    sinh, tanh, acosh
    """

    def fdiff(self, argindex=1):
        if argindex == 1:
            return sinh(self.args[0])
        else:
            raise ArgumentIndexError(self, argindex)

    @classmethod
    def eval(cls, arg):
        from sympy import cos
        arg = sympify(arg)

        if arg.is_Number:
            if arg is S.NaN:
                return S.NaN
            elif arg is S.Infinity:
                return S.Infinity
            elif arg is S.NegativeInfinity:
                return S.Infinity
            elif arg is S.Zero:
                return S.One
            elif arg.is_negative:
                return cls(-arg)
        else:
            if arg is S.ComplexInfinity:
                return S.NaN

            i_coeff = arg.as_coefficient(S.ImaginaryUnit)

            if i_coeff is not None:
                return cos(i_coeff)
            else:
                if _coeff_isneg(arg):
                    return cls(-arg)

            if arg.is_Add:
                x, m = _peeloff_ipi(arg)
                if m:
                    return cosh(m)*cosh(x) + sinh(m)*sinh(x)

            if arg.func == asinh:
                return sqrt(1 + arg.args[0]**2)

            if arg.func == acosh:
                return arg.args[0]

            if arg.func == atanh:
                return 1/sqrt(1 - arg.args[0]**2)

            if arg.func == acoth:
                x = arg.args[0]
                return x/(sqrt(x - 1) * sqrt(x + 1))

    @staticmethod
    @cacheit
    def taylor_term(n, x, *previous_terms):
        if n < 0 or n % 2 == 1:
            return S.Zero
        else:
            x = sympify(x)

            if len(previous_terms) > 2:
                p = previous_terms[-2]
                return p * x**2 / (n*(n - 1))
            else:
                return x**(n)/factorial(n)

    def _eval_conjugate(self):
        return self.func(self.args[0].conjugate())

class tanh(HyperbolicFunction):
    r"""
    The hyperbolic tangent function, `\frac{\sinh(x)}{\cosh(x)}`.

    * tanh(x) -> Returns the hyperbolic tangent of x

    See Also
    ========

    sinh, cosh, atanh
    """

    def fdiff(self, argindex=1):
        if argindex == 1:
            return S.One - tanh(self.args[0])**2
        else:
            raise ArgumentIndexError(self, argindex)

    def inverse(self, argindex=1):
        """
        Returns the inverse of this function.
        """
        return atanh

    @classmethod
    def eval(cls, arg):
        from sympy import tan
        arg = sympify(arg)

        if arg.is_Number:
            if arg is S.NaN:
                return S.NaN
            elif arg is S.Infinity:
                return S.One
            elif arg is S.NegativeInfinity:
                return S.NegativeOne
            elif arg is S.Zero:
                return S.Zero
            elif arg.is_negative:
                return -cls(-arg)
        else:
            if arg is S.ComplexInfinity:
                return S.NaN

            i_coeff = arg.as_coefficient(S.ImaginaryUnit)

            if i_coeff is not None:
                if _coeff_isneg(i_coeff):
                    return -S.ImaginaryUnit * tan(-i_coeff)
                return S.ImaginaryUnit * tan(i_coeff)
            else:
                if _coeff_isneg(arg):
                    return -cls(-arg)

            if arg.is_Add:
                x, m = _peeloff_ipi(arg)
                if m:
                    tanhm = tanh(m)
                    if tanhm is S.ComplexInfinity:
                        return coth(x)
                    else: # tanhm == 0
                        return tanh(x)

            if arg.func == asinh:
                x = arg.args[0]
                return x/sqrt(1 + x**2)

            if arg.func == acosh:
                x = arg.args[0]
                return sqrt(x - 1) * sqrt(x + 1) / x

            if arg.func == atanh:
                return arg.args[0]

            if arg.func == acoth:
                return 1/arg.args[0]

    @staticmethod
    @cacheit
    def taylor_term(n, x, *previous_terms):
        from sympy import bernoulli
        if n < 0 or n % 2 == 0:
            return S.Zero
        else:
            x = sympify(x)

            a = 2**(n + 1)

            B = bernoulli(n + 1)
            F = factorial(n + 1)

            return a*(a - 1) * B/F * x**n

    def _eval_conjugate(self):
        return self.func(self.args[0].conjugate())

class coth(HyperbolicFunction):
    r"""
    The hyperbolic cotangent function, `\frac{\cosh(x)}{\sinh(x)}`.

    * coth(x) -> Returns the hyperbolic cotangent of x
    """

    def fdiff(self, argindex=1):
        if argindex == 1:
            return -1/sinh(self.args[0])**2
        else:
            raise ArgumentIndexError(self, argindex)

    def inverse(self, argindex=1):
        """
        Returns the inverse of this function.
        """
        return acoth

    @classmethod
    def eval(cls, arg):
        from sympy import cot
        arg = sympify(arg)

        if arg.is_Number:
            if arg is S.NaN:
                return S.NaN
            elif arg is S.Infinity:
                return S.One
            elif arg is S.NegativeInfinity:
                return S.NegativeOne
            elif arg is S.Zero:
                return S.ComplexInfinity
            elif arg.is_negative:
                return -cls(-arg)
        else:
            if arg is S.ComplexInfinity:
                return S.NaN

            i_coeff = arg.as_coefficient(S.ImaginaryUnit)

            if i_coeff is not None:
                if _coeff_isneg(i_coeff):
                    return S.ImaginaryUnit * cot(-i_coeff)
                return -S.ImaginaryUnit * cot(i_coeff)
            else:
                if _coeff_isneg(arg):
                    return -cls(-arg)

            if arg.is_Add:
                x, m = _peeloff_ipi(arg)
                if m:
                    cothm = coth(m)
                    if cotm is S.ComplexInfinity:
                        return coth(x)
                    else: # cothm == 0
                        return tanh(x)

            if arg.func == asinh:
                x = arg.args[0]
                return sqrt(1 + x**2)/x

            if arg.func == acosh:
                x = arg.args[0]
                return x/(sqrt(x - 1) * sqrt(x + 1))

            if arg.func == atanh:
                return 1/arg.args[0]

            if arg.func == acoth:
                return arg.args[0]

    @staticmethod
    @cacheit
    def taylor_term(n, x, *previous_terms):
        from sympy import bernoulli
        if n == 0:
            return 1 / sympify(x)
        elif n < 0 or n % 2 == 0:
            return S.Zero
        else:
            x = sympify(x)

            B = bernoulli(n + 1)
            F = factorial(n + 1)

            return 2**(n + 1) * B/F * x**n

    def _eval_conjugate(self):
        return self.func(self.args[0].conjugate())

class ReciprocalHyperbolicFunction(HyperbolicFunction):
    """Base class for reciprocal functions of hyperbolic functions. """

    #To be defined in class
    _reciprocal_of = None
    _is_even = None
    _is_odd = None

    @classmethod
    def eval(cls, arg):
        if arg.could_extract_minus_sign():
            if cls._is_even:
                return cls(-arg)
            if cls._is_odd:
                return -cls(-arg)

        t = cls._reciprocal_of.eval(arg)
        if hasattr(arg, 'inverse') and arg.inverse() == cls:
            return arg.args[0]
        return 1/t if t != None else t

    def _call_reciprocal(self, method_name, *args, **kwargs):
        # Calls method_name on _reciprocal_of
        o = self._reciprocal_of(self.args[0])
        return getattr(o, method_name)(*args, **kwargs)

    def _calculate_reciprocal(self, method_name, *args, **kwargs):
        # If calling method_name on _reciprocal_of returns a value != None
        # then return the reciprocal of that value
        t = self._call_reciprocal(method_name, *args, **kwargs)
        return 1/t if t != None else t

    def _rewrite_reciprocal(self, method_name, arg):
        # Special handling for rewrite functions. If reciprocal rewrite returns
        # unmodified expression, then return None
        t = self._call_reciprocal(method_name, arg)
        if t != None and t != self._reciprocal_of(arg):
            return 1/t

class asinh(InverseHyperbolicFunction):
    """
    The inverse hyperbolic sine function.

    * asinh(x) -> Returns the inverse hyperbolic sine of x

    See Also
    ========

    acosh, atanh, sinh
    """

    def fdiff(self, argindex=1):
        if argindex == 1:
            return 1/sqrt(self.args[0]**2 + 1)
        else:
            raise ArgumentIndexError(self, argindex)

    @classmethod
    def eval(cls, arg):
        from sympy import asin
        arg = sympify(arg)

        if arg.is_Number:
            if arg is S.NaN:
                return S.NaN
            elif arg is S.Infinity:
                return S.Infinity
            elif arg is S.NegativeInfinity:
                return S.NegativeInfinity
            elif arg is S.Zero:
                return S.Zero
            elif arg is S.One:
                return log(sqrt(2) + 1)
            elif arg is S.NegativeOne:
                return log(sqrt(2) - 1)
            elif arg.is_negative:
                return -cls(-arg)
        else:
            if arg is S.ComplexInfinity:
                return S.ComplexInfinity

            i_coeff = arg.as_coefficient(S.ImaginaryUnit)

            if i_coeff is not None:
                return S.ImaginaryUnit * asin(i_coeff)
            else:
                if _coeff_isneg(arg):
                    return -cls(-arg)

    @staticmethod
    @cacheit
    def taylor_term(n, x, *previous_terms):
        if n < 0 or n % 2 == 0:
            return S.Zero
        else:
            x = sympify(x)
            if len(previous_terms) >= 2 and n > 2:
                p = previous_terms[-2]
                return -p * (n - 2)**2/(n*(n - 1)) * x**2
            else:
                k = (n - 1) // 2
                R = RisingFactorial(S.Half, k)
                F = factorial(k)
                return (-1)**k * R / F * x**n / n

class acosh(InverseHyperbolicFunction):
    """
    The inverse hyperbolic cosine function.

    * acosh(x) -> Returns the inverse hyperbolic cosine of x

    See Also
    ========

    asinh, atanh, cosh
    """

    def fdiff(self, argindex=1):
        if argindex == 1:
            return 1/sqrt(self.args[0]**2 - 1)
        else:
            raise ArgumentIndexError(self, argindex)

    @classmethod
    def eval(cls, arg):
        arg = sympify(arg)

        if arg.is_Number:
            if arg is S.NaN:
                return S.NaN
            elif arg is S.Infinity:
                return S.Infinity
            elif arg is S.NegativeInfinity:
                return S.Infinity
            elif arg is S.Zero:
                return S.Pi*S.ImaginaryUnit / 2
            elif arg is S.One:
                return S.Zero
            elif arg is S.NegativeOne:
                return S.Pi*S.ImaginaryUnit

        if arg.is_number:
            cst_table = {
                S.ImaginaryUnit: log(S.ImaginaryUnit*(1 + sqrt(2))),
                -S.ImaginaryUnit: log(-S.ImaginaryUnit*(1 + sqrt(2))),
                S.Half: S.Pi/3,
                -S.Half: 2*S.Pi/3,
                sqrt(2)/2: S.Pi/4,
                -sqrt(2)/2: 3*S.Pi/4,
                1/sqrt(2): S.Pi/4,
                -1/sqrt(2): 3*S.Pi/4,
                sqrt(3)/2: S.Pi/6,
                -sqrt(3)/2: 5*S.Pi/6,
                (sqrt(3) - 1)/sqrt(2**3): 5*S.Pi/12,
                -(sqrt(3) - 1)/sqrt(2**3): 7*S.Pi/12,
                sqrt(2 + sqrt(2))/2: S.Pi/8,
                -sqrt(2 + sqrt(2))/2: 7*S.Pi/8,
                sqrt(2 - sqrt(2))/2: 3*S.Pi/8,
                -sqrt(2 - sqrt(2))/2: 5*S.Pi/8,
                (1 + sqrt(3))/(2*sqrt(2)): S.Pi/12,
                -(1 + sqrt(3))/(2*sqrt(2)): 11*S.Pi/12,
                (sqrt(5) + 1)/4: S.Pi/5,
                -(sqrt(5) + 1)/4: 4*S.Pi/5
            }

            if arg in cst_table:
                if arg.is_real:
                    return cst_table[arg]*S.ImaginaryUnit
                return cst_table[arg]

        if arg.is_infinite:
            return S.Infinity

    @staticmethod
    @cacheit
    def taylor_term(n, x, *previous_terms):
        if n == 0:
            return S.Pi*S.ImaginaryUnit / 2
        elif n < 0 or n % 2 == 0:
            return S.Zero
        else:
            x = sympify(x)
            if len(previous_terms) >= 2 and n > 2:
                p = previous_terms[-2]
                return p * (n - 2)**2/(n*(n - 1)) * x**2
            else:
                k = (n - 1) // 2
                R = RisingFactorial(S.Half, k)
                F = factorial(k)
                return -R / F * S.ImaginaryUnit * x**n / n

class atanh(InverseHyperbolicFunction):
    """
    The inverse hyperbolic tangent function.

    * atanh(x) -> Returns the inverse hyperbolic tangent of x

    See Also
    ========

    asinh, acosh, tanh
    """

    def fdiff(self, argindex=1):
        if argindex == 1:
            return 1/(1 - self.args[0]**2)
        else:
            raise ArgumentIndexError(self, argindex)

    @classmethod
    def eval(cls, arg):
        from sympy import atan
        arg = sympify(arg)

        if arg.is_Number:
            if arg is S.NaN:
                return S.NaN
            elif arg is S.Zero:
                return S.Zero
            elif arg is S.One:
                return S.Infinity
            elif arg is S.NegativeOne:
                return S.NegativeInfinity
            elif arg is S.Infinity:
                return -S.ImaginaryUnit * atan(arg)
            elif arg is S.NegativeInfinity:
                return S.ImaginaryUnit * atan(-arg)
            elif arg.is_negative:
                return -cls(-arg)
        else:
            if arg is S.ComplexInfinity:
                return S.NaN

            i_coeff = arg.as_coefficient(S.ImaginaryUnit)

            if i_coeff is not None:
                return S.ImaginaryUnit * atan(i_coeff)
            else:
                if _coeff_isneg(arg):
                    return -cls(-arg)

    @staticmethod
    @cacheit
    def taylor_term(n, x, *previous_terms):
        if n < 0 or n % 2 == 0:
            return S.Zero
        else:
            x = sympify(x)
            return x**n / n

    def _eval_as_leading_term(self, x):
        from sympy import Order
        arg = self.args[0].as_leading_term(x)

        if x in arg.free_symbols and Order(1, x).contains(arg):
            return arg
        else:
            return self.func(arg)

class acoth(InverseHyperbolicFunction):
    """
    The inverse hyperbolic cotangent function.

    * acoth(x) -> Returns the inverse hyperbolic cotangent of x
    """

    def fdiff(self, argindex=1):
        if argindex == 1:
            return 1/(1 - self.args[0]**2)
        else:
            raise ArgumentIndexError(self, argindex)

    @classmethod
    def eval(cls, arg):
        from sympy import acot
        arg = sympify(arg)

        if arg.is_Number:
            if arg is S.NaN:
                return S.NaN
            elif arg is S.Infinity:
                return S.Zero
            elif arg is S.NegativeInfinity:
                return S.Zero
            elif arg is S.Zero:
                return S.Pi*S.ImaginaryUnit / 2
            elif arg is S.One:
                return S.Infinity
            elif arg is S.NegativeOne:
                return S.NegativeInfinity
            elif arg.is_negative:
                return -cls(-arg)
        else:
            if arg is S.ComplexInfinity:
                return 0

            i_coeff = arg.as_coefficient(S.ImaginaryUnit)

            if i_coeff is not None:
                return -S.ImaginaryUnit * acot(i_coeff)
            else:
                if _coeff_isneg(arg):
                    return -cls(-arg)

    @staticmethod
    @cacheit
    def taylor_term(n, x, *previous_terms):
        if n == 0:
            return S.Pi*S.ImaginaryUnit / 2
        elif n < 0 or n % 2 == 0:
            return S.Zero
        else:
            x = sympify(x)
            return x**n / n

    def _eval_as_leading_term(self, x):
        from sympy import Order
        arg = self.args[0].as_leading_term(x)

        if x in arg.free_symbols and Order(1, x).contains(arg):
            return S.ImaginaryUnit*S.Pi/2
        else:
            return self.func(arg)
class asech(InverseHyperbolicFunction):

    def fdiff(self, argindex=1):
        if argindex == 1:
            z = self.args[0]
            return -1/(z*sqrt(1 - z**2))
        else:
            raise ArgumentIndexError(self, argindex)

    @classmethod
    def eval(cls, arg):
        arg = sympify(arg)

        if arg.is_Number:
            if arg is S.NaN:
                return S.NaN
            elif arg is S.Infinity:
                return S.Pi*S.ImaginaryUnit / 2
            elif arg is S.NegativeInfinity:
                return S.Pi*S.ImaginaryUnit / 2
            elif arg is S.Zero:
                return S.Infinity
            elif arg is S.One:
                return S.Zero
            elif arg is S.NegativeOne:
                return S.Pi*S.ImaginaryUnit

        if arg.is_number:
            cst_table = {
                S.ImaginaryUnit: - (S.Pi*S.ImaginaryUnit / 2) + log(1 + sqrt(2)),
                -S.ImaginaryUnit: (S.Pi*S.ImaginaryUnit / 2) + log(1 + sqrt(2)),
                (sqrt(6) - sqrt(2)): S.Pi / 12,
                (sqrt(2) - sqrt(6)): 11*S.Pi / 12,
                sqrt(2 - 2/sqrt(5)): S.Pi / 10,
                -sqrt(2 - 2/sqrt(5)): 9*S.Pi / 10,
                2 / sqrt(2 + sqrt(2)): S.Pi / 8,
                -2 / sqrt(2 + sqrt(2)): 7*S.Pi / 8,
                2 / sqrt(3): S.Pi / 6,
                -2 / sqrt(3): 5*S.Pi / 6,
                (sqrt(5) - 1): S.Pi / 5,
                (1 - sqrt(5)): 4*S.Pi / 5,
                sqrt(2): S.Pi / 4,
                -sqrt(2): 3*S.Pi / 4,
                sqrt(2 + 2/sqrt(5)): 3*S.Pi / 10,
                -sqrt(2 + 2/sqrt(5)): 7*S.Pi / 10,
                S(2): S.Pi / 3,
                -S(2): 2*S.Pi / 3,
                sqrt(2*(2 + sqrt(2))): 3*S.Pi / 8,
                -sqrt(2*(2 + sqrt(2))): 5*S.Pi / 8,
                (1 + sqrt(5)): 2*S.Pi / 5,
                (-1 - sqrt(5)): 3*S.Pi / 5,
                (sqrt(6) + sqrt(2)): 5*S.Pi / 12,
                (-sqrt(6) - sqrt(2)): 7*S.Pi / 12,
            }

            if arg in cst_table:
                if arg.is_real:
                    return cst_table[arg]*S.ImaginaryUnit
                return cst_table[arg]

        if arg is S.ComplexInfinity:
            return S.NaN

    @staticmethod
    @cacheit
    def expansion_term(n, x, *previous_terms):
        if n == 0:
            return log(2 / x)
        elif n < 0 or n % 2 == 1:
            return S.Zero
        else:
            x = sympify(x)
            if len(previous_terms) > 2 and n > 2:
                p = previous_terms[-2]
                return p * (n - 1)**2 // (n // 2)**2 * x**2 / 4
            else:
                k = n // 2
                R = RisingFactorial(S.Half , k) *  n
                F = factorial(k) * n // 2 * n // 2
                return -1 * R / F * x**n / 4

class acsch(InverseHyperbolicFunction):

    def fdiff(self, argindex=1):
        if argindex == 1:
            z = self.args[0]
            return -1/(z**2*sqrt(1 + 1/z**2))
        else:
            raise ArgumentIndexError(self, argindex)

    @classmethod
    def eval(cls, arg):
        arg = sympify(arg)

        if arg.is_Number:
            if arg is S.NaN:
                return S.NaN
            elif arg is S.Infinity:
                return S.Zero
            elif arg is S.NegativeInfinity:
                return S.Zero
            elif arg is S.Zero:
                return S.ComplexInfinity
            elif arg is S.One:
                return log(1 + sqrt(2))
            elif arg is S.NegativeOne:
                return - log(1 + sqrt(2))

        if arg.is_number:
            cst_table = {
                S.ImaginaryUnit: -S.Pi / 2,
                S.ImaginaryUnit*(sqrt(2) + sqrt(6)): -S.Pi / 12,
                S.ImaginaryUnit*(1 + sqrt(5)): -S.Pi / 10,
                S.ImaginaryUnit*2 / sqrt(2 - sqrt(2)): -S.Pi / 8,
                S.ImaginaryUnit*2: -S.Pi / 6,
                S.ImaginaryUnit*sqrt(2 + 2/sqrt(5)): -S.Pi / 5,
                S.ImaginaryUnit*sqrt(2): -S.Pi / 4,
                S.ImaginaryUnit*(sqrt(5)-1): -3*S.Pi / 10,
                S.ImaginaryUnit*2 / sqrt(3): -S.Pi / 3,
                S.ImaginaryUnit*2 / sqrt(2 + sqrt(2)): -3*S.Pi / 8,
                S.ImaginaryUnit*sqrt(2 - 2/sqrt(5)): -2*S.Pi / 5,
                S.ImaginaryUnit*(sqrt(6) - sqrt(2)): -5*S.Pi / 12,
                S(2): -S.ImaginaryUnit*log((1+sqrt(5))/2),
            }

            if arg in cst_table:
                return cst_table[arg]*S.ImaginaryUnit

        if arg is S.ComplexInfinity:
            return S.Zero

        if _coeff_isneg(arg):
            return -cls(-arg)

    def inverse(self, argindex=1):
        """
        Returns the inverse of this function.
        """
        return csch

    def _eval_rewrite_as_log(self, arg):
        return log(1/arg + sqrt(1/arg**2 + 1))
