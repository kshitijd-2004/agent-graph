"""Reproducibly attach reviewed patch concepts; never synthesize title keywords."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from scripts.swebench_materializer.grading.curated import ROWS
from evaluators.task_evaluators.patch_review import VERSION


def patch_digest(patch):
    return hashlib.sha256(patch.encode()).hexdigest()


def with_grading(manifest, draft):
    result = deepcopy(manifest)
    index = draft['provenance']['selection_index']
    reviewed = json.loads(Path(__file__).with_name('reviewed_sources.json').read_text()).get(str(index))
    if reviewed is None:
        raise ValueError('No reviewed grading rubric for this fixture; do not synthesize title grading')
    if (draft['fixture_id'] != reviewed['fixture_id']
            or draft['source']['instance_id'] != reviewed['instance_id']
            or patch_digest(draft['oracle']['gold_patch']) != reviewed['gold_patch_sha256']):
        raise ValueError('Gold patch or source changed; grading rubric needs review')
    row = ROWS[index]
    if result['source']['instance_id'] != draft['source']['instance_id']:
        raise ValueError('Rubric/source mismatch')
    patch = draft['oracle']['gold_patch']
    locations = [line[6:] for line in patch.splitlines() if line.startswith('+++ b/')]
    # A filename or qualified symbol is acceptable; no exact patch text needed.
    aliases = locations + row['locus']
    aliases += [Path(p).name for p in locations if Path(p).name != '__init__.py']
    aliases = [a for a in dict.fromkeys(aliases)
               if a not in ('fit', 'run', 'draw', 'wrapper', 'transform', '_f',
                            '__init__', '__call__', '__iter__', '__array__', '__getattr__', '__add__')]
    # Keep every existing source anchor: LEP omission operators consume these
    # functions. All anchors describe the same reported fix and use its complete
    # rubric, rather than awarding separate credit for individual patch hunks.
    issues = deepcopy(draft['required_issues'])
    rubric = dict(version=VERSION, gold_patch_sha256=patch_digest(patch),
                  source_instance_id=draft['source']['instance_id'],
                  changed_files=locations, location_any=aliases,
                  all_of=row['concepts'], reject_any=row.get('reject', []),
                  explanation=row['gold'])
    for issue in issues:
        issue.pop('keywords', None)
        issue['grading'] = deepcopy(rubric)
    result['required_issues'] = issues
    result['success_criteria'] = deepcopy(draft['success_criteria'])
    return result
