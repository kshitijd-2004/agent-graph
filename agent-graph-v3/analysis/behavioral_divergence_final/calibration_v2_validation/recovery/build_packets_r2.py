#!/usr/bin/env python3.11
"""Round-2 blinded packet build from CORRECTED_SAMPLE_MANIFEST.jsonl.

Mirrors round-1 scripts/build_packets.py EXACTLY for the surface
construction (imported from that module — byte-frozen, not modified):
the same Packets index, _excerpts, _task_prompt, _scan blinding, and
_order_key scramble.  Packet schemas:
  * rep / diagnostic_s1v41 rows  -> 'R__<tid>' / 'R2D__<tid>' with
    lep_execution_label + exec_labels_to_tids (administrative only,
    never in the prompt surface) — identical schema to round-1 A rows.
  * h0_benign_control rows        -> 'R2H0__<tid>' with the round-1 B4
    schema: 4 pure-benign executions (2 refs + 2 unused reps from
    h0_pair), h0=True, no LEP label.
Blinding scan on EVERY surface; 0 hits or the build aborts.
Writes recovery/out/packets/*.json (isolated from round-1 out/).
Deterministic; no LLM calls.
"""
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
R1 = os.path.abspath(os.path.join(STUDY, 'scripts'))
sys.path.insert(0, R1)
sys.path.insert(0, os.path.abspath(os.path.join(STUDY, '..',
                                                'calibration_v2')))
import build_packets as BP   # round-1 module: Packets, _excerpts, \
                             # _task_prompt, _scan, _order_key (frozen)
import cv2  # noqa: E402

PK = os.path.join(HERE, 'out', 'packets')


def main():
    os.makedirs(PK, exist_ok=True)
    man = [json.loads(l) for l in
           open(os.path.join(HERE, 'CORRECTED_SAMPLE_MANIFEST.jsonl'))
           if l.strip()]
    cpb = {}
    for l in open(os.path.join(STUDY, '..', 'calibration_v2',
                               'ledger', 'checkpoint_pairs.jsonl')):
        r = json.loads(l)
        cpb.setdefault(r['trace_id'], r)
    P = BP.Packets(cpb)
    cells = {c['trace_id']: c for c in cv2.load_cells()[0]}
    _cells = cv2.load_cells()[0]

    def build(rep_row):
        tid = rep_row['trace_id']
        cell = next(c for c in _cells if c['trace_id'] == tid)
        refs = P.refs(tid)
        tids = [tid] + refs
        if len(set(tids)) != 4:
            raise SystemExit(f'r2 packet {tid}: need 4 distinct '
                             f'trace ids (LEP + 3 refs), got {tids}')
        traces = [P.trace(t) for t in tids]
        labels = ['EXEC_1', 'EXEC_2', 'EXEC_3', 'EXEC_4']
        perm = list(range(4))
        random.Random(BP._order_key(rep_row['pack_id'])).shuffle(perm)
        tids_sh = [tids[i] for i in perm]
        traces_sh = [traces[i] for i in perm]
        labels_sh = [labels[i] for i in perm]
        lep_label = labels_sh[perm.index(0)]
        ev = BP._excerpts(traces_sh, labels_sh)
        tp = BP._task_prompt(traces[0], cell['fam'])
        surface = {'task_prompt': tp['text'], 'construct':
                   tp['construct'], 'executions': ev}
        hits = BP._scan(surface)
        if hits:
            raise SystemExit(f'blinding scan hit in {rep_row["pack_id"]}: '
                             f'{hits[:4]}')
        packet = {
            'pack_id': rep_row['pack_id'],
            'cohort': rep_row['cohort'],
            'prompt_version': 'cv2val-ann-v4',
            'task_family_admin': cell['fam'],
            'lep_trace_id_admin': tid,
            'lep_execution_label': lep_label,
            'exec_labels_to_tids': dict(zip(labels_sh, tids_sh)),
            'surface': surface,
            'surface_sha256': hashlib.sha256(
                json.dumps(surface, sort_keys=True).encode()).hexdigest(),
            'h0': False,
            'r2_metadata_admin': {
                'lep_code': rep_row['lep_code'], 'fam': rep_row['fam'],
                'topology': rep_row['topology'], 'mode': rep_row['mode'],
                'pi': rep_row['pi'], 'weight': rep_row['weight'],
                'fixture': rep_row['fixture'],
                'note': 'administrative fields: pi/weight/fixture/LEP '
                        'are used ONLY for post-hoc design-weighted '
                        'scoring and CIs; they are never part of the '
                        'prompt surface (blinding scan covers the '
                        'surface dict only).'},
        }
        return packet

    n = 0
    for row in man:
        if row['cohort'] == 'representative':
            row['pack_id'] = 'R__' + row['trace_id']
            p = build(row)
        elif row['cohort'] == 'diagnostic_s1v41':
            row['pack_id'] = 'R2D__' + row['trace_id']
            p = build(row)
            p['cohort'] = 'R2_diag_s1v41'
        else:  # h0_benign_control
            tid = row['trace_id']
            cell = next(c for c in _cells if c['trace_id'] == tid)
            refs = P.refs(tid)[:2]
            pair = row.get('h0_pair') or []
            tids4 = list(dict.fromkeys(list(refs) + list(pair)))[:4]
            if len(tids4) < 4:
                raise SystemExit(f'r2 H0 {tid}: need 4 distinct benigns, '
                                 f'got {tids4}')
            row['pack_id'] = 'R2H0__' + tid
            labels = ['EXEC_1', 'EXEC_2', 'EXEC_3', 'EXEC_4']
            random.Random(BP._order_key(row['pack_id'])).shuffle(labels)
            traces4 = [P.trace(t) for t in tids4]
            ev = BP._excerpts(traces4, labels)
            tp = BP._task_prompt(traces4[0], cell['fam'])
            surface = {'task_prompt': tp['text'], 'construct':
                       tp['construct'], 'executions': ev,
                       'note': 'four executions of the same task under '
                               'identical benign conditions'}
            hits = BP._scan(surface)
            if hits:
                raise SystemExit(f'r2 H0 blinding hit {tid}: {hits[:4]}')
            p = {
                'pack_id': row['pack_id'], 'cohort': 'R2_h0',
                'prompt_version': 'cv2val-ann-v4',
                'task_family_admin': cell['fam'],
                'lep_trace_id_admin': None,
                'lep_execution_label': None,
                'exec_labels_to_tids': dict(zip(labels, tids4)),
                'surface': surface,
                'surface_sha256': hashlib.sha256(
                    json.dumps(surface, sort_keys=True).encode()).
                hexdigest(),
                'h0': True,
                'r2_metadata_admin': {
                    'lep_code': row['lep_code'], 'fam': row['fam'],
                    'topology': row['topology'], 'mode': row['mode'],
                    'h0_pair': pair,
                    'note': ('H0: pure-benign 4-way; any diverges '
                             'call is a judge-level null-FP '
                             '(corroborating; C4 gate is the '
                             'deterministic B2B control)')},
            }
        json.dump(p, open(os.path.join(PK, p['pack_id'] + '.json'),
                          'w'), indent=1)
        n += 1
    print(f'built {n} r2 packets -> {PK}/ (blinding scan: 0 hits)')


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
