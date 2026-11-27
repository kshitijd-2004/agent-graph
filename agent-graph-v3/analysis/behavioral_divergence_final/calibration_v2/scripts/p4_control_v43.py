#!/usr/bin/env python3.11
"""p4 per-position threshold sensitivity on the V4.3 ledger (non-circular
probe-level control).  Each of the 3 benign reps is treated as the
exchangeable 4th execution: position i = the mean of the 2 in-cell benign
pairs NOT involving rep i (continuous dims), or k = # of the 2 pairs with
distance > 0 (discrete dims, one-sided binomial vs the family LEP-free
disagree-mass p_hat, n=2, alpha=0.05).  Continuous position fires are 0 by
construction (mean-of-2-of-3 - B < max-of-3); discrete positions fire under
the exact test.  Reports per-position FPR, execution row-OR, and the
independent-multiplicity model 1-(1-r)^3.  Writes out_v43/p4_control_v43.json.
"""
import importlib.util as ilu
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, V2)
sys.path.insert(0, HERE)
import cv2
import m1_gate_v43 as G  # loads the V4.3 ledger via load_v42_ledger()


def main():
    led = G.load_v42_ledger()          # V4.3 ledger (legacy fn name)
    cells, _ = cv2.load_cells()
    seen = set(); P1s = []
    for c in cells:
        if c['trace_id'] not in seen:
            seen.add(c['trace_id']); P1s.append(c['trace_id'])
    p_hat = G.family_disagree_mass(led, P1s)
    cont = 0; disc = 0; fires_by_dim = {}
    pos_total = 0; pos_fire = 0; row_or = 0; n_cells = 0
    for t in P1s:
        row = led.get(t)
        if not row or not row.get('comparable'):
            continue
        n_cells += 1
        fam = row.get('task_family')
        pb = [((p.get('components') or {}).get(d))
              for p in row.get('pair_benign', []) for d in ('__x__')]
        pb = [row['pair_benign'][i].get('components') or {}
              for i in range(len(row.get('pair_benign') or []))]
        cell_fired = False
        for d in G.DIMS:
            if len(pb) < 3:
                continue
            D = [pb[i].get(d) for i in range(3)]
            if d in G.CONT_DIMS:
                B = [v for v in D if v is not None]
                th = max(B) if B else None
                for i in range(3):
                    others = [v for j, v in enumerate(D)
                              if j != i and v is not None]
                    if len(others) < 2 or th is None:
                        continue
                    pos_total += 1
                    s = sum(others) / len(others)
                    f = (s - (sum(B) / len(B))) > th   # 0 by construction
                    cont += 1
                    if f:
                        pos_fire += 1; cell_fired = True
                        fires_by_dim[d] = fires_by_dim.get(d, 0) + 1
            else:
                p = p_hat.get((fam, d))
                if p is None:
                    continue
                for i in range(3):
                    others = [v for j, v in enumerate(D)
                              if j != i and v is not None]
                    if not others:
                        continue
                    k = sum(1 for v in others if v > 0)
                    pos_total += 1; disc += 1
                    if k > 0 and G._binom_tail(len(others), k, p) <= G.ALPHA:
                        pos_fire += 1; cell_fired = True
                        fires_by_dim[d] = fires_by_dim.get(d, 0) + 1
        if cell_fired:
            row_or += 1
    r = pos_fire / pos_total if pos_total else None
    model = (1 - (1 - r) ** 3) if r is not None else None
    rep = {'note': 'p4 per-position threshold sensitivity on the V4.3 ledger '
                   '(non-circular: positions are LEP-free exchangeable probes '
                   'vs the same-cell (B, th, p_hat) objects; continuous '
                   'position fires are 0 by construction; discrete use the '
                   'one-sided exact binomial, n = available pairs, alpha=0.05).',
           'n_cells': n_cells, 'pos_total': pos_total,
           'continuous_positions': cont, 'discrete_positions': disc,
           'pos_fire': pos_fire, 'per_position_fpr': r,
           'fires_by_dim': fires_by_dim,
           'execution_row_or_fired': row_or,
           'execution_row_or_fpr': (row_or / n_cells) if n_cells else None,
           'independent_multiplicity_model_row_or': model,
           'excess_over_model': ((row_or / n_cells) - model
                                 if n_cells and model else None)}
    out = os.path.join(V2, 'out_v43', 'p4_control_v43.json')
    json.dump(rep, open(out, 'w'), indent=1)
    print(f'p4 (V4.3): n_cells={n_cells} pos_eval={pos_total} '
          f'({cont} cont / {disc} disc)')
    print(f'  per-position FPR: {pos_fire}/{pos_total} = {r:.5f} '
          f'(fires_by_dim={fires_by_dim})')
    print(f'  execution row-OR: {row_or}/{n_cells} = '
          f'{(row_or/n_cells if n_cells else None):.4f} | '
          f'ind-multiplicity model 1-(1-r)^3 = {model:.4f} | '
          f'excess = {rep["excess_over_model"]:+.4f}')
    print('wrote', out)


if __name__ == '__main__':
    main()
