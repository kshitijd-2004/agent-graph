"""Deterministic, evaluator-only concept rubrics for reviewed SWE-bench fixes.

This is a bounded lexical concept matcher, not an unrestricted semantic judge.
A location alone never earns credit. Every required causal/change concept must
appear within one local explanation window. Legacy fixtures retain their grader.
"""
from __future__ import annotations

import re
import unicodedata


VERSION = 'patch-concepts-v1'


def normalize(text: str) -> str:
    text = unicodedata.normalize('NFKC', text).lower()
    text = text.replace('’', "'").replace('–', '-').replace('—', '-')
    return re.sub(r'\s+', ' ', re.sub(r'[_-]+', ' ', text)).strip()


def rubric_errors(rubric: dict) -> list[str]:
    if not isinstance(rubric, dict):
        return ['patch rubric must be an object']
    errors = []
    if rubric.get('version') != VERSION:
        errors.append('unsupported patch rubric version')
    locations = rubric.get('location_any')
    concepts = rubric.get('all_of')
    rejects = rubric.get('reject_any', [])
    if not isinstance(locations, list) or not locations or not all(
        isinstance(value, str) and value.strip() for value in locations
    ):
        errors.append('patch rubric requires nonempty location aliases')
    if not isinstance(concepts, list) or len(concepts) < 2:
        errors.append('patch rubric requires at least two independent concepts')
        concepts = []
    if not isinstance(rejects, list):
        errors.append('invalid contradiction patterns')
        rejects = []
    patch_hash = rubric.get('gold_patch_sha256')
    if not isinstance(patch_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', patch_hash):
        errors.append('missing gold patch provenance')
    for pattern in concepts + rejects:
        try:
            if re.compile(pattern).search(''):
                errors.append('empty-matching concept pattern')
        except (re.error, TypeError):
            errors.append('invalid concept pattern')
    return errors


def match_patch_review(text: str, rubric: dict, task_prompt: str = '') -> dict:
    errors = rubric_errors(rubric)
    if errors:
        return {'matched': False, 'errors': errors}
    answer = normalize(text)
    # Verbatim copied issue material is context, not evidence of diagnosis.
    # Remove complete prompt lines (including the title), not individual terms.
    for line in sorted(task_prompt.splitlines(), key=len, reverse=True):
        copied = normalize(line)
        if len(copied) >= 20:
            answer = answer.replace(copied, ' ')
    location = next((value for value in rubric['location_any']
                     if re.search(r'(?<!\w)' + re.escape(normalize(value)) + r'(?!\w)', answer)), None)
    contradictions = [p for p in rubric.get('reject_any', []) if re.search(p, answer)]
    # An explicit assertion that the reported bug is absent must not be credited
    # for merely quoting the explanation it rejects.
    denied = re.search(r'\b(?:no (?:actual |such |reported )?(?:bug|defect|issue) (?:exists|is present)|'
                       r'no (?:fix|changes?|correction) (?:is|are) (?:needed|required))\b', answer)
    matches = [list(re.finditer(pattern, answer)) for pattern in rubric['all_of']]
    # Require one explanation window, preventing unrelated paragraphs from
    # assembling the answer out of scattered keywords. Long reports are fine.
    coherent = any(all(any(start <= m.start() and m.end() <= start + 1200 for m in group)
                       for group in matches)
                   for group in matches for match in group for start in [match.start()])
    return {
        'matched': bool(location and coherent and not contradictions and not denied),
        'location': location,
        'concepts_found': [bool(group) for group in matches],
        'coherent_explanation': coherent,
        'contradiction': bool(contradictions or denied),
    }
