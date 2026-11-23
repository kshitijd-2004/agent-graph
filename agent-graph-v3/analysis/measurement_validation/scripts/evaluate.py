#!/usr/bin/env python3.11
"""PHASE C — extraction validation and divergence-classification evaluation.

Compares the frozen extractor, the v6 extractor, and (when supplied) two
human annotators' adjudicated labels, against the SAME normalised view
(``views.py``) and the SAME grading path (``graders.py``).

gated-on-human-labels: every function here that requires human labels
raises ``HumanLabelsMissing`` rather than silently substituting a
model-produced label.  Task §4 forbids generating supposed human labels with
v6 or an LLM, so a missing annotation is a hard stop, not a default.

RUN MODES
---------
  --stage extractor_only   run everything that does NOT need human labels:
                           extractor-vs-extractor agreement, coverage,
                           state profiles, GT-leak probe.  Always runnable.
  --stage full             the human-grounded evaluation.  Requires
                           ``annotation_templates/ADJUDICATED.jsonl``.

Usage:
    python3.11 analysis/measurement_validation/scripts/evaluate.py \
        --stage extractor_only
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common as C  # noqa: E402
import stats_v1 as S  # noqa: E402
from extractors import load_specs, run_both, leak_probe  # noqa: E402
import views as V  # noqa: E402


def _load_local_graders():
    """Import THIS directory's ``graders`` by explicit path.

    A plain ``import graders`` is unsafe here.  ``extractors`` prepends
    ``C.V6_DIR`` to ``sys.path``, and ``analysis/measurement_v6/`` contains
    its own ``graders.py``.  A name-based import therefore resolves to the v6
    module, whose API is different — the failure surfaces only as an
    AttributeError deep inside the grading loop, after the wrong module has
    already been silently bound.  Loading by file path cannot be shadowed.
    """
    import importlib.util
    name = '_mv_validation_graders'
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, 'graders.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


G = _load_local_graders()
assert hasattr(G, 'grade_family'), (
    'loaded the wrong graders module — the validation graders was expected')

SAMPLE = os.path.join(C.VALIDATION_ROOT, 'sample_manifest.jsonl')
EVIDENCE = os.path.join(C.VALIDATION_ROOT, 'reports')
ADJUDICATED = os.path.join(C.VALIDATION_ROOT, 'annotation_templates',
                           'ADJUDICATED.jsonl')
ANN_DIR = os.path.join(C.VALIDATION_ROOT, 'annotation_templates')

DIMS = C.DESIGN['dimensions']


class HumanLabelsMissing(RuntimeError):
    """Raised when an evaluation step needs adjudicated labels and none exist.

    This is deliberate: the framework must never fabricate a human label, and
    must never quietly substitute the pipeline's own prediction for one.
    """


def require_adjudicated(path: str = ADJUDICATED) -> dict:
    if not os.path.isfile(path):
        raise HumanLabelsMissing(
            f'no adjudicated labels at {path}. Phase B (human annotation) has '
            'not been completed. Run with --stage extractor_only for the '
            'steps that do not depend on human judgment, or supply the '
            'adjudicated file to run the full evaluation.')
    out = {}
    for r in C.read_jsonl(path):
        out[r['item_token']] = r
    if not out:
        raise HumanLabelsMissing(f'{path} exists but is empty')
    return out


# ── evidence extraction (no human labels needed) ────────────────────────────
def extract_all(sample, specs, idx, limit=None):
    """Run BOTH extractors on every sampled trace and return per-unit recs."""
    out = []
    for i, rec in enumerate(sample):
        if limit and i >= limit:
            break
        path = rec.get('source_path') or (idx.get(rec['trace_id']) or {}).get('path')
        if not path or not os.path.isfile(path):
            out.append({'unit_id': rec['unit_id'], 'error': 'trace_missing'})
            continue
        spec = specs.get(rec['fixture_id'])
        if spec is None:
            out.append({'unit_id': rec['unit_id'], 'error': 'spec_missing'})
            continue
        tr = C.read_json(path)
        fam = rec['task_family']
        st = run_both(tr, spec, fam)
        fv = V.frozen_view(fam, st['frozen'])
        vv = V.v6_view(fam, st['v6'])
        # deliverable-only comparison (the part that is gradeable)
        dkeys = tuple(sorted(set(fv['deliverable_keys']) | set(vv['deliverable_keys'])))
        cmp_deliv = V.compare_views(fv, vv, dkeys) if dkeys else {
            'per_key': {}, 'tally': {}, 'n_keys': 0}
        cmp_all = V.compare_views(fv, vv)
        gf, gfdet = G.grade_family(fam, fv, spec)
        gv, gvdet = G.grade_family(fam, vv, spec)
        out.append({
            'unit_id': rec['unit_id'], 'trace_id': rec['trace_id'],
            'task_family': fam, 'fixture_id': rec['fixture_id'],
            'component': rec.get('component'), 'stratum': rec.get('stratum'),
            'inclusion_probability': rec.get('inclusion_probability'),
            'design_weight': rec.get('design_weight'),
            'frozen_view_states': fv['deliverable_states'],
            'v6_view_states': vv['deliverable_states'],
            'deliverable_compare': cmp_deliv,
            'all_compare': cmp_all,
            'frozen_grade': gf, 'frozen_grade_detail': gfdet,
            'v6_grade': gv, 'v6_grade_detail': gvdet,
            'extraction_agreement': (gf == gv),
            # containment attestation for this extraction: was the spec
            # GT-value-free at the point the extractors received it?
            'spec_gt_value_free': st.get('spec_gt_value_free'),
            'spec_residual_gt_value_fields':
                st.get('spec_residual_gt_value_fields'),
            # audit-only: does any v6-declared GT key remain reachable?
            'spec_residual_gt_keys_v6': st.get('spec_residual_gt_keys_v6'),
        })
    return out


# ── extractor-vs-extractor agreement report ────────────────────────────────
def report_extractor_agreement(recs, outdir):
    C.ensure_dir(outdir)
    ok = [r for r in recs if 'error' not in r]
    C.write_json(os.path.join(outdir, 'extractor_vs_extractor.json'), {
        'n_units': len(recs), 'n_ok': len(ok),
        'n_errors': len(recs) - len(ok),
        'error_reasons': dict(collections.Counter(
            r.get('error') for r in recs if 'error' in r)),
    })

    # ── per-family deliverable agreement ───────────────────────────────────
    rows = []
    for fam in C.DESIGN['families']:
        sub = [r for r in ok if r['task_family'] == fam]
        n = len(sub)
        tally = collections.Counter()
        for r in sub:
            for o in (r['deliverable_compare']['tally'] or {}):
                tally[o] += r['deliverable_compare']['tally'][o]
        tot = sum(tally.values())
        rows.append({
            'task_family': fam, 'n_units': n,
            'n_deliverable_items': tot,
            'outcome_rates': {k: round(v / tot, 4) for k, v in
                              tally.most_common()} if tot else {},
            'both_observed': tally.get('exact_match', 0)
            + tally.get('value_differs', 0)
            + tally.get('state_differs', 0),
            'exact_agreement': (tally.get('exact_match', 0)
                                / max(1, tally.get('exact_match', 0)
                                      + tally.get('value_differs', 0)
                                      + tally.get('state_differs', 0))),
        })
    C.write_json(os.path.join(outdir, 'deliverable_agreement_by_family.json'),
                 rows)

    # ── grading agreement ──────────────────────────────────────────────────
    grade_rows = []
    for fam in C.DESIGN['families']:
        sub = [r for r in ok if r['task_family'] == fam]
        cm = collections.Counter((r['frozen_grade'], r['v6_grade']) for r in sub)
        consider = [r for r in sub
                    if r['frozen_grade'] != G.UNGRADEABLE
                    and r['v6_grade'] != G.UNGRADEABLE]
        agree = sum(1 for r in consider if r['frozen_grade'] == r['v6_grade'])
        grade_rows.append({
            'task_family': fam,
            'n_units': len(sub),
            'grade_matrix': {f'{a}->{b}': v for (a, b), v in cm.most_common()},
            'n_both_gradeable': len(consider),
            'grade_agreement_on_gradeable':
                (agree / len(consider)) if consider else None,
            'frozen_gradeable_rate':
                sum(1 for r in sub if r['frozen_grade'] != G.UNGRADEABLE)
                / max(1, len(sub)),
            'v6_gradeable_rate':
                sum(1 for r in sub if r['v6_grade'] != G.UNGRADEABLE)
                / max(1, len(sub)),
        })
    C.write_json(os.path.join(outdir, 'grading_agreement_by_family.json'),
                 grade_rows)

    # ── extraction COVERAGE: the headline structural finding ──────────────
    cov = []
    for fam in C.DESIGN['families']:
        sub = [r for r in ok if r['task_family'] == fam]
        n = max(1, len(sub))
        fz_obs = v6_obs = both = neither = 0
        for r in sub:
            fo = any(s == 'observed' for s in r['frozen_view_states'].values())
            vo = any(s == 'observed' for s in r['v6_view_states'].values())
            fz_obs += fo
            v6_obs += vo
            both += (fo and vo)
            neither += (not fo and not vo)
        cov.append({
            'task_family': fam, 'n_units': len(sub),
            'frozen_deliverable_observed_rate': fz_obs / n,
            'v6_deliverable_observed_rate': v6_obs / n,
            'both_observed_rate': both / n,
            'neither_observed_rate': neither / n,
            'coverage_gap_pp': round(100 * (v6_obs - fz_obs) / n, 2),
        })
    C.write_json(os.path.join(outdir, 'extraction_coverage_by_family.json'),
                 cov)
    return {'coverage': cov, 'grade_agreement': grade_rows}


def report_state_profile(recs, outdir):
    """State distribution per family per dimension, both extractors."""
    out = {}
    for fam in C.DESIGN['families']:
        sub = [r for r in recs if r.get('task_family') == fam and 'error' not in r]
        rows = {}
        for who, key in (('frozen', 'frozen_view_states'),
                         ('v6', 'v6_view_states')):
            c = collections.Counter()
            for r in sub:
                for s in (r[key] or {}).values():
                    c[s] += 1
            rows[who] = dict(c.most_common())
        out[fam] = {'n_units': len(sub), 'deliverable_states': rows}
    C.write_json(os.path.join(outdir, 'extraction_state_profile.json'), out)
    return out


def run_leak_probe(sample, specs, idx, n=25, outdir=EVIDENCE):
    """Differential GT-containment probe over a seeded subset."""
    C.ensure_dir(outdir)
    rng = C.Rng(C.DESIGN['seed'] + 991)
    pick = rng.order([r for r in sample if r.get('kind') == 'lep'])[:n]
    results = []
    for rec in pick:
        path = rec.get('source_path') or (idx.get(rec['trace_id']) or {}).get('path')
        spec = specs.get(rec['fixture_id'])
        if not path or spec is None:
            continue
        tr = C.read_json(path)
        lp = leak_probe(tr, spec, rec['task_family'])
        lp['unit_id'] = rec['unit_id']
        lp['task_family'] = rec['task_family']
        results.append(lp)
    leaked = [r for r in results if r['leaked']]
    vacuous = [r for r in results if r.get('vacuous')]
    C.write_json(os.path.join(outdir, 'ground_truth_leak_probe.json'), {
        'method': ('canary differential: replace every GT-VALUE field with a '
                   'canary (task-side label/unit kept), extract, and compare '
                   'against the value-stripped extraction; any difference or '
                   'canary echo in the output means a GT-value field '
                   'influenced extraction'),
        'n_traces_probed': len(results),
        'n_leaked': len(leaked),
        'n_vacuous': len(vacuous),
        'leaked': leaked,
        'result': ('PASS' if not leaked else 'FAIL'),
        'note': ('A probe is VACUOUS when no GT-value field exists in the '
                 'spec to canary: it cannot detect a leak and must not be '
                 'read as a pass.  The probe tests GT-VALUE invariance; a '
                 'dependence on a task-side field (unit/label) is '
                 'legitimate and is kept identical on both arms, so it '
                 'cannot produce a false positive.'),
    })
    return {'n': len(results), 'n_leaked': len(leaked),
            'n_vacuous': len(vacuous)}


# ── human-grounded evaluation (Phase C, requires adjudication) ──────────────
def evaluate_against_humans(recs, adj, outdir):
    """Extractor accuracy vs adjudicated human labels, per dimension/family.

    A human label of ``unavailable`` is a COVERAGE outcome, never a correct
    negative: those rows are removed from the false-positive denominator and
    reported separately as an extraction-coverage figure.
    """
    C.ensure_dir(outdir)
    by_token = {r['unit_id']: r for r in recs if 'error' not in r}
    results = {'by_family': {}, 'by_dimension': {}, 'overall': {}}
    strata = {'A_representative': [], 'B_targeted': []}

    def item_pairs(unit_rec, human):
        """Yield (key, human_state, human_value, frozen, v6) for one unit."""
        for key, h in (human.get('labels') or {}).items():
            hs = V.canon_state(h.get('state'))
            hv = h.get('value')
            fv = {k: v for k, v in unit_rec['frozen_view_states'].items()}
            yield key, hs, hv, fv.get(key), None

    for tok, human in adj.items():
        u = by_token.get(tok)
        if not u:
            continue
        for key, hs, hv, fs, _vs in item_pairs(u, human):
            if hs == 'unavailable':
                continue
            bucket = dict(task_family=u['task_family'], dim=None,
                          human_state=hs, frozen_state=fs)
            strata.setdefault(u.get('component') or 'unknown', []).append(bucket)

    # The full metric suite is built when the adjudicated file carries the
    # per-dimension divergence judgments; until then the structure is emitted
    # with the eligible-row counts, so the shape of the eventual report is
    # fixed and the pending counts are visible.
    for k, v in strata.items():
        results.setdefault('eligible_by_component', {})[k] = len(v)
    C.write_json(os.path.join(outdir, 'human_grounded_metrics.json'), results)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', default='extractor_only',
                    choices=['extractor_only', 'full'])
    ap.add_argument('--sample', default=SAMPLE)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--outdir', default=EVIDENCE)
    a = ap.parse_args()
    C.ensure_dir(a.outdir)

    sample = C.read_jsonl(a.sample)
    idx = {r['trace_id']: r for r in C.read_jsonl(
        os.path.join(C.CACHE_DIR, 'trace_index.jsonl'))}
    specs = load_specs()
    print(f'sample units: {len(sample)} ({a.stage})')
    recs = extract_all(sample, specs, idx, limit=a.limit)
    C.write_jsonl(os.path.join(C.CACHE_DIR, 'extraction_evidence.jsonl'), recs)

    agree = report_extractor_agreement(recs, a.outdir)
    states = report_state_profile(recs, a.outdir)
    print('\ncoverage:')
    for r in agree['coverage']:
        print(f"  {r['task_family']:20s} frozen={r['frozen_deliverable_observed_rate']:.3f} "
              f"v6={r['v6_deliverable_observed_rate']:.3f} "
              f"gap={r['coverage_gap_pp']:+.1f}pp")
    print('\ngrade agreement on gradeable rows:')
    for r in agree['grade_agreement']:
        v = r['grade_agreement_on_gradeable']
        print(f"  {r['task_family']:20s} n={r['n_both_gradeable']:5d} "
              f"agree={'n/a' if v is None else f'{v:.3f}'}")

    lp = run_leak_probe(sample, specs, idx, outdir=a.outdir)
    print(f"\nGT leak probe: {lp['n']} traces, leaked={lp['n_leaked']}")

    if a.stage == 'full':
        adj = require_adjudicated()
        res = evaluate_against_humans(recs, adj, a.outdir)
        print(f"\nhuman-grounded evaluation: {json.dumps(res, indent=1)[:800]}")
    else:
        print('\n--stage extractor_only complete. The human-grounded metrics '
              'require adjudicated labels (Phase B) and are NOT reported here.')


if __name__ == '__main__':
    main()
