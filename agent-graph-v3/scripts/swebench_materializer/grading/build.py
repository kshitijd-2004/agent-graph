"""Reproducibly attach reviewed patch concepts; never synthesize title keywords."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from evaluators.task_evaluators.patch_review import VERSION


def patch_digest(patch):
    return hashlib.sha256(patch.encode()).hexdigest()


def load_reviewed_rubrics():
    rows = json.loads(Path(__file__).with_name('reviewed_rubrics.json').read_text())
    by_index = {}
    for row in rows:
        index = row['selection_index']
        if index in by_index:
            raise ValueError(f'Duplicate reviewed rubric index: {index}')
        by_index[index] = row
    return by_index


ROWS = load_reviewed_rubrics()


def _natural_alias(alias: str) -> str:
    """Convert a code identifier to a natural-language form that matches how
    authors refer to it in prose.  Splits CamelCase boundaries and replaces
    dots/underscores with spaces, mirroring patch_review.normalize."""
    import re as _re
    # Split CamelCase: 'PythonCodePrinter' -> 'Python Code Printer'
    s = _re.sub(r'([a-z])([A-Z])', r'\1 \2', alias)
    s = _re.sub(r'([A-Z]{2,})([A-Z][a-z])', r'\1 \2', s)
    # Replace dots and underscores with spaces (mirrors normalize)
    s = _re.sub(r'[._]+', ' ', s)
    return s.strip()


def _split_dotted_identifier(alias: str) -> list[str]:
    """Split a dotted class.method identifier into standalone aliases so
    each component matches the normalized text independently.

    'OrderedSet.__reversed__' → ['OrderedSet', '__reversed__']
    'PythonCodePrinter' stays as-is (no dots).
    """
    import re as _re
    parts = alias.split('.')
    result = []
    for part in parts:
        p = part.strip()
        if p and p not in ('__init__', '__class__'):
            result.append(p)
    # Also add compound CamelCase prefix (everything before last dot)
    if '.' in alias:
        prefix = alias.rsplit('.', 1)[0]
        if prefix not in result:
            result.append(prefix)
    return result


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
    identity = row['identity']
    if (identity['fixture_id'] != draft['fixture_id']
            or identity['instance_id'] != draft['source']['instance_id']
            or identity['gold_patch_sha256'] != patch_digest(draft['oracle']['gold_patch'])):
        raise ValueError('Frozen rubric identity does not match draft fixture')
    if row['selection_index'] != index:
        raise ValueError('Frozen rubric selection index mismatch')
    if result['source']['instance_id'] != draft['source']['instance_id']:
        raise ValueError('Rubric/source mismatch')
    patch = draft['oracle']['gold_patch']
    locations = [line[6:] for line in patch.splitlines() if line.startswith('+++ b/')]
    # A filename or qualified symbol is acceptable; no exact patch text needed.
    aliases = locations + row['locus']
    aliases += [Path(p).name for p in locations if Path(p).name != '__init__.py']
    # For __init__.py files, also add parent-directory aliases so paraphrases
    # like "In fields/__init__.py" resolve without leading directories.
    for p in locations:
        if Path(p).name == '__init__.py' and p != '__init__.py':
            parts = Path(p).parts
            for n in range(1, len(parts)):
                suffix = str(Path(*parts[-n - 1:]))
                if suffix not in aliases:
                    aliases.append(suffix)
    # Add natural-language aliases for CamelCase code identifiers.
    # Authors split identifiers into words in prose (e.g. "PythonCodePrinter"
    # → "python code printer"), but normalize() only replaces _ and - with
    # spaces, not CamelCase boundaries.
    natural_aliases = []
    for alias in aliases:
        nat = _natural_alias(alias)
        if nat and nat not in aliases:
            natural_aliases.append(nat)
        # For dotted identifiers (Class.method), also add the bare class
        # name so "OrderedSet.__reversed__" matches texts that only say
        # "OrderedSet lacks ..." without mentioning the method name.
        for split_alias in _split_dotted_identifier(alias):
            if split_alias not in aliases and split_alias not in natural_aliases:
                natural_aliases.append(split_alias)
    aliases.extend(natural_aliases)
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
