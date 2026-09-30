"""Expand selection.json to 300, generate ROWS 101-300, and regenerate drafts."""
import json
import hashlib
import re
from pathlib import Path
from collections import Counter

BASE = Path('/u/kdhande/agent-graph/agent-graph-v3')
MAT_DIR = BASE / 'scripts' / 'swebench_materializer'


def load_candidates():
    with open(BASE / 'swe_verified_candidates.json') as f:
        return {c['instance_id']: c for c in json.load(f)}


def load_selection():
    with open(MAT_DIR / 'selection.json') as f:
        return json.load(f)


def extract_changed_files(patch):
    files = []
    for line in patch.splitlines():
        if line.startswith('--- a/') and '/dev/null' not in line:
            files.append(line[6:])
    return list(dict.fromkeys(files))


def derive_locus(patch, filepath):
    """Derive evaluator location anchors from patch."""
    locus = [filepath]
    funcs = []
    for line in patch.splitlines():
        if line.startswith('@@'):
            for m in re.finditer(r'def\s+(\w+)', line):
                funcs.append(m.group(1))
    if funcs:
        locus.extend(list(dict.fromkeys(funcs))[:3])
    added = [l[1:].strip() for l in patch.splitlines()
             if l.startswith('+') and not l.startswith('+++')]
    symbols = []
    for line in added[:20]:
        for m in re.finditer(r'\b([a-z_][a-z_0-9]{2,})\b', line, re.I):
            sym = m.group(1)
            if sym not in ('the', 'and', 'for', 'not', 'with', 'from', 'this',
                           'that', 'return', 'self', 'None', 'True', 'False',
                           'import', 'class', 'def', 'if', 'else', 'elif',
                           'in ', 'is ', 'not ', 'and ', 'or ', 'pass'):
                symbols.append(sym)
    seen = set()
    for s in symbols:
        if s not in seen:
            seen.add(s)
            locus.append(s)
        if len(locus) >= 8:
            break
    return locus[:8]


def derive_concepts(patch):
    """Derive two concept patterns from added lines."""
    added = ' '.join(l[1:].strip() for l in patch.splitlines()
                     if l.startswith('+') and not l.startswith('+++'))[:2000]

    patterns = []
    if re.search(r'\b(if|unless|guard|check|assert|valid|condition)\b', added, re.I):
        patterns.append('guard|check|condition|validat')
    if re.search(r'\b(copy|clone|deepcopy|dict\(|list\(|\.copy)\b', added):
        patterns.append('copy|clone|independent|separate|preserv')
    if re.search(r'\b(set|assign|update|=)\s+\w+', added):
        patterns.append('assign|set|initializ|updat')
    if re.search(r'\b(except|try|raise|error|exception)\b', added, re.I):
        patterns.append('except|error|exception|handl')
    if re.search(r'\b(for\s+\w+\s+in|while\s+\w+|iter\w*)\b', added):
        patterns.append('loop|iterat')
    if re.search(r'\b(clear|reset|empty|None\b|null\b)\b', added):
        patterns.append('clear|reset|none|null|empty')
    if re.search(r'\b(return\b|\byield\b)', added):
        patterns.append('return|yield|output')
    if re.search(r'\b(default|fallback)\b', added):
        patterns.append('default|fallback')
    if re.search(r'\.\w+\s*=|self\.\w+', added):
        patterns.append('attribute|property|field|state')
    if re.search(r'\b(==|!=|is\s|less|greater|compar)\b', added):
        patterns.append('compar|test|check|condition')
    if re.search(r'\b(import\s|from\s+\w+\s+import)\b', added):
        patterns.append('import|dependenc|module')
    if re.search(r'\b(class|__init__)\b', added):
        patterns.append('class|constructor|init')
    if re.search(r'\b(raise|except|catch)\b', added):
        patterns.append('error|except|rais')
    if re.search(r'\b(mutat|modif|chang|updat)\b', added):
        patterns.append('mutat|modif|chang|updat')

    if len(patterns) < 2:
        patterns = ['modif|chang|updat', 'correct|fix|resolv']
    return patterns[:2]


def derive_explanation(patch, locus):
    """Generate gold explanation."""
    added = ' '.join(l[1:].strip() for l in patch.splitlines()
                     if l.startswith('+') and not l.startswith('+++'))[:1500]
    func = next((l for l in locus if re.match(r'^[a-z_][a-z_0-9]+$', l)), locus[0] if locus else 'the code')
    filepath = locus[0] if locus else 'unknown'

    if re.search(r'\b(if\s+\w+|unless|guard)', added, re.I):
        return (f"In {func}, the patch adds a guard or condition check that was missing. "
                f"Without this check, the code proceeds with invalid state and produces incorrect behavior. "
                f"The guard ensures the operation only executes when the precondition is met.")
    if re.search(r'\b(copy|clone|deepcopy|\.copy)', added):
        return (f"In {func}, the patch introduces a copy or clone before mutation. "
                f"Modifying the original shared object caused side effects in other references. "
                f"The copy isolates the change and preserves correct behavior elsewhere.")
    if re.search(r'\b(except|try|raise)', added):
        return (f"In {func}, the patch adds error handling or exception catching. "
                f"Previously unhandled errors propagate and cause crashes or incorrect behavior. "
                f"The handler ensures errors are dealt with appropriately.")
    if re.search(r'\b(clear|reset|None)', added):
        return (f"In {func}, the patch clears or resets state that was retained incorrectly. "
                f"Stale state causes the code to behave as if the object still holds old data. "
                f"Resetting ensures a clean state for the next operation.")
    if re.search(r'\b(return\b)', added) and not re.search(r'\b(if|guard)', added, re.I):
        return (f"In {func}, the patch corrects the return logic. "
                f"The function returned an incorrect value or missed a step on certain code paths. "
                f"Adjusting the return ensures the expected value is produced consistently.")
    if re.search(r'\b(default|fallback)', added):
        return (f"In {func}, the patch adds a default value for a previously unhandled case. "
                f"When the expected input is absent, the code now falls back to a sensible default "
                f"instead of producing None or crashing.")
    if re.search(r'\b(for\s|while\s)', added):
        return (f"In {func}, the iteration logic is corrected. "
                f"The patch fixes how the loop processes items or terminates, "
                f"ensuring each iteration handles data correctly.")
    if re.search(r'\.\w+\s*=|self\.\w+', added):
        return (f"In {func}, the patch corrects attribute assignment. "
                f"The attribute was set at the wrong point in the code flow, "
                f"leading to incorrect state when the value was used.")
    if re.search(r'\b(==|!=|is\s)', added):
        return (f"In {func}, the patch corrects a comparison or condition. "
                f"The previous comparison evaluated incorrectly, causing the wrong code path to execute. "
                f"Fixing the condition ensures the right branch is taken.")
    return (f"In {func}, the patch corrects behavior by modifying the affected code path in {filepath}. "
            f"The previous implementation did not handle the reported case correctly, "
            f"leading to the observed incorrect behavior.")


def derive_paraphrase(patch, locus):
    """Generate paraphrase (no issue title repetition)."""
    added = ' '.join(l[1:].strip() for l in patch.splitlines()
                     if l.startswith('+') and not l.startswith('+++'))[:1500]
    func = next((l for l in locus if re.match(r'^[a-z_][a-z_0-9]+$', l)), locus[0] if locus else 'the code')

    if re.search(r'\b(if\s+\w+|unless|guard)', added, re.I):
        return (f"Reviewing {func} shows the code path proceeds without verifying a necessary precondition. "
                f"The patch introduces a condition check that prevents the operation from executing "
                f"when the required state is not satisfied, stopping incorrect behavior at the source.")
    if re.search(r'\b(copy|clone|deepcopy|\.copy)', added):
        return (f"The code in {func} modifies a shared data structure without isolating the change first. "
                f"The patch creates an independent copy before applying modifications, "
                f"so the alteration does not leak to other references holding the same object.")
    if re.search(r'\b(except|try|raise)', added):
        return (f"An error condition in {func} is not handled and propagates unexpectedly. "
                f"The patch adds exception handling to catch this case and respond appropriately, "
                f"preventing unhandled errors from causing incorrect behavior.")
    if re.search(r'\b(clear|reset|None)', added):
        return (f"State in {func} is not reset when it should be, causing stale values to persist. "
                f"The patch clears or resets this state at the appropriate point in the code flow, "
                f"ensuring subsequent operations see the correct fresh state.")
    if re.search(r'\b(return\b)', added) and not re.search(r'\b(if|guard)', added, re.I):
        return (f"The return path in {func} does not produce the correct result on all code paths. "
                f"The patch adjusts the logic so the function returns the expected value "
                f"regardless of which branch is taken.")
    if re.search(r'\b(default|fallback)', added):
        return (f"A case without a defined value is not handled in {func}. "
                f"The patch adds a default or fallback to ensure consistent behavior "
                f"when the value is absent rather than producing an error or None.")
    if re.search(r'\b(for\s|while\s)', added):
        return (f"The iteration in {func} does not process items correctly. "
                f"The patch fixes the loop logic so each item is handled as expected, "
                f"correcting incorrect behavior in the loop body or termination.")
    if re.search(r'\.\w+\s*=|self\.\w+', added):
        return (f"An attribute in {func} is not set at the right point in the execution flow. "
                f"The patch ensures the attribute is assigned the correct value before it is used, "
                f"fixing incorrect state that was observed.")
    if re.search(r'\b(==|!=|is\s)', added):
        return (f"A condition in {func} evaluates incorrectly, causing the wrong code path to execute. "
                f"The patch corrects the comparison logic so the right branch is taken based on actual state.")
    return (f"The implementation in {func} does not handle the reported case correctly. "
            f"The patch modifies the affected code path to produce the expected behavior, "
            f"resolving the incorrect behavior without changing unrelated functionality.")


def derive_incorrect(patch, locus):
    """Generate a plausible but incorrect diagnosis."""
    func = next((l for l in locus if re.match(r'^[a-z_][a-z_0-9]+$', l)), locus[0] if locus else 'the code')

    added = ' '.join(l[1:].strip() for l in patch.splitlines()
                     if l.startswith('+') and not l.startswith('+++'))[:1500]

    if re.search(r'\b(if\s+\w+|unless|guard)', added, re.I):
        return (f"{func} fails because the condition is checked too late in the execution flow. "
                f"Move the guard check to execute before any side effects occur.")
    if re.search(r'\b(copy|clone|deepcopy|\.copy)', added):
        return (f"{func} creates unnecessary copies of data that is not shared. "
                f"Remove the copy and operate directly on the original object to improve performance.")
    if re.search(r'\b(except|try|raise)', added):
        return (f"{func} raises an exception that callers should handle themselves. "
                f"Remove the exception handler and let errors propagate to the calling layer.")
    if re.search(r'\b(clear|reset|None)', added):
        return (f"{func} should preserve its internal state across calls for caching efficiency. "
                f"Remove the reset so the cached value persists and improves subsequent performance.")
    if re.search(r'\b(return\b)', added) and not re.search(r'\b(if|guard)', added, re.I):
        return (f"{func} returns too eagerly, skipping necessary computation. "
                f"Refactor to always execute the full logic path regardless of conditions.")
    if re.search(r'\b(default|fallback)', added):
        return (f"{func} should fail explicitly when the value is absent rather than using a default. "
                f"Remove the fallback to surface the error to the caller.")
    if re.search(r'\b(for\s|while\s)', added):
        return (f"{func} should use a vectorized bulk operation instead of a per-item loop. "
                f"Replace the loop with a bulk operation for better performance.")
    if re.search(r'\.\w+\s*=|self\.\w+', added):
        return (f"{func} should compute the attribute lazily on first access. "
                f"Move the assignment to a property getter rather than setting it eagerly.")
    if re.search(r'\b(==|!=|is\s)', added):
        return (f"{func} uses identity comparison when equality comparison is needed. "
                f"Replace the identity check with an equality comparison operator.")
    return (f"{func} does not need modification. The reported behavior is correct "
            f"and the issue is a misunderstanding of the intended design.")


def main():
    # Load data
    candidates = load_candidates()
    sel = load_selection()

    used_ids = {f['instance_id'] for f in sel['fixtures']}

    # Filter candidates for 101-300
    extra_needed = 200
    extra_cands = [c for c in candidates.values()
                   if c['instance_id'] not in used_ids
                   and c.get('patch')
                   and len(c.get('patch', '')) > 50
                   and c.get('FAIL_TO_PASS')]

    # Stratify by repo (matching original allocation proportions)
    original_repo_dist = Counter(f['repo'] for f in sel['fixtures'])
    total_orig = sum(original_repo_dist.values())
    target_repos = {}
    for repo, count in original_repo_dist.items():
        target_repos[repo] = int(count * 2)  # double for 300

    selected_extra = []
    by_repo = {}
    for c in sorted(extra_cands, key=lambda x: len(x.get('patch', ''))):
        repo = c['repo']
        if repo not in by_repo:
            by_repo[repo] = []
        by_repo[repo].append(c)

    # Allocate per repo
    for repo, target in target_repos.items():
        pool = by_repo.get(repo, [])
        # Sort by patch size (prefer smaller, focused patches)
        pool_sorted = sorted(pool, key=lambda x: len(x.get('patch', '')))
        selected_extra.extend(pool_sorted[:target])

    # If we didn't get enough, fill from any remaining repo
    if len(selected_extra) < extra_needed:
        remaining = [c for c in extra_cands if c not in selected_extra]
        remaining_sorted = sorted(remaining, key=lambda x: len(x.get('patch', '')))
        selected_extra.extend(remaining_sorted[:extra_needed - len(selected_extra)])

    # Sort by repo then instance_id for deterministic output
    selected_extra.sort(key=lambda x: (x['repo'], x['instance_id']))

    print(f'Selected {len(selected_extra)} extra candidates')

    # Expand selection.json (idempotent)
    if len(sel['fixtures']) < 300:
        new_index = max(f['selection_index'] for f in sel['fixtures']) + 1
        new_fixtures = []
        for c in selected_extra[:extra_needed]:
            changed_files = extract_changed_files(c['patch'])
            new_fixtures.append({
                'selection_index': new_index,
                'instance_id': c['instance_id'],
                'repo': c['repo'],
                'source_files_changed': changed_files[:3],
                'fail_to_pass': c.get('FAIL_TO_PASS', []),
                'pass_to_pass': c.get('PASS_TO_PASS', []),
            })
            new_index += 1
        sel['fixtures'].extend(new_fixtures)
        sel['n_selected'] = len(sel['fixtures'])
        sel['selection_policy']['repository_allocation'] = dict(Counter(f['repo'] for f in sel['fixtures']))

        with open(MAT_DIR / 'selection.json', 'w') as f:
            json.dump(sel, f, indent=2)
        print(f'Expanded selection.json to {len(sel["fixtures"])} fixtures')
    else:
        print(f'selection.json already has {len(sel["fixtures"])} fixtures, skipping expansion')

    # Generate ROWS entries 101-300
    rows_101_300 = {}
    for fix in sel['fixtures']:
        idx = fix['selection_index']
        if idx < 101:
            continue
        iid = fix['instance_id']
        cand = candidates.get(iid)
        if not cand or not cand.get('patch'):
            print(f'WARNING: No patch for {iid}')
            continue

        filepath = fix['source_files_changed'][0] if fix['source_files_changed'] else 'unknown'
        patch = cand['patch']
        patch_hash = hashlib.sha256(patch.encode()).hexdigest()[:16]

        locus = derive_locus(patch, filepath)
        concepts = derive_concepts(patch)
        gold = derive_explanation(patch, locus)
        paraphrase = derive_paraphrase(patch, locus)
        incorrect = derive_incorrect(patch, locus)

        locus_str = '|'.join(locus[:5])
        concepts_str = '~'.join(concepts[:2])

        rows_101_300[idx] = {
            'locus': locus_str,
            'concepts': concepts_str,
            'gold': gold,
            'paraphrase': paraphrase,
            'incorrect': incorrect,
            'source': {
                'instance_id': iid,
                'patch_hash': patch_hash,
                'filepath': filepath,
            }
        }

    print(f'Generated {len(rows_101_300)} ROWS entries (101-300)')

    # Generate Python code for new entries
    lines = ['\n\n# Entries 101-300 (auto-generated from gold patches)']
    for idx in sorted(rows_101_300.keys()):
        r = rows_101_300[idx]
        locus_esc = r['locus'].replace("'", "\\'")
        concepts_esc = r['concepts'].replace("'", "\\'")
        gold_esc = r['gold'].replace("'", "\\'")
        paraphrase_esc = r['paraphrase'].replace("'", "\\'")
        incorrect_esc = r['incorrect'].replace("'", "\\'")
        lines.append(f"add({idx}, '{locus_esc}',")
        lines.append(f"    r'{concepts_esc}',")
        lines.append(f"    '{gold_esc}',")
        lines.append(f"    '{paraphrase_esc}',")
        lines.append(f"    '{incorrect_esc}')")

    append_text = '\n'.join(lines) + '\n'

    # Append to curated.py
    curated_path = MAT_DIR / 'grading' / 'curated.py'
    existing = curated_path.read_text()
    # Check if already appended
    if '# Entries 101-300' not in existing:
        with open(curated_path, 'a') as f:
            f.write(append_text)
        print(f'Appended {len(rows_101_300)} entries to curated.py')
    else:
        print('Entries 101-300 already in curated.py')

    # Verify
    from importlib import reload
    import scripts.swebench_materializer.grading.curated as cmod
    cmod = reload(cmod)
    print(f'ROWS now has {len(cmod.ROWS)} entries')
    print(f'Keys 101-105: {[cmod.ROWS.get(i) is not None for i in range(101, 106)]}')

    # Write rows for inspection
    with open('/tmp/rows_101_300.json', 'w') as f:
        json.dump(rows_101_300, f, indent=2)
    print('Done')


if __name__ == '__main__':
    main()
