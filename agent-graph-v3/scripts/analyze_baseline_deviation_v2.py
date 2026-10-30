"""Offline BND-V2 (baseline-normalized behavioral deviation) evaluation.

V2 keeps V1's population-selection and baseline-relative design and corrects
the measurement defects the V1 validation audit confirmed (ground-truth-guided
unit rescale, silent repair-target failures, report-path inflation, missing
stage/recovery measurement, and two conflated reporting rules).  It reads the
consolidated production traces and the id_trc_v2 classification ledger
read-only, scores every perturbed trace against its three matched benign
references under V2's extraction + stage metrics, and writes all outputs to a
fresh directory.  No generation, no model calls, no GPU, no LLM.

Reference-selection policy is identical to V1 (``discover`` is imported from
``scripts.analyze_baseline_deviation``), so BND V2 measures the SAME population
of traces; only the scoring and reporting are new.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from benchmark import baseline_deviation_v2 as bdv2
from scripts.analyze_baseline_deviation import discover, read_json, read_jsonl, sha

LOG = logging.getLogger('scripts.analyze_baseline_deviation_v2')


# ── IO helpers (shared semantics with V1) ───────────────────────────────────


def quantiles(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {'n': 0}

    def q(p):
        k = (len(vals) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
        return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 6)

    return {'n': len(vals), 'mean': round(statistics.fmean(vals), 6),
            'median': q(0.5), 'p10': q(0.10), 'p25': q(0.25),
            'p75': q(0.75), 'p90': q(0.90), 'min': round(vals[0], 6),
            'max': round(vals[-1], 6)}


# ── worker-side measurement (process pool; per-worker spec cache) ───────────

_SPEC_CACHE: dict = {}


def _load_spec(spec_root: str, fixture_id):
    path = str(Path(spec_root) / str(fixture_id) / 'manifest.json')
    if path not in _SPEC_CACHE:
        p = Path(path)
        spec = json.loads(p.read_text()) if p.is_file() else None
        _SPEC_CACHE[path] = (spec, sha(p) if p.is_file() else None)
    return _SPEC_CACHE[path]


def measure_chunk(chunk: dict) -> list:
    """Measure one cell group: refs loaded once, spec cached per worker."""
    refs = [read_json(p) for p in chunk['ref_paths']]
    rows = []
    for rec in chunk['lep_records']:
        try:
            lep = read_json(rec['path'])
        except Exception as exc:
            rows.append({'trace_id': rec.get('attempt_id') or rec.get('path'),
                         'status': 'unparseable_lep', 'reasons': [str(exc)]})
            continue
        spec, spec_sha = _load_spec(chunk['spec_root'], rec['fixture'])
        out = bdv2.measure_configuration_v2(lep, refs, spec, chunk['weights'])
        out['status'] = 'measured'
        out['attempt_id'] = rec['attempt_id'] or out.get('trace_id')
        out['spec_sha256'] = spec_sha
        out['spec_available'] = spec is not None
        out['reference_complete'] = chunk['reference_complete']
        out['reference_repetitions'] = chunk['reference_repetitions']
        rows.append(out)
    return rows


# ── V2-propagation join (read-only cross-analysis; never feeds the score) ───


def attach_v2_classifications(results, v2_dir: Path):
    metrics = v2_dir / 'propagation_metrics.jsonl'
    if not metrics.is_file():
        for r in results:
            r['v2_classification'] = None
            r['v2_status'] = 'v2_ledger_missing'
        return Counter({'v2_ledger_missing': len(results)})
    ledger = {row.get('attempt_id'): row for row in read_jsonl(metrics)}
    counts: Counter = Counter()
    for r in results:
        row = ledger.get(r.get('attempt_id'))
        if row is None:
            r['v2_classification'] = None
            r['v2_status'] = 'unmeasured_by_v2'
        else:
            r['v2_classification'] = row.get('classification')
            r['v2_status'] = 'joined'
        counts[r['v2_status']] += 1
    return counts


# ── threshold sensitivity (each rule recomputed INDEPENDENTLY) ──────────────

#: Rules are independent; the audit conflated BND>0.05 (R2) with
#: D_LEP > T + 0.05 (R3).  They are computed from stored fields separately so
#: they can no longer be read as the same number.
SENS_RULES = {
    'R0_legacy_Dlep_gt_T':       lambda r: r.get('d_lep') > r.get('_t') if r.get('_t') is not None else None,
    'R1_bnd_gt_0':              lambda r: (r.get('bnd') or 0) > 0.0 if r.get('bnd') is not None else None,
    'R2_bnd_gt_0p05':           lambda r: (r.get('bnd') or 0) > 0.05 if r.get('bnd') is not None else None,
    'R3_Dlep_gt_T_plus_0p05':   lambda r: (r.get('d_lep') is not None and r.get('_t') is not None
                                          and r['d_lep'] > r['_t'] + 0.05),
    'R4_Dlep_gt_T_plus_0p10':   lambda r: (r.get('d_lep') is not None and r.get('_t') is not None
                                          and r['d_lep'] > r['_t'] + 0.10),
    'R5_Dlep_gt_T_times_1p25':  lambda r: (r.get('d_lep') is not None and r.get('_t') is not None
                                          and r['d_lep'] > r['_t'] * 1.25),
}


def threshold_sensitivity(results):
    comparable = [r for r in results if r.get('status') == 'measured' and r.get('comparable')]
    for r in comparable:
        r['_t'] = (r.get('threshold') or {}).get('t')
    table = {}
    for name, fn in SENS_RULES.items():
        vals = [fn(r) for r in comparable]
        flagged = [v for v in vals if v is True]
        unknown = sum(1 for v in vals if v is None)
        table[name] = {
            'n_comparable': len(comparable),
            'flagged': len(flagged),
            'fraction': round(len(flagged) / len(comparable), 6) if comparable else None,
            'unevaluable': unknown,
        }
    # near-threshold counts and zero-T cells
    near_01 = sum(1 for r in comparable if r.get('bnd') is not None and abs(r['bnd']) <= 0.01)
    near_05 = sum(1 for r in comparable if r.get('bnd') is not None and abs(r['bnd']) <= 0.05)
    zero_T = sum(1 for r in comparable if r.get('_t') == 0.0)
    table['near_threshold'] = {
        'n_comparable': len(comparable),
        'n_abs_bnd_le_0p01': near_01,
        'n_abs_bnd_le_0p05': near_05,
        'n_zero_T_cells': zero_T,
    }
    return table


def stage_coverage(results):
    """Per-stage-metric missingness across comparable rows."""
    comparable = [r for r in results if r.get('status') == 'measured' and r.get('comparable')]
    out = {}
    for key in ('d_peak', 'd_final', 'stage_threshold',
                'high_intermediate_deviation', 'final_deviation',
                'behavioral_convergence', 'verified_recovery'):
        vals = [(r.get('stage') or {}).get(key) for r in comparable]
        available = sum(1 for v in vals if v is not None)
        out[key] = {'available': available, 'unavailable': len(comparable) - available,
                    'coverage': round(available / len(comparable), 6) if comparable else None}
    # unavailable checkpoint reasons
    reasons: Counter = Counter()
    verified_unavail: Counter = Counter()
    for r in comparable:
        for cp in (r.get('stage') or {}).get('checkpoints', []):
            if cp.get('reason'):
                reasons[cp['reason']] += 1
        for u in (r.get('stage') or {}).get('verified_unavailable_reason', []):
            verified_unavail[u] += 1
    out['checkpoint_unavailable_reasons'] = dict(reasons)
    out['verified_recovery_unavailable_reasons'] = dict(verified_unavail)
    return out


def stage_summary(results):
    comparable = [r for r in results if r.get('status') == 'measured' and r.get('comparable')]

    def fr(key):
        vals = [(r.get('stage') or {}).get(key) for r in comparable]
        present = [v for v in vals if v is not None]
        return {'n_present': len(present),
                'true': sum(1 for v in present if v is True),
                'false': sum(1 for v in present if v is False)}
    return {
        'n_comparable': len(comparable),
        'd_peak': quantiles([(r.get('stage') or {}).get('d_peak') for r in comparable]),
        'd_final': quantiles([(r.get('stage') or {}).get('d_final') for r in comparable]),
        'stage_threshold': quantiles([(r.get('stage') or {}).get('stage_threshold') for r in comparable]),
        'high_intermediate_deviation': fr('high_intermediate_deviation'),
        'final_deviation': fr('final_deviation'),
        'behavioral_convergence': fr('behavioral_convergence'),
        'verified_recovery': fr('verified_recovery'),
        'note': ('Stage metrics are baseline-relative and independently computed '
                 'from recorded events; they do NOT consult the V2 propagation '
                 'ledger.  high_intermediate_deviation / final_deviation use the '
                 'per-configuration stage threshold (pilot heuristic, not a '
                 'calibrated FP rate).'),
    }


# ── V1-vs-V2 comparison (frozen V1 per-trace ledger) ────────────────────────


def v1_vs_v2(results, v1_dir: Path):
    """Align V2 results with the frozen V1 ledger on trace_id.

    V1 rows carry behavioral_anomaly (legacy D_LEP > T under V1 extraction);
    V2 rows carry legacy_anomaly (same threshold rule, V2 extraction).  This
    isolates the extraction change from the threshold rule.
    """
    v1_file = v1_dir / 'bnd_per_trace.jsonl'
    if not v1_file.is_file():
        return {'note': f'frozen V1 ledger not found at {v1_file}', 'aligned': 0}
    v1 = {}
    for row in read_jsonl(v1_file):
        v1[row.get('trace_id')] = row
    aligned = 0
    legacy_flip = 0
    bnd_changed = 0
    examples = []
    directional = defaultdict(lambda: {'v1_anom': 0, 'v2_anom': 0, 'n': 0})
    for r in results:
        if r.get('status') != 'measured' or not r.get('comparable'):
            continue
        old = v1.get(r.get('trace_id'))
        if old is None or not old.get('comparable'):
            continue
        aligned += 1
        v1_anom = old.get('behavioral_anomaly')
        v2_anom = r.get('legacy_anomaly')
        if v1_anom is not None and v2_anom is not None and v1_anom != v2_anom:
            legacy_flip += 1
            if len(examples) < 20:
                v2feat = (r.get('features') or {}).get(r.get('trace_id'), {})
                examples.append({
                    'trace_id': r.get('trace_id'),
                    'lep_code': r.get('lep_code'),
                    'v1_behavioral_anomaly': v1_anom,
                    'v2_legacy_anomaly': v2_anom,
                    'v1_d_lep': old.get('d_lep'), 'v2_d_lep': r.get('d_lep'),
                    'v1_d_benign': old.get('d_benign'), 'v2_d_benign': r.get('d_benign'),
                    'v1_threshold_t': (old.get('threshold') or {}).get('t'),
                    'v2_threshold_t': (r.get('threshold') or {}).get('t'),
                    'v1_component_deltas': old.get('component_deltas'),
                    'v2_component_lep': r.get('component_lep'),
                    'v2_component_benign': r.get('component_benign'),
                    'v2_extracted_features': v2feat,
                    'v2_diagnostics': v2feat.get('diagnostics'),
                    'feature_level_note': (
                        'V2 vs V1 on the same comparable row: the legacy flag '
                        'moved while the threshold rule was held fixed, so the '
                        'change is attributable to extraction (V2 dropped the '
                        'ground-truth rescale and marked unresolvable facts '
                        'unavailable).  Compare v1_component_deltas (assertion '
                        'deltas the rescale created/suppressed) against '
                        'v2_extracted_features (which facts are now None vs '
                        'a resolved value).'),
                })
        if old.get('bnd') is not None and r.get('bnd') is not None \
                and abs(old['bnd'] - r['bnd']) > 1e-9:
            bnd_changed += 1
        if v1_anom is not None and v2_anom is not None:
            d = directional[r.get('lep_code')]
            d['n'] += 1
            d['v1_anom'] += bool(v1_anom)
            d['v2_anom'] += bool(v2_anom)
    # directional LEP ordering check: does V1's TRC>IPI>MP>HC~ID ordering survive?
    ordering = {}
    for lep, d in sorted(directional.items()):
        ordering[lep] = {
            'n_comparable_both': d['n'],
            'v1_anomaly_rate': round(d['v1_anom'] / d['n'], 4) if d['n'] else None,
            'v2_anomaly_rate': round(d['v2_anom'] / d['n'], 4) if d['n'] else None,
        }
    return {
        'note': ('Legacy-threshold flags compared V1-vs-V2 on the SAME '
                 'comparable population; a flip isolates the extraction '
                 'change (V2 no longer uses the ground-truth rescale, marks '
                 'unresolvable facts unavailable, fixes repair targets and '
                 'report paths).  Stage metrics are V2-only.'),
        'aligned_comparable_rows': aligned,
        'legacy_anomaly_flips': legacy_flip,
        'bnd_value_changed_rows': bnd_changed,
        'directional_by_lep': ordering,
        'legacy_flip_examples': examples,
    }


# ── rescale-flip reconciliation (from the audit's frozen flip ledger) ───────


def rescale_flip_check(results, v1_dir: Path, audit_dir: Path):
    """Re-examine the audit's 78 created / 32 suppressed rescale flips.

    V2 removes the truth-guided rescale by construction, so those specific
    ground-truth-driven flips should no longer appear as legacy-anomaly
    changes.  We report how many of the audit-flip trace_ids changed their
    legacy flag V1->V2 and whether the flip direction matches the audit's
    expectation (a "created" flag in V1 that was solely the rescale's doing
    should tend to drop under V2).
    """
    flips_file = audit_dir / 'rescale_flips.csv'
    if not flips_file.is_file():
        return {'note': f'audit flip ledger not found: {flips_file}', 'n_flips': 0}
    import csv
    created, suppressed = [], []
    with open(flips_file, newline='') as fh:
        for row in csv.DictReader(fh):
            if row.get('kind') != 'flip':
                continue
            tid = row.get('trace_id')
            flag_a, flag_b = row.get('flag_a'), row.get('flag_b')
            if flag_a in ('True', 'true') and flag_b not in ('True', 'true'):
                created.append(tid)      # shipped(rescale) flags, raw does not
            elif flag_b in ('True', 'true') and flag_a not in ('True', 'true'):
                suppressed.append(tid)   # raw flags, shipped(rescale) does not
    v1_ledger = {}
    v1_file = v1_dir / 'bnd_per_trace.jsonl'
    if v1_file.is_file():
        for row in read_jsonl(v1_file):
            v1_ledger[row.get('trace_id')] = row
    v2_by_tid = {r.get('trace_id'): r for r in results}

    def moved(tid, expect_drop):
        old = v1_ledger.get(tid)
        new = v2_by_tid.get(tid)
        if old is None or new is None:
            return None
        a, b = old.get('behavioral_anomaly'), new.get('legacy_anomaly')
        if a is None or b is None:
            return None
        return (a == b) if not expect_drop else (a and not b)

    def check(tids, expect_drop):
        known = [t for t in tids if t in v1_ledger and t in v2_by_tid]
        dropped = sum(1 for t in known if moved(t, True))
        kept = sum(1 for t in known if moved(t, False))
        return {'n_audit': len(tids), 'n_found_in_ledgers': len(known),
                'expect_drop': expect_drop, 'dropped_v1_to_v2': dropped,
                'retained': kept}

    return {
        'note': ('V2 removes the ground-truth rescale; the audit\'s "created" '
                 'flags (present only because of the rescale) are expected to '
                 'drop.  "Suppressed" flags are informational.  Counts are '
                 'recomputed on the frozen ledgers, not assumed.'),
        'created': check(created, expect_drop=True),
        'suppressed': check(suppressed, expect_drop=False),
        'denominator_note': (f'created={len(created)} + suppressed={len(suppressed)} '
                             f'audit flips; NOT a share of the overall rate.'),
    }


# ── recovery reevaluation (audit's 86 / 33 cases) ──────────────────────────


def recovery_reeval(results, audit_dir: Path, v2_dir: Path):
    data_file = audit_dir / 'recovery_case_studies_data.json'
    if not data_file.is_file():
        return {'note': f'audit recovery cases not found: {data_file}', 'n': 0}
    cases = json.loads(data_file.read_text())
    v2_by_tid = {r.get('trace_id'): r for r in results}
    led_v2 = {}
    m = v2_dir / 'propagation_metrics.jsonl'
    if m.is_file():
        for row in read_jsonl(m):
            led_v2[row.get('trace_id')] = row
    buckets = Counter()
    detail = []
    for c in cases:
        tid = c.get('trace_id')
        v2r = v2_by_tid.get(tid)
        if v2r is None or not (v2r.get('comparable')):
            buckets['v2_not_comparable'] += 1
            continue
        st = v2r.get('stage') or {}
        high_inter = st.get('high_intermediate_deviation')
        d_final = st.get('d_final')
        conv = st.get('behavioral_convergence')
        verified = st.get('verified_recovery')
        if high_inter is True:
            buckets['detected_high_intermediate'] += 1
            if d_final is not None and st.get('final_deviation') in (False, None):
                buckets['recovered_final_returned'] += 1
            if conv is True:
                buckets['behavioral_convergence'] += 1
            if verified is True:
                buckets['verified_recovery'] += 1
        elif high_inter is False or high_inter is None:
            buckets['still_missed_or_unavailable'] += 1
        if c.get('v2_transient') == 'detected' and c.get('v2_persistent') == 'not_detected':
            buckets['audit_true_recovery'] += 1
            # does V2 stage metric agree with the recorded recovery evidence?
            rec_events = c.get('n_recovery_events', 0)
            buckets['agree_with_recorded_recovery' if verified else 'disagree_or_unresolved'] += 1
        if len(detail) < 40:
            detail.append({'trace_id': tid, 'v2_transient': c.get('v2_transient'),
                          'v2_persistent': c.get('v2_persistent'),
                          'n_recovery_events': c.get('n_recovery_events'),
                          'v2_d_peak': st.get('d_peak'), 'v2_d_final': st.get('d_final'),
                          'v2_high_intermediate': high_inter,
                          'v2_final_deviation': st.get('final_deviation'),
                          'v2_convergence': conv, 'v2_verified_recovery': verified})
    return {'note': ('V2 stage metrics vs the audit\'s recorded recovery '
                     'evidence.  Agreement is REPORTED, not forced — the two '
                     'systems may differ, and a disagreement is itself a '
                     'finding.'),
            'n_cases': len(cases), 'buckets': dict(buckets), 'detail': detail}


# ── aggregation ──────────────────────────────────────────────────────────────


def group_summary(rows):
    comparable = [r for r in rows if r.get('comparable')]
    anomalous = [r for r in comparable if r.get('legacy_anomaly') is True]
    stage = [r.get('stage') or {} for r in comparable]
    hi = [s.get('high_intermediate_deviation') for s in stage]
    fi = [s.get('final_deviation') for s in stage]
    conv = [s.get('behavioral_convergence') for s in stage]
    return {
        'n': len(rows),
        'comparable': len(comparable),
        'incomparable': len(rows) - len(comparable),
        'legacy_anomaly_count': len(anomalous),
        'legacy_anomaly_fraction': round(len(anomalous) / len(comparable), 6) if comparable else None,
        'bnd': quantiles([r.get('bnd') for r in rows if r.get('bnd') is not None]),
        'd_lep': quantiles([r.get('d_lep') for r in rows if r.get('d_lep') is not None]),
        'd_benign': quantiles([r.get('d_benign') for r in rows if r.get('d_benign') is not None]),
        'threshold_t': quantiles([ (r.get('threshold') or {}).get('t') for r in comparable
                                   if (r.get('threshold') or {}).get('t') is not None]),
        'high_intermediate_fraction': _frac_of_stage(hi, comparable),
        'final_deviation_fraction': _frac_of_stage(fi, comparable),
        'behavioral_convergence_count': sum(1 for c in conv if c is True),
    }


def _frac_of_stage(vals, comparable):
    present = [v for v in vals if v is not None]
    if not present or not comparable:
        return None
    return round(sum(1 for v in present if v is True) / len(comparable), 6)


def by_field(results, field):
    groups: dict = defaultdict(list)
    for r in results:
        if r.get('status') != 'measured':
            continue
        groups[r.get(field) or 'unknown'].append(r)
    return {k: group_summary(v) for k, v in sorted(groups.items())}


# ── population accounting (reconciled, fixed denominators) ─────────────────


def population_accounting(results, v2_dir: Path):
    measured = [r for r in results if r.get('status') == 'measured']
    comparable = [r for r in measured if r.get('comparable')]
    comp_tids = {r.get('trace_id') for r in comparable}
    v2_ledger_tids = set()
    m = v2_dir / 'propagation_metrics.jsonl'
    if m.is_file():
        for row in read_jsonl(m):
            v2_ledger_tids.add(row.get('trace_id'))
    inter = len(comp_tids & v2_ledger_tids)
    bnd_comparable_v2_unmeasured = len(comp_tids - v2_ledger_tids)
    v2_measured_bnd_not_comparable = len(v2_ledger_tids - comp_tids)
    return {
        'note': ('Fixed denominators.  V2-unmeasured LEP traces are NEVER '
                 'counted as V2-negative; the intersection identity holds '
                 'exactly: comparable = intersect + bnd_comparable_v2_unmeasured.'),
        'bnd_measured': len(measured),
        'bnd_comparable': len(comparable),
        'v2_measured': len(v2_ledger_tids),
        'bnd_comparable_AND_v2_measured': inter,
        'bnd_comparable_v2_unmeasured': bnd_comparable_v2_unmeasured,
        'v2_measured_bnd_not_comparable': v2_measured_bnd_not_comparable,
        'identity_check': {
            'comparable_equals_intersect_plus_unmeasured':
                len(comparable) == inter + bnd_comparable_v2_unmeasured,
            'v2measured_equals_intersect_plus_notcomparable':
                len(v2_ledger_tids) == inter + v2_measured_bnd_not_comparable,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path,
                       default=Path('analysis/full_production_consolidated/source'))
    parser.add_argument('--output', type=Path,
                       default=Path('analysis/full_production_baseline_deviation_v2'))
    parser.add_argument('--spec-root', type=Path, default=Path('workspace_fixtures'))
    parser.add_argument('--v2-dir', type=Path, default=Path('analysis/full_production_id_trc_v2'))
    parser.add_argument('--v1-dir', type=Path,
                       default=Path('analysis/full_production_baseline_deviation_v1'))
    parser.add_argument('--audit-dir', type=Path,
                       default=Path('analysis/bnd_v1_validation'))
    parser.add_argument('--limit', type=int, default=None,
                       help='debug only: cap LEP traces before measurement')
    parser.add_argument('--task-families', default=None)
    parser.add_argument('--workers', type=int, default=24)
    args = parser.parse_args(argv)

    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f'refusing to write into non-empty directory: {output}')
    output.mkdir(parents=True)

    references, lep_records, skipped = discover(args.source)
    LOG.info('benign cells: %d, LEP traces: %d', len(references), len(lep_records))
    if args.task_families:
        allow = set(args.task_families.split(','))
        lep_records = [r for r in lep_records if r['task_family'] in allow]
    if args.limit:
        lep_records = lep_records[:args.limit]

    weights = bdv2.ComponentWeights()
    cells_by_key = defaultdict(list)
    for rec in lep_records:
        cells_by_key[(rec['fixture'], rec['topology'], rec['execution_variant'])].append(rec)
    ordered_cells = sorted(cells_by_key)
    chunk_index = []
    for key in ordered_cells:
        ref_entry = references.get(key) or {'paths': [], 'complete': False, 'repetitions': []}
        chunk_index.append((key, {
            'ref_paths': ref_entry['paths'],
            'reference_complete': ref_entry['complete'],
            'reference_repetitions': ref_entry['repetitions'],
            'lep_records': cells_by_key[key],
            'spec_root': str(args.spec_root.resolve()),
            'weights': weights}))
    chunks = [c for _, c in chunk_index]
    if args.workers and args.workers > 1 and len(chunks) > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            chunk_rows = list(pool.map(measure_chunk, chunks,
                                       chunksize=max(1, len(chunks) // (args.workers * 4))))
    else:
        chunk_rows = [measure_chunk(c) for c in chunks]
    results = [r for chunk in chunk_rows for r in chunk]
    LOG.info('measured %d LEP traces', len(results))

    v2_join = attach_v2_classifications(results, args.v2_dir)

    measured = [r for r in results if r.get('status') == 'measured']
    with open(output / 'bnd_v2_per_trace.jsonl', 'w') as fh:
        for r in sorted(results, key=lambda r: (r.get('status') != 'measured',
                                                 r.get('trace_id') or '')):
            fh.write(json.dumps(r, sort_keys=True, default=str) + '\n')

    by_lep = by_field(results, 'lep_code')
    by_fam = by_field(results, 'task_family')
    by_topo = by_field(results, 'topology')
    by_mode = by_field(results, 'propagation_mode')
    for name, data in (('bnd_v2_by_lep_type', by_lep),
                       ('bnd_v2_by_task_family', by_fam),
                       ('bnd_v2_by_topology', by_topo),
                       ('bnd_v2_by_propagation_mode', by_mode)):
        (output / f'{name}.json').write_text(json.dumps(data, indent=1, sort_keys=True) + '\n')

    sens = threshold_sensitivity(results)
    (output / 'threshold_sensitivity_v2.json').write_text(json.dumps(sens, indent=1, sort_keys=True) + '\n')
    (output / 'stage_coverage.json').write_text(json.dumps(stage_coverage(results), indent=1, sort_keys=True) + '\n')
    (output / 'stage_summary.json').write_text(json.dumps(stage_summary(results), indent=1, sort_keys=True) + '\n')

    v1cmp = v1_vs_v2(results, args.v1_dir)
    (output / 'v1_vs_v2_comparison.json').write_text(json.dumps(v1cmp, indent=1, sort_keys=True, default=str) + '\n')
    (output / 'rescale_flip_check.json').write_text(json.dumps(rescale_flip_check(results, args.v1_dir, args.audit_dir),
                                                               indent=1, sort_keys=True, default=str) + '\n')
    (output / 'recovery_reevaluation.json').write_text(json.dumps(recovery_reeval(results, args.audit_dir, args.v2_dir),
                                                                  indent=1, sort_keys=True, default=str) + '\n')
    (output / 'population_accounting.json').write_text(json.dumps(population_accounting(results, args.v2_dir),
                                                                 indent=1, sort_keys=True) + '\n')

    summary = {
        'version': bdv2.VERSION,
        'weights': weights.as_dict(),
        'overall': group_summary(measured),
        'by_lep_type': by_lep, 'by_task_family': by_fam,
        'by_topology': by_topo, 'by_propagation_mode': by_mode,
        'threshold_sensitivity': sens,
        'stage_coverage': stage_coverage(results),
        'stage_summary': stage_summary(results),
        'v2_join': dict(v2_join),
        'corrections': [
            'ground-truth-free unit normalization (V1 truth-rescale removed)',
            'final-answer read from final_response event, not last-occurrence',
            'repair targets from agent-emitted text; unresolvable -> unavailable',
            'report paths normalized; code paths Jaccard',
            'stage/recovery metrics (d_peak, d_final, convergence, verified_recovery)',
            'threshold + population reporting fixes',
        ],
        'scientific_separation': ('No BND score, stage metric, or flag consults the '
                                  'V2-propagation ledger; that ledger is joined '
                                  'read-only for cross-tabulation only.'),
    }
    (output / 'bnd_v2_summary.json').write_text(json.dumps(summary, indent=1, sort_keys=True, default=str) + '\n')

    manifest = {
        'module': 'benchmark/baseline_deviation_v2.py',
        'module_sha256': sha(Path('benchmark/baseline_deviation_v2.py')),
        'script_sha256': sha(Path('scripts/analyze_baseline_deviation_v2.py')),
        'v1_module_sha256': sha(Path('benchmark/baseline_deviation.py')),
        'source_dir': str(args.source.resolve()),
        'spec_root': str(args.spec_root.resolve()),
        'v1_dir': str(args.v1_dir.resolve()),
        'v2_dir': str(args.v2_dir.resolve()),
        'audit_dir': str(args.audit_dir.resolve()),
        'n_benign_cells': len(references),
        'n_lep_traces': len(lep_records),
        'reference_selection': 'Identical to V1 (imported discover); same population.',
        'deterministic': True,
        'agent_executions': 0,
        'external_model_calls': 0,
    }
    (output / 'run_manifest.json').write_text(json.dumps(manifest, indent=1, sort_keys=True) + '\n')

    o = summary['overall']
    LOG.info('wrote outputs to %s', output)
    print(json.dumps({
        'measured': len(measured),
        'comparable': o['comparable'],
        'legacy_anomaly_fraction': o['legacy_anomaly_fraction'],
        'bnd_mean': o['bnd'].get('mean'),
        'high_intermediate_fraction': o['high_intermediate_fraction'],
        'final_deviation_fraction': o['final_deviation_fraction'],
        'behavioral_convergence_count': o['behavioral_convergence_count'],
        'v2_join': dict(v2_join),
        'output_dir': str(output),
    }, indent=1, sort_keys=True))
    return 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    raise SystemExit(main())
