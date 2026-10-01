from random import randrange, choice
from math import log
from sympy.ntheory import primefactors
from sympy import multiplicity, factorint, Symbol
from sympy.combinatorics import Permutation
from sympy.combinatorics.permutations import (_af_commutes_with, _af_invert,
    _af_rmul, _af_rmuln, _af_pow, Cycle)
from sympy.combinatorics.util import (_check_cycles_alt_sym,
    _distribute_gens_by_base, _orbits_transversals_from_bsgs,
    _handle_precomputed_bsgs, _base_ordering, _strong_gens_from_distr,
    _strip, _strip_af)
from sympy.core import Basic
from sympy.functions.combinatorial.factorials import factorial
from sympy.ntheory import sieve
from sympy.utilities.iterables import has_variety, is_sequence, uniq
from sympy.testing.randtest import _randrange
from itertools import islice
from sympy.core.sympify import _sympify
class PermutationGroup(Basic):
    def minimal_blocks(self, randomized=True):
        def _number_blocks(blocks):
            # number the blocks of a block system
            # in order and return the number of
            # blocks and the tuple with the
            # reordering
            n = len(blocks)
            appeared = {}
            m = 0
            b = [None]*n
            for i in range(n):
                if blocks[i] not in appeared:
                    appeared[blocks[i]] = m
                    b[i] = m
                    m += 1
                else:
                    b[i] = appeared[blocks[i]]
            return tuple(b), m

        if not self.is_transitive():
            return False
        blocks = []
        num_blocks = []
        rep_blocks = []
        if randomized:
            random_stab_gens = []
            v = self.schreier_vector(0)
            for i in range(len(self)):
                random_stab_gens.append(self.random_stab(0, v))
            stab = PermutationGroup(random_stab_gens)
        else:
            stab = self.stabilizer(0)
        orbits = stab.orbits()
