"""Export the reviewed repair using the preserved pre-repair identity audit.

Does not write workspaces or manifests. The baseline artifact pins the original
cohort and prevents a later selection edit from silently relabeling reviews.
"""
import json
from pathlib import Path

from scripts.swebench_materializer.grading.expanded_review import REVIEWS, ORIGINAL_CONCEPT_REPAIRS

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'scripts/swebench_materializer'


def main():
    baseline = json.loads((ROOT / 'analysis/swebench_300_repair/before.json').read_text())['fixtures']
    reviewed = json.loads((BASE / 'grading/reviewed_sources.json').read_text())
    selection = json.loads((BASE / 'selection.json').read_text())
    frozen, entries = [], []
    assert set(REVIEWS) == {r['instance_id'] for r in baseline if r['selection_index'] > 100}
    assert set(ORIGINAL_CONCEPT_REPAIRS) <= {
        r['instance_id'] for r in baseline if r['selection_index'] <= 100
    }
    for record in baseline:
        index = record['selection_index']
        identity = {k: record[k] for k in ('instance_id', 'fixture_id', 'gold_patch_sha256')}
        assert reviewed[str(index)] == identity
        draft = json.loads((BASE / 'draft_manifests' / identity['fixture_id'] / 'manifest.json').read_text())
        from hashlib import sha256
        assert sha256(draft['oracle']['gold_patch'].encode()).hexdigest() == identity['gold_patch_sha256']
        assert draft['source']['instance_id'] == identity['instance_id']
        assert draft['provenance']['selection_index'] == index
        if index <= 100:
            row = dict(record['curated_row'])
            if identity['instance_id'] in ORIGINAL_CONCEPT_REPAIRS:
                row['concepts'] = ORIGINAL_CONCEPT_REPAIRS[identity['instance_id']]
            entry = record['selection_entry']
        else:
            row = dict(REVIEWS[identity['instance_id']])
            assert row.pop('instance_id') == identity['instance_id']
            entry = dict(selection_index=index, instance_id=identity['instance_id'], repo=record['repo'],
                         source_files_changed=draft['oracle']['source_files_changed'],
                         fail_to_pass=draft['oracle']['fail_to_pass'], pass_to_pass=draft['oracle']['pass_to_pass'])
        row.update(identity=identity, selection_index=index)
        frozen.append(row)
        entries.append(entry)
    (BASE / 'grading/reviewed_rubrics.json').write_text(json.dumps(frozen, indent=2) + '\n')
    selection['fixtures'] = entries
    selection['n_selected'] = len(entries)
    from collections import Counter
    selection['selection_policy']['repository_allocation'] = dict(Counter(e['repo'] for e in entries))
    (BASE / 'selection.json').write_text(json.dumps(selection, indent=2) + '\n')
    print('Pinned 300 rubrics and restored the frozen draft selection; no workspaces written.')


if __name__ == '__main__':
    main()
