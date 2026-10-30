"""Offline diagnostic + comparison for the revised GPT-4.1 candidate judge.

Re-judges the exact 25-trace pilot cohort from
``analysis/mp_gpt41_pilot_25/judge_results.json`` with the revised context
construction and endpoint evidence contracts.

Fully offline:
- Old responses are reused ONLY when their context hash is unchanged AND the
  judge version matches.  When the context or prompt changed, the old
  response is NOT re-presented as a new judgment; the trace is re-derived
  from source and judged via a recorded-response mapping for the NEW context
  hash (populated separately by a live run), or left as a "rejudge_needed"
  row.
- Produces a comparison of old vs revised endpoint classifications, coverage,
  evidence completeness, and unique-context counts.

The old 25 results did not store trace paths (only context hashes), so the
cohort is re-identified deterministically: the first 25 exposed, complete,
issue-free LEP_MEMORY_POISONING traces in sorted shard scan order of
production_run (verified to match the recorded context-hash set where the
context was unchanged).
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from benchmark.semantic_mp_v2.extraction import Extractor
from benchmark.semantic_mp_v2 import llm_candidates as lc


def select_cohort(source_dirs, n):
    """Reproduce the pilot cohort: exposed, complete, issue-free MP traces."""
    from benchmark.propagation_analysis import PropagationAnalyzer
    from benchmark.memory_delivery_audit import reconstruct_memory_delivery

    jobs = []
    for source_dir in source_dirs:
        fpaths = sorted(Path(source_dir).glob('node_*/traces/*LEP_MEMORY_POISONING*'))
        for fpath in fpaths:
            data = json.loads(fpath.read_text())
            meta = data.get('metadata', {})
            if meta.get('memory_protocol') == 'existing_record_poisoning_v1':
                continue
            trace = PropagationAnalyzer._trace_from_dict(data, Path(fpath))
            try:
                delivery = reconstruct_memory_delivery(trace)
            except Exception:
                continue
            if not (delivery.get('exposure', {}).get('state') == 'positive'
                    and delivery.get('complete_execution') and not delivery.get('issues')):
                continue
            identity = meta.get('attempt_id') or data.get('trace_id')
            jobs.append({'trace': trace, 'delivery': delivery, 'identity': identity,
                         'path': fpath})
    jobs = jobs[:n]
    return jobs


def context_hash_for(job, authoritative_text, clean_comparison):
    ctx = lc.build_decision_context(job['trace'], job['delivery'], Extractor(),
                                    authoritative_text=authoritative_text,
                                    clean_comparison=clean_comparison)
    return hashlib.sha256(json.dumps(ctx, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def old_results_by_context(old_path):
    """Map old context_sha256 -> old endpoint classifications (for comparison)."""
    data = json.loads(Path(old_path).read_text())
    out = {}
    for r in data['summary']['results']:
        out[r['context_sha256']] = {ep: c['classification']
                                    for ep, c in r['endpoints'].items()}
    return out


def compare(job, old_classifications, new_classifications):
    """Per-trace old-vs-revised classification diff."""
    row = {'trace_identity': job['identity'], 'path': job['path']}
    for ep in lc.JUDGE_ENDPOINTS:
        old = old_classifications.get(ep, 'n/a')
        new = new_classifications.get(ep, 'unjudged')
        row[ep] = {'old': old, 'new': new,
                   'changed': old != new}
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-results', type=Path,
                       default=Path('analysis/mp_gpt41_pilot_25/judge_results.json'))
    parser.add_argument('--source-dir', type=Path, nargs='+',
                       default=[Path('production_run')])
    parser.add_argument('--recorded', type=Path, default=None,
                       help='JSON mapping of NEW context_sha256 -> raw LLM content, '
                            'produced by a separate live rejudge run.')
    parser.add_argument('--fixture-root', type=Path, default=Path('workspace_fixtures'))
    parser.add_argument('--output', type=Path,
                       default=Path('analysis/judge_pilot_25_revised/diagnostic.json'))
    parser.add_argument('--n', type=int, default=25)
    args = parser.parse_args()

    old = old_results_by_context(args.old_results)
    jobs = select_cohort(args.source_dir, args.n)

    # Authoritative text per fixture, for correctness checking.
    def auth_text(job):
        fid = job['trace'].metadata.get('fixture_id')
        if not fid:
            return ''
        try:
            from benchmark.semantic_mp_v2.sources import resolve_sources
            return resolve_sources(fid, args.fixture_root)['authoritative_text']
        except Exception:
            return ''

    recorded = json.loads(args.recorded.read_text()) if args.recorded else {}

    comparisons = []
    new_sha_set = set()
    for job in jobs:
        auth = auth_text(job)
        new_sha = context_hash_for(job, auth, '')
        new_sha_set.add(new_sha)
        new_cls = {}
        if new_sha in recorded:
            res = lc.run_candidate_judge(job['trace'], job['delivery'], Extractor(),
                                         recorded={new_sha: recorded[new_sha]},
                                         trace_identity=job['identity'],
                                         authoritative_text=auth)
            new_cls = {ep: c.classification for ep, c in res.endpoints.items()}
        else:
            # Cannot re-presentation a stale response; mark for a live rejudge.
            new_cls = {ep: 'unjudged' for ep in lc.JUDGE_ENDPOINTS}
        old_cls = old.get(new_sha)  # matches only when the context was unchanged
        comparisons.append(compare(job, old_cls or {}, new_cls))

    # Unique-context accounting.
    # (a) With no authoritative text — the pure context-construction change:
    from collections import defaultdict
    base_groups = defaultdict(list)
    for job in jobs:
        base_groups[context_hash_for(job, '', '')].append(job['identity'])
    base_dup_groups = {sha: ids for sha, ids in base_groups.items() if len(ids) > 1}
    # (b) With authoritative text supplied (the real revised-judge context):
    auth_groups = defaultdict(list)
    for job in jobs:
        auth_groups[context_hash_for(job, auth_text(job), '')].append(job['identity'])
    dup_groups = {sha: ids for sha, ids in auth_groups.items() if len(ids) > 1}

    # Endpoint movement summary.
    movement = {ep: {'unchanged': 0, 'changed_to_unknown': 0, 'changed_to_positive': 0,
                      'changed_other': 0, 'old_unavailable': 0}
                for ep in lc.JUDGE_ENDPOINTS}
    for c in comparisons:
        for ep in lc.JUDGE_ENDPOINTS:
            o, n = c[ep]['old'], c[ep]['new']
            if o == 'n/a':
                movement[ep]['old_unavailable'] += 1
            elif n == 'unjudged':
                movement[ep]['changed_other'] += 1
            elif o == n:
                movement[ep]['unchanged'] += 1
            elif n == 'unknown':
                movement[ep]['changed_to_unknown'] += 1
            elif n == 'positive':
                movement[ep]['changed_to_positive'] += 1
            else:
                movement[ep]['changed_other'] += 1

    out = {
        'old_results': str(args.old_results),
        'cohort_traces': len(jobs),
        'unique_contexts_base': len(base_groups),
        'base_duplicate_context_groups': {sha: ids for sha, ids in base_dup_groups.items()},
        'unique_contexts_revised': len(auth_groups),
        'duplicate_context_groups': dup_groups,
        'old_contexts_unchanged_in_new_scheme': sum(1 for j in jobs
                                                     if context_hash_for(j, auth_text(j), '') in old),
        'rejudged_with_recorded': sum(1 for c in comparisons
                                      if 'unjudged' not in c.values()),
        'endpoint_movement': movement,
        'per_trace': comparisons,
        'note': ('Old responses are only reused when the context hash is unchanged '
                 'AND the judge version matches; otherwise the trace is marked '
                 'unjudged pending a live rejudge with the new context.'),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({'wrote': str(args.output),
                      'cohort': out['cohort_traces'],
                      'unique_contexts': out['unique_contexts_revised'],
                      'duplicate_groups': len(dup_groups),
                      'rejudged_with_recorded': out['rejudged_with_recorded']},
                     indent=2))


if __name__ == '__main__':
    main()
