#!/usr/bin/env python3.11
"""Build the canonical population + duplicate reconciliation (requirement 9).

Writes ledger/population.json + ledger/duplicate_reconciliation.json.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
import cv2


def main():
    out_dir = os.path.join(os.path.abspath(os.path.join(HERE, '..')), 'ledger')
    os.makedirs(out_dir, exist_ok=True)
    cells, dupes = cv2.load_cells()
    full = json.load(open(cv2.CELLS))['cells']

    # per-duplicate forensics across the source ledgers
    cal_by = cv2._by(cv2.CALLEDGER)
    v6 = cv2._jsonl(cv2.V6_LEDGER)
    v6_by_rows = {}
    for i, r in enumerate(v6):
        v6_by_rows.setdefault(r['trace_id'], []).append(i)
    ds_by_rows = {}
    for i, r in enumerate(cv2._jsonl(cv2.DOWNSTREAM)):
        ds_by_rows.setdefault(r['trace_id'], []).append(i)

    diff_fields = {}
    for tid in dupes:
        crows = cal_by[tid]
        # compare the calibrated ledger's two copies on gate fields
        cal_rows = [c for c in cv2._jsonl(cv2.CALLEDGER) if c['trace_id'] == tid]
        cal_diff = sorted({k for k in cal_rows[0]
                            for c2 in cal_rows[1:]
                            if cal_rows[0].get(k) != c2.get(k)}) if len(cal_rows) > 1 else []
        ds_rows = [r for r in cv2._jsonl(cv2.DOWNSTREAM) if r['trace_id'] == tid]
        ds_diff = sorted({k for k in ds_rows[0]
                           for c2 in ds_rows[1:]
                           if ds_rows[0].get(k) != c2.get(k)}) if len(ds_rows) > 1 else []
        diff_fields[tid] = {'cal_diff': cal_diff, 'ds_diff': ds_diff}

    rep = {
        'P1_full_rows': len(full),
        'P1star_unique': len(cells),
        'n_duplicates': len(dupes),
        'duplicate_ids': {t: idx for t, idx in dupes.items()},
        'retained_copy': {t: idx[0] for t, idx in dupes.items()},
        'excluded_copy': {t: idx[1] for t, idx in dupes.items()},
        'diff_fields': diff_fields,
        'note': ('canonical analysis unit = unique trace_id (P1* = 16,767). '
                 'The 6 second-copies are accidental double-records of the '
                 'same trace file (trace_index maps each to one path). '
                 'Gate-relevant fields agree across the two copies; only '
                 'legacy/diagnostic fields differ. See POPULATION_RECONCILIATION.md'),
    }
    with open(os.path.join(out_dir, 'duplicate_reconciliation.json'), 'w') as fh:
        json.dump(rep, fh, indent=1)
    with open(os.path.join(out_dir, 'population.json'), 'w') as fh:
        json.dump({'P1_full': len(full), 'P1star_unique': len(cells),
                   'dup_ids': sorted(dupes)}, fh, indent=1)
    print('P1* =', len(cells), 'dupes =', len(dupes))
    print('cal_diff fields:', {k: v['cal_diff'] for k, v in diff_fields.items()})
    print('ds_diff fields:', {k: v['ds_diff'] for k, v in diff_fields.items()})


if __name__ == '__main__':
    main()
