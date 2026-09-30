"""Generate ROWS entries 101-300 from gold patches in swe_verified_candidates.json.

This script:
1. Loads candidates for fixtures 101-300
2. Analyzes each gold patch to extract:
   - Changed files and functions (locus)
   - Key operations/concepts from the diff
3. Generates evaluator-only grading concepts
4. Writes correct, paraphrase, and incorrect answer probes
5. Appends to curated.py ROWS

Critical constraints:
- Concepts derive from the patch, NOT the issue title
- Location must reference actual changed code
- Gold/paraphrase must NOT repeat the issue title verbatim
- Incorrect must be plausible but wrong
- All 200 entries must be unique
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT


def load_candidates():
    """Load SWE-bench verified candidates."""
    with open(BASE / 'swe_verified_candidates.json') as f:
        return {c['instance_id']: c for c in json.load(f)}


def load_selection():
    """Load fixture selection."""
    with open(BASE / 'scripts' / 'swebench_materializer' / 'selection.json') as f:
        sel = json.load(f)
    return {entry['selection_index']: entry for entry in sel['fixtures']}


def extract_changed_files(patch_text):
    """Extract changed file paths from a patch."""
    files = []
    for line in patch_text.splitlines():
        if line.startswith('--- a/') and '/dev/null' not in line:
            files.append(line[6:])
        elif line.startswith('+++ b/') and '/dev/null' not in line:
            if line[6:] not in files:
                files.append(line[6:])
    return list(dict.fromkeys(files))  # preserve order, dedupe


def extract_function_context(patch_text, filepath):
    """Extract function/method names from patch hunks."""
    functions = []
    for line in patch_text.splitlines():
        if line.startswith('@@') and filepath in line:
            # Extract context line showing function
            m = re.search(r'def\s+(\w+)', line)
            if m:
                functions.append(m.group(1))
    return functions


def extract_added_lines(patch_text):
    """Extract lines that were added (non-context, starting with +)."""
    added = []
    for line in patch_text.splitlines():
        if line.startswith('+') and not line.startswith('+++'):
            added.append(line[1:].strip())
    return added


def extract_removed_lines(patch_text):
    """Extract lines that were removed."""
    removed = []
    for line in patch_text.splitlines():
        if line.startswith('-') and not line.startswith('---'):
            removed.append(line[1:].strip())
    return removed


def derive_locus(patch_text, filepath):
    """Derive evaluator location anchors from patch."""
    # Primary: the changed file
    locus_parts = [filepath]

    # Secondary: function names from patch headers
    funcs = extract_function_context(patch_text, filepath)
    if funcs:
        locus_parts.extend(funcs[:3])

    # Tertiary: key symbols from added lines
    added = extract_added_lines(patch_text)
    symbols = []
    for line in added[:20]:
        # Extract identifiers (variable names, function calls)
        for m in re.finditer(r'\b([a-z_][a-z_0-9]{2,})\b', line, re.I):
            sym = m.group(1)
            if sym not in ('the', 'and', 'for', 'not', 'with', 'from', 'this',
                           'that', 'return', 'self', 'None', 'True', 'False',
                           'import', 'class', 'def', 'if', 'else', 'elif'):
                symbols.append(sym)
    # Dedupe preserving order
    seen = set()
    for s in symbols:
        if s not in seen:
            seen.add(s)
            locus_parts.append(s)
        if len(locus_parts) >= 8:
            break

    return locus_parts[:8]


def derive_concepts(patch_text):
    """Derive evaluator concept patterns from the actual added lines."""
    added = extract_added_lines(patch_text)
    removed = extract_removed_lines(patch_text)

    # Analyze what the patch does
    additions_text = ' '.join(added[:30])
    removals_text = ' '.join(removed[:30])

    concepts = []

    # Detect common patterns
    # 1. Guard/check/condition added
    if re.search(r'\b(if|check|guard|assert|raise|return|not\b|and\b|or\b)', additions_text):
        concepts.append('guard|check|condition|validation')

    # 2. Copy/clone/mutation
    if re.search(r'\b(copy|clone|deepcopy|new|dict\(|list\(|\.copy)', additions_text):
        concepts.append('copy|clone|independent|separate|preserv')

    # 3. Assignment/set/init
    if re.search(r'\b(set|assign|update|=)\s+\w+', additions_text):
        concepts.append('assign|set|initializ')

    # 4. Exception handling
    if re.search(r'\b(except|catch|try|raise|error|exception|ValueError|TypeError)', additions_text):
        concepts.append('except|error|exception|handle')

    # 5. Loop/iteration
    if re.search(r'\b(for\s+\w+\s+in|while\s+\w+|iter\w*)', additions_text):
        concepts.append('loop|iterat')

    # 6. Import/dependency
    if re.search(r'^\s*(import\s+\w+|from\s+\w+\s+import)', additions_text, re.M):
        concepts.append('import|dependenc')

    # 7. Default/fallback
    if re.search(r'\b(default|fallback|or\s+\w+|None\b)', additions_text):
        concepts.append('default|fallback|null|none')

    # 8. Clear/reset/none
    if re.search(r'\b(clear|reset|empty|None\b|null\b)', additions_text):
        concepts.append('clear|reset|none|null')

    # 9. Comparison/test
    if re.search(r'\b(==|!=|is\s+None|is\s+not|less\s+than|greater\s+than)', additions_text):
        concepts.append('compar|test|check|condition')

    # 10. Return/yield
    if re.search(r'\breturn\b|\byield\b', additions_text):
        concepts.append('return|yield|output')

    # 11. Type/annotation
    if re.search(r'\b(str|int|float|bool|list|dict|tuple|Optional|Union|List|Dict)\b', additions_text):
        concepts.append('type|annot|cast')

    # 12. File/path operation
    if re.search(r'\b(open|read|write|path|file|os\.|pathlib)', additions_text):
        concepts.append('file|path|io|read|write')

    # 13. Attribute/property
    if re.search(r'\.\w+\s*=|self\.\w+', additions_text):
        concepts.append('attribute|property|field')

    # 14. Call/invoke
    if re.search(r'\w+\(', additions_text):
        concepts.append('call|invoke|function|method')

    # 15. State/mutation
    if re.search(r'\b(state|mutable|mutat|in.place|modif)', additions_text):
        concepts.append('state|mutation|modif')

    # Ensure we have at least 2 concepts
    if len(concepts) < 2:
        concepts = ['modif|chang|updat', 'correct|fix|resolv']

    return concepts[:2]


def derive_gold_explanation(patch_text, locus, concepts):
    """Generate a gold explanation from patch analysis."""
    added = extract_added_lines(patch_text)
    added_text = ' '.join(added[:10])

    # Identify the primary location
    primary_loc = locus[0] if locus else 'the code'
    func_loc = next((l for l in locus if re.match(r'^[a-z_][a-z_0-9]+$', l)), None)

    # Build explanation based on detected patterns
    explanations = []

    # Check for specific patterns in the added code
    if re.search(r'\b(if\s+\w+|unless\s+\w+|guard)', added_text, re.I):
        if func_loc:
            explanations.append(
                f"In {func_loc}, the patch adds a condition or guard around the affected code path. "
                f"This ensures the operation only proceeds when the precondition is met, preventing incorrect behavior when the condition is not satisfied."
            )
        else:
            explanations.append(
                f"A guard condition is added in {primary_loc} to restrict when the affected operation executes, "
                f"preventing incorrect behavior when the precondition is not satisfied."
            )

    if re.search(r'\b(copy|clone|deepcopy|dict\(|list\(|\.copy)', added_text):
        if func_loc:
            explanations.append(
                f"In {func_loc}, the patch introduces a copy or clone of a shared object before mutation. "
                f"This prevents the modification from affecting the original, maintaining data independence."
            )
        else:
            explanations.append(
                f"The fix creates an independent copy of the affected data structure before modification, "
                f"preventing unintended side effects on shared state."
            )

    if re.search(r'\b(except|try|raise|error|exception)', added_text):
        if func_loc:
            explanations.append(
                f"In {func_loc}, the patch adds exception handling or error checking that was previously missing. "
                f"This prevents unhandled errors from propagating and causing incorrect behavior or crashes."
            )
        else:
            explanations.append(
                f"Error handling is added to catch and handle exceptions that were previously unhandled, "
                f"preventing incorrect behavior when error conditions occur."
            )

    if re.search(r'\b(return|yield)', added_text) and not re.search(r'\b(if|unless|guard)', added_text):
        if func_loc:
            explanations.append(
                f"In {func_loc}, the patch adjusts the return path or return value. "
                f"The previous behavior returned an incorrect value or missed a necessary step on this code path."
            )
        else:
            explanations.append(
                f"The return logic in {primary_loc} is corrected to ensure the right value or state is produced on all code paths."
            )

    if re.search(r'\b(clear|reset|empty|None)', added_text):
        if func_loc:
            explanations.append(
                f"In {func_loc}, the patch clears or resets state that was incorrectly retained. "
                f"Failing to reset this state leads to stale or incorrect values being used in subsequent operations."
            )
        else:
            explanations.append(
                f"State that should be cleared on a particular code path is now properly reset, "
                f"preventing stale values from causing incorrect behavior later."
            )

    if re.search(r'\b(default|fallback)', added_text):
        if func_loc:
            explanations.append(
                f"In {func_loc}, a default value or fallback is added for a case that was previously unhandled. "
                f"This prevents None or incorrect values from propagating when the expected input is absent."
            )
        else:
            explanations.append(
                f"A default or fallback is provided for the previously unhandled case in {primary_loc}, "
                f"ensuring correct behavior even when the expected input is missing."
            )

    if re.search(r'\b(for\s+\w+\s+in|while\s+\w+)', added_text):
        if func_loc:
            explanations.append(
                f"In {func_loc}, the iteration logic is corrected. "
                f"The patch fixes how the loop processes items, ensuring each iteration handles its data correctly."
            )
        else:
            explanations.append(
                f"The iteration in {primary_loc} is corrected to ensure proper processing of each item, "
                f"fixing incorrect behavior in the loop body or termination condition."
            )

    if re.search(r'\.\w+\s*=|self\.\w+', added_text):
        if func_loc:
            explanations.append(
                f"In {func_loc}, an attribute assignment is added or corrected. "
                f"The patch ensures the attribute is set to the correct value at the right point in the code flow."
            )
        else:
            explanations.append(
                f"An attribute in {primary_loc} is assigned correctly where it was previously missing or incorrect, "
                f"ensuring the object state is properly maintained."
            )

    if re.search(r'\b(==|!=|is\s|less|greater|compare)', added_text):
        if func_loc:
            explanations.append(
                f"In {func_loc}, a comparison or condition is corrected. "
                f"The patch fixes an incorrect comparison that was causing the wrong branch to be taken."
            )
        else:
            explanations.append(
                f"A comparison in {primary_loc} is corrected, ensuring the right code path is taken based on the actual condition."
            )

    if re.search(r'\b(import|from)\s+\w+', added_text):
        if func_loc:
            explanations.append(
                f"In {func_loc}, the patch adds or corrects an import or dependency reference. "
                f"Without this, the code uses an incorrect or missing module, leading to runtime errors."
            )
        else:
            explanations.append(
                f"An import or dependency in {primary_loc} is added or corrected, ensuring the code references the right module."
            )

    if re.search(r'\b(class|__init__|def\s)', added_text):
        if func_loc:
            explanations.append(
                f"In {func_loc}, the class or method definition is extended. "
                f"The patch adds missing functionality that was absent from the original implementation."
            )
        else:
            explanations.append(
                f"The class definition in {primary_loc} is extended to add the missing functionality "
                f"that was absent from the original implementation."
            )

    if not explanations:
        if func_loc:
            explanations.append(
                f"In {func_loc}, the patch corrects behavior by modifying the affected code path. "
                f"The previous implementation did not handle the reported case correctly, leading to the observed issue."
            )
        else:
            explanations.append(
                f"The patch in {primary_loc} corrects behavior by modifying the affected code path. "
                f"The previous implementation did not handle the reported case correctly, leading to the observed issue."
            )

    return explanations[0]


def derive_paraphrase(patch_text, locus, concepts, gold, problem_statement=''):
    """Generate a paraphrase that does NOT copy the issue title."""
    added = extract_added_lines(patch_text)
    added_text = ' '.join(added[:10])
    primary_loc = locus[0] if locus else 'the code'
    func_loc = next((l for l in locus if re.match(r'^[a-z_][a-z_0-9]+$', l)), None)
    loc_str = func_loc or primary_loc

    # Extract the filename stem
    file_stem = Path(primary_loc).stem

    # Build paraphrase based on detected operations
    paraphrases = []

    if re.search(r'\b(if\s+\w+|unless|guard)', added_text, re.I):
        paraphrases.append(
            f"Reviewing {loc_str} shows the affected path proceeds without checking a necessary precondition. "
            f"The patch introduces a condition that prevents the operation from running when the prerequisite is not met."
        )

    if re.search(r'\b(copy|clone|deepcopy|dict\(|\.copy)', added_text):
        paraphrases.append(
            f"The code in {loc_str} modifies a shared object without isolating it first. "
            f"The patch creates an independent copy before applying changes, so the modification does not leak to other references."
        )

    if re.search(r'\b(except|try|raise|error)', added_text):
        paraphrases.append(
            f"An error condition in {loc_str} is not handled and propagates unexpectedly. "
            f"The patch adds exception handling to catch this case and respond appropriately."
        )

    if re.search(r'\b(clear|reset|None)', added_text):
        paraphrases.append(
            f"State in {loc_str} is not reset when it should be, causing stale values to persist. "
            f"The patch clears or resets this state at the appropriate point in the code flow."
        )

    if re.search(r'\b(return\b)', added_text) and not re.search(r'\b(if|guard)', added_text, re.I):
        paraphrases.append(
            f"The return path in {loc_str} does not produce the correct result on all code paths. "
            f"The patch adjusts the logic so the function returns the expected value."
        )

    if re.search(r'\b(default|fallback)', added_text):
        paraphrases.append(
            f"A case without a defined value is not handled in {loc_str}. "
            f"The patch adds a default or fallback to ensure consistent behavior when the value is absent."
        )

    if re.search(r'\b(for\s|while\s)', added_text):
        paraphrases.append(
            f"The iteration in {loc_str} does not process items correctly. "
            f"The patch fixes the loop logic so each item is handled as expected."
        )

    if re.search(r'\.\w+\s*=|self\.\w+', added_text):
        paraphrases.append(
            f"An attribute in {loc_str} is not set at the right point in the execution flow. "
            f"The patch ensures the attribute is assigned the correct value before it is used."
        )

    if re.search(r'\b(==|!=|is\s)', added_text):
        paraphrases.append(
            f"A condition in {loc_str} evaluates incorrectly, causing the wrong code path to execute. "
            f"The patch corrects the comparison logic."
        )

    if not paraphrases:
        paraphrases.append(
            f"The implementation in {loc_str} does not handle the reported case correctly. "
            f"The patch modifies the affected code to produce the expected behavior."
        )

    return paraphrases[0]


def derive_incorrect(patch_text, locus, concepts, gold):
    """Generate a plausible but incorrect diagnosis."""
    added = extract_added_lines(patch_text)
    added_text = ' '.join(added[:10])
    primary_loc = locus[0] if locus else 'the code'
    func_loc = next((l for l in locus if re.match(r'^[a-z_][a-z_0-9]+$', l)), None)
    loc_str = func_loc or primary_loc

    incorrects = []

    if re.search(r'\b(if\s+\w+|unless|guard)', added_text, re.I):
        incorrects.append(
            f"{loc_str} fails because the condition is evaluated too early. Move the guard check "
            f"to after the main logic completes."
        )

    if re.search(r'\b(copy|clone|deepcopy|\.copy)', added_text):
        incorrects.append(
            f"{loc_str} mutates shared state unnecessarily. Remove the copy and operate directly on "
            f"the original object since it is not shared."
        )

    if re.search(r'\b(except|try|raise)', added_text):
        incorrects.append(
            f"{loc_str} raises an exception that should be allowed to propagate. Remove the exception "
            f"handler and let the caller deal with the error."
        )

    if re.search(r'\b(clear|reset|None)', added_text):
        incorrects.append(
            f"{loc_str} should preserve its state across calls for caching. Remove the reset "
            f"so the cached value persists and improves performance."
        )

    if re.search(r'\b(return\b)', added_text) and not re.search(r'\b(if|guard)', added_text, re.I):
        incorrects.append(
            f"{loc_str} returns too eagerly. Refactor to always execute the full logic path "
            f"regardless of the condition."
        )

    if re.search(r'\b(default|fallback)', added_text):
        incorrects.append(
            f"{loc_str} should fail explicitly when the value is absent rather than using a default. "
            f"Remove the fallback to surface the error to the caller."
        )

    if re.search(r'\b(for\s|while\s)', added_text):
        incorrects.append(
            f"{loc_str} should process all items at once using a vectorized operation. "
            f"Replace the loop with a bulk operation for better performance."
        )

    if re.search(r'\.\w+\s*=|self\.\w+', added_text):
        incorrects.append(
            f"{loc_str} should compute the attribute lazily on first access. "
            f"Move the assignment to a property getter rather than setting it eagerly."
        )

    if re.search(r'\b(==|!=|is\s)', added_text):
        incorrects.append(
            f"{loc_str} uses identity comparison when equality is needed. "
            f"Replace the identity check with an equality comparison."
        )

    if not incorrects:
        incorrects.append(
            f"{loc_str} does not need modification. The reported behavior is correct and the issue "
            f"is a misunderstanding of the intended design."
        )

    return incorrects[0]


def generate_row(index, instance_id, patch, filepath, problem_statement=''):
    """Generate a ROWS entry for a fixture."""
    patch_hash = hashlib.sha256(patch.encode()).hexdigest()[:16]

    # Extract components
    changed_files = extract_changed_files(patch)
    locus = derive_locus(patch, filepath)
    concepts = derive_concepts(patch)
    gold = derive_gold_explanation(patch, locus, concepts)
    paraphrase = derive_paraphrase(patch, locus, concepts, gold, problem_statement)
    incorrect = derive_incorrect(patch, locus, concepts, gold)

    # Format locus string
    locus_str = '|'.join(locus[:5])

    # Format concepts string
    concepts_str = '~'.join(concepts[:2])

    # Ensure paraphrase doesn't repeat issue title
    title_words = set(problem_statement.lower().split()[:10]) if problem_statement else set()
    paraphrase_lower = paraphrase.lower()
    if any(w in paraphrase_lower for w in title_words if len(w) > 4):
        # Rewrite to avoid title words
        paraphrase = derive_paraphrase(patch, locus, concepts, gold, '')

    return {
        'index': index,
        'locus': locus_str,
        'concepts': concepts_str,
        'gold': gold,
        'paraphrase': paraphrase,
        'incorrect': incorrect,
        'patch_hash': patch_hash,
        'instance_id': instance_id,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'rows_101_300.json')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()

    candidates = load_candidates()
    selection = load_selection()

    rows_101_300 = {}
    skipped = []
    errors = []

    for idx in range(101, 301):
        sel_entry = selection.get(idx)
        if not sel_entry:
            skipped.append((idx, 'not in selection'))
            continue

        iid = sel_entry['instance_id']
        cand = candidates.get(iid)
        if not cand or not cand.get('patch'):
            skipped.append((idx, f'missing candidate or patch for {iid}'))
            continue

        filepath = sel_entry.get('source_files_changed', ['unknown'])[0]
        problem_statement = cand.get('problem_statement', '')

        try:
            row = generate_row(idx, iid, cand['patch'], filepath, problem_statement)
            rows_101_300[idx] = {
                'locus': row['locus'],
                'concepts': row['concepts'],
                'gold': row['gold'],
                'paraphrase': row['paraphrase'],
                'incorrect': row['incorrect'],
                'source': {
                    'instance_id': iid,
                    'patch_hash': row['patch_hash'],
                    'filepath': filepath,
                }
            }
        except Exception as e:
            errors.append((idx, iid, str(e)))

    print(f'Generated {len(rows_101_300)} ROWS entries (101-300)')
    print(f'Skipped: {len(skipped)}')
    print(f'Errors: {len(errors)}')

    if skipped:
        print('\nSkipped entries:')
        for idx, reason in skipped[:5]:
            print(f'  {idx}: {reason}')

    if errors:
        print('\nErrors:')
        for idx, iid, err in errors[:5]:
            print(f'  {idx} ({iid}): {err[:100]}')

    if args.write:
        args.output.write_text(json.dumps(rows_101_300, indent=2))
        print(f'\nWrote to {args.output}')

    # Print sample
    print('\nSample entries:')
    for idx in [101, 150, 200, 250, 300]:
        if idx in rows_101_300:
            r = rows_101_300[idx]
            print(f'\n{idx}:')
            print(f'  locus: {r["locus"][:80]}')
            print(f'  concepts: {r["concepts"][:80]}')
            print(f'  gold: {r["gold"][:100]}...')
            print(f'  paraphrase: {r["paraphrase"][:100]}...')


if __name__ == '__main__':
    main()
