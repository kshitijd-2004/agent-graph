"""Build a small blinded LLM-candidate review set for exposed MP traces.

Output: a JSON review packet with one entry per selected trace:
  - trace identity (attempt id, fixture, topology, task family, exposure state)
  - the LLM's candidate classifications with verbatim-quote grounding status
  - a deliberately blank ``ground_truth`` field, reserved for independent review

No model is called by this script.  When ``--recorded`` is supplied (a JSON
mapping of ``context_sha256 -> raw LLM response content``, produced by a prior
``run_candidate_judge`` run with recording), candidates are re-validated
offline through the same quote-grounding path.  Without ``--recorded`` every
candidate is marked ``not_judged`` so the review set can be handed to
annotators without any LLM-in-the-loop fabrication risk.
"""
import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from benchmark.semantic_mp_v2.llm_candidates import (
    JUDGE_ENDPOINTS, VERSION, _parse_and_validate,
)
from benchmark.semantic_mp_v2.extraction import Extractor


def _candidates_for(row, recorded):
    """Re-validate recorded LLM responses for this trace, or return placeholders.

    A recorded mapping is keyed on the ``context_sha256`` stored in the
    candidate result; the row must carry a prior ``candidate`` payload for
    the lookup to work.  Without it, placeholders are emitted so the review
    set is never built from a mismatched context.
    """
    prior = (row.get('candidate') or {})
    if recorded is not None:
        sha = prior.get('context_sha256')
        context = prior.get('context')
        if sha and sha in recorded and context is not None:
            out = _parse_and_validate(recorded[sha], context, JUDGE_ENDPOINTS)
            return {ep: asdict(cand) for ep, cand in out.items()}
    return {ep: {'classification': 'unknown', 'rationale': 'not_judged_no_model_call',
                 'quotes': [], 'quote_grounded': False, 'demoted': False}
            for ep in JUDGE_ENDPOINTS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
                        help='Directory to write the review set into.')
    parser.add_argument('--traces', type=Path, nargs='+', required=True,
                        help='semantic_by_trace.jsonl file(s) from a production run.')
    parser.add_argument('--n', type=int, default=8, help='max entries in the set.')
    parser.add_argument('--recorded', type=Path,
                        help='Optional JSON context_sha256 -> raw LLM response content. '
                             'Supplied for offline replay only; never triggers a model call.')
    parser.add_argument('--exposure-only', action='store_true',
                        help='Restrict the set to traces whose delivery exposure is positive.')
    args = parser.parse_args(argv)

    rows = []
    for path in args.traces:
        for line in Path(path).read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get('status') != 'measured':
                continue
            exposure = (row.get('measurement') or {}).get('delivery', {}).get('exposure', {})
            if args.exposure_only and exposure.get('state') != 'positive':
                continue
            rows.append(row)
    rows.sort(key=lambda r: hashlib.sha256((r.get('trace_id') or '').encode()).hexdigest())
    rows = rows[:args.n]

    recorded = None
    if args.recorded is not None:
        recorded = json.loads(Path(args.recorded).read_text())

    extractor = Extractor()  # deterministic only; used for context hashing
    packet = []
    for row in rows:
        trace_id = row.get('trace_id')
        metadata = row.get('metadata') or {}
        measurement = row.get('measurement') or {}
        delivery = measurement.get('delivery') or {}
        entry = {
            'entry_id': hashlib.sha256((trace_id or '').encode()).hexdigest()[:16],
            'trace_id': trace_id,
            'attempt_id': row.get('attempt_id'),
            'context': {
                'fixture_id': metadata.get('fixture_id'),
                'task_family': metadata.get('task_family'),
                'topology': metadata.get('topology'),
                'exposure_state': delivery.get('exposure', {}).get('state'),
            },
            'candidates': _candidates_for(row, recorded),
            'ground_truth': None,  # filled only by independent annotation
            'version': VERSION,
        }
        packet.append(entry)

    args.output.mkdir(parents=True, exist_ok=True)
    out_path = args.output / 'blinded_review_set.json'
    out_path.write_text(json.dumps({
        'version': VERSION,
        'independent_ground_truth': False,
        'note': ('LLM candidates are proposals only, not ground truth. '
                 'Every entry carries ground_truth=null until independent '
                 'annotation is completed.'),
        'judged_from_recorded': recorded is not None,
        'entries': packet,
    }, indent=2))
    print(json.dumps({'written': str(out_path), 'entries': len(packet),
                      'judged_from_recorded': recorded is not None}, indent=2))


if __name__ == '__main__':
    main()
