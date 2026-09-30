"""Read-only identity and pre-fix byte audit, independent of grading outcomes."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from scripts.swebench_materializer.grading.curated import ROWS
from scripts.swebench_materializer.materialize_swebench_fixtures import load_verified

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'scripts/swebench_materializer'


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def identity(manifest):
    return dict(fixture_id=manifest['fixture_id'], **manifest['source'],
                selection_index=manifest['provenance']['selection_index'])


def audit():
    official = load_verified()
    selection = json.loads((BASE / 'selection.json').read_text())['fixtures']
    selected = {r['selection_index']: r for r in selection}
    assert len(selected) == len(selection) == 300 and set(selected) == set(range(1, 301))
    reviewed = json.loads((BASE / 'grading/reviewed_sources.json').read_text())
    drafts = {}
    for path in (BASE / 'draft_manifests').glob('*/manifest.json'):
        m = json.loads(path.read_text())
        assert m['fixture_id'] not in drafts
        drafts[m['fixture_id']] = m
    records = []
    for d in sorted(drafts.values(), key=lambda d: d['provenance']['selection_index']):
        path = ROOT / 'workspace_fixtures_v3' / d['fixture_id'] / 'manifest.json'
        exists = path.exists()
        m = json.loads(path.read_text()) if exists else d
        i = m['provenance']['selection_index']
        o = official[m['source']['instance_id']]
        patch_sha = sha(o['patch'])
        expected = dict(fixture_id=m['fixture_id'], instance_id=o['instance_id'], gold_patch_sha256=patch_sha)
        issue_text = m['task_prompt'].split('Issue:\n', 1)[1].strip()
        official_text = o['problem_statement'].strip()
        # Added drafts wrap the complete upstream issue with its title and a
        # Description label. Compare the entire payload, not a substring/title.
        wrapped_text = official_text.splitlines()[0].strip() + '\nDescription\n\n' + official_text
        failures = []
        if not exists:
            failures.append('missing_materialized_manifest')
        for label, ok in ({
            'draft_identity': identity(d) == {k: identity(m)[k] for k in identity(d)},
            'directory_identity': path.parent.name == m['fixture_id'],
            'repo': m['source']['repo'] == o['repo'],
            'base_commit': m['source']['base_commit'] == o['base_commit'] == m['materialization']['base_commit'],
            'pre_fix_provenance': m['materialization']['workspace_state'] == 'pre_fix_base_commit',
            'prompt': m['task_prompt'] == d['task_prompt'] and issue_text in (official_text, wrapped_text),
            'required_files': m['required_files'] == d['required_files'] == m['materialization']['requested_files'],
            'gold_patch': sha(d['oracle']['gold_patch']) == patch_sha,
            'patch_applies_provenance': m['materialization']['gold_patch_applies_cleanly'],
        } if exists else {}).items():
            if not ok:
                failures.append(label)
        files = {}
        for name in m['required_files'] if exists else []:
            p = subprocess.run(['git', '-C', str(ROOT / '.cache/agentprop-swebench' / o['repo']),
                                'show', f"{o['base_commit']}:{name}"], capture_output=True)
            context = m['materialization']['source_context'][name]
            original = p.stdout
            expected_bytes = original
            if context['strategy'] != 'full_file':
                lines = original.decode().splitlines(keepends=True)
                expected_bytes = ''.join(''.join(lines[a-1:b]) for a, b in context['included_ranges']).encode()
                if sha(original) != context['original_sha256']:
                    failures.append('original_source_hash:' + name)
            actual = (path.parent / name).read_bytes()
            ok = p.returncode == 0 and actual == expected_bytes
            files[name] = dict(verified=ok, upstream_sha256=sha(original), workspace_sha256=sha(actual), context=context)
            if not ok:
                failures.append('pre_fix_bytes:' + name)
        row = ROWS[i]
        # Legacy generated rows have no identity. Reproduce their generating
        # algorithm against the selection entry to establish origin, not trust it.
        origin = row.get('identity')
        origin_proven = origin is not None
        if origin is None and i > 100:
            from scripts.swebench_materializer.grading.expand_to_300 import (
                derive_locus, derive_concepts, derive_explanation, derive_paraphrase, derive_incorrect)
            so = official[selected[i]['instance_id']]
            locus = derive_locus(so['patch'], selected[i]['source_files_changed'][0])
            generated = dict(locus=locus[:5], concepts=derive_concepts(so['patch'])[:2],
                             gold=derive_explanation(so['patch'], locus),
                             paraphrase=derive_paraphrase(so['patch'], locus),
                             incorrect=derive_incorrect(so['patch'], locus))
            origin_proven = generated == row
            origin = dict(instance_id=so['instance_id'], gold_patch_sha256=sha(so['patch'])) if origin_proven else None
        if origin is None and i <= 100:
            origin = reviewed[str(i)]
            origin_proven = all(issue['grading']['explanation'] == row['gold'] for issue in m['required_issues'])
        grading_errors = []
        if selected[i]['instance_id'] != o['instance_id']:
            grading_errors.append('selection_instance_mismatch')
        if reviewed[str(i)] != expected:
            grading_errors.append('reviewed_identity_mismatch')
        if not origin_proven or origin['instance_id'] != o['instance_id'] or origin['gold_patch_sha256'] != patch_sha:
            grading_errors.append('curated_row_identity_mismatch')
        for issue in m['required_issues'] if exists else []:
            g = issue.get('grading', {})
            if g.get('source_instance_id') != o['instance_id'] or g.get('gold_patch_sha256') != patch_sha:
                grading_errors.append('attached_rubric_provenance_mismatch')
        records.append(dict(selection_index=i, **expected, repo=o['repo'], base_commit=o['base_commit'],
                            selection_entry=selected[i], reviewed_entry=reviewed[str(i)],
                            curated_row=row, curated_origin=origin, curated_origin_proven=origin_proven,
                            draft_identity=identity(d), materialized_identity=identity(m) if exists else None,
                            materialized_rubrics=[v.get('grading') for v in m['required_issues']],
                            materialization_failures=failures, grading_mapping_failures=grading_errors,
                            all_identities_agree=not grading_errors and not failures, files=files,
                            manifest_sha256=sha(path.read_bytes()) if exists else None))
    assert len(records) == 300 and len({r['instance_id'] for r in records}) == 300
    bad = [r['selection_index'] for r in records if r['grading_mapping_failures']]
    return dict(summary=dict(total=300, first_divergence=min(bad) if bad else None,
                             mapping_corrupted=len(bad), materialization_failures=sum(bool(r['materialization_failures']) for r in records),
                             grading_only_failures=sum(bool(r['grading_mapping_failures']) and not r['materialization_failures'] for r in records),
                             origins_unproven=[r['selection_index'] for r in records if not r['curated_origin_proven']]), fixtures=records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = audit()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['summary'], indent=2))
