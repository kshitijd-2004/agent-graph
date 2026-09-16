"""Marker-free partial_omission for leps/tool_result_corruption.py.

The current operator replaces security-critical function bodies with
`...  # [OMITTED: security-critical function]`. That leaves two signatures
a detector can exploit without understanding anything about the run:

  1. a literal marker string that appears in no clean result
  2. a constant output length (590 bytes in all nine lep traces, against a
     median of 25 for clean results)

Both are properties of the injection mechanism, not of the perturbation, so a
detector that finds them has learned nothing transferable. Measured on the
current traces: a message-passing GNN reaches 0.85 Top-1 on localization using
only text-length features.

This replacement removes a single security-relevant statement and its
explanatory comment. The result stays syntactically valid, keeps every function
present, and reads as ordinary code, so the reviewer's failure is a genuine
miss rather than a missing block. Length delta varies with the statement
removed.

Drop the three functions below into ToolResultCorruptionLEP and route
`partial_omission` to `_silent_omission`.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional, Tuple

# Functions carrying a planted defect, read from every fixture manifest's
# required_issues (e.g. "main.store() and main.delete_file() have ...").
# code_review_easy has no required_issues, but its src/main.py defines the same
# functions as code_review_conflicting, so the union across fixtures covers it.
def _load_defect_functions() -> List[str]:
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "workspace_fixtures"
    names: set = set()
    for mf in root.glob("*/manifest.json"):
        try:
            issues = json.loads(mf.read_text()).get("required_issues") or []
        except (OSError, ValueError):
            continue
        for issue in issues:
            for n in re.findall(r"(\w+)\(\)", issue.get("description", "")):
                names.add(n.split(".")[-1])
    return sorted(names)


DEFECT_FUNCTIONS: List[str] = _load_defect_functions()


# Statements whose removal deletes a defect without breaking the file.
# Each entry: (regex for the line, whether to drop a preceding comment block).
_OMISSION_TARGETS: List[Tuple[str, bool]] = [
    # a guard clause whose absence reintroduces the vulnerability
    (r"^\s*if\s+not\s+\w+.*:\s*$", False),
    # an explicit security annotation that hands the reviewer the answer
    (r"^\s*#\s*SECURITY ISSUE\b.*$", False),
    # a validation or sanitisation call
    (r"^\s*\w*\s*=?\s*\w*(validate|sanitize|check|verify|escape)\w*\(.*\)\s*$", True),
    # an existence or permission test
    (r"^\s*if\s+.*(os\.path\.exists|os\.access|isfile|startswith)\(.*\).*:\s*$", True),
]


def _pick_index(candidates: List[int], seed_text: str) -> int:
    """Deterministic choice from a seed, so a run is reproducible without
    always removing the same statement across different files."""
    h = int(hashlib.sha256(seed_text.encode()).hexdigest()[:8], 16)
    return candidates[h % len(candidates)]


def _find_targets(lines: List[str]) -> List[int]:
    out = []
    for i, line in enumerate(lines):
        for pat, _ in _OMISSION_TARGETS:
            if re.match(pat, line):
                out.append(i)
                break
    return out


def _block_end(lines: List[str], start: int) -> int:
    """If the target line opens a block, return the last line of that block."""
    if not lines[start].rstrip().endswith(":"):
        return start
    indent = len(lines[start]) - len(lines[start].lstrip())
    j = start + 1
    while j < len(lines):
        s = lines[j].strip()
        if s and (len(lines[j]) - len(lines[j].lstrip())) <= indent:
            break
        j += 1
    return j - 1


def _functions(lines: List[str]) -> List[Tuple[int, int, str]]:
    """Top-level defs as (start, end, name)."""
    out = []
    for i, line in enumerate(lines):
        if not re.match(r"^def \w+", line):
            continue
        j = i + 1
        while j < len(lines) and (not lines[j].strip() or lines[j].startswith((" ", "\t"))):
            j += 1
        out.append((i, j - 1, re.match(r"^def (\w+)", line).group(1)))
    return out


def _defect_bearing(lines: List[str], defect_functions: List[str]) -> List[Tuple[int, int, str]]:
    """Only functions that carry a planted defect.

    Removing anything else produces a shorter file with every defect still
    present, so the reviewer reports the same findings and nothing propagates.
    The perturbation has to remove a finding the reviewer was supposed to make.
    """
    want = {f.lower() for f in defect_functions}
    return [(s, e, n) for s, e, n in _functions(lines) if n.lower() in want]


def _silent_omission(original: str, defect_functions: Optional[List[str]] = None,
                     mode: str = "auto") -> str:
    """Remove a planted defect, leaving no marker.

    defect_functions names the functions that carry a defect the reviewer is
    expected to report (from the fixture manifest). Without it the operator
    cannot tell a defect-bearing function from an innocent one, and removing an
    innocent one changes nothing about the report.

    mode "function"  drop one whole defect-bearing function
    mode "statement" drop one guard or validation statement
    mode "auto"      prefer function removal, fall back to statement

    Returns the original unchanged when there is nothing safe to remove, which
    is preferable to emitting a marked-up file. Callers should treat an
    unchanged return as "did not fire".
    """
    lines = original.split("\n")

    if mode in ("auto", "function") and defect_functions:
        fns = _defect_bearing(lines, defect_functions)
        # keep at least one defect behind, so a perturbed run is not simply an
        # easier task than its clean twin
        if len(fns) > 1:
            k = _pick_index(list(range(len(fns))), original[:256])
            s0, e0, _ = fns[k]
            while s0 > 0 and lines[s0 - 1].strip().startswith("#"):
                s0 -= 1
            kept = lines[:s0] + lines[e0 + 1:]
            out: List[str] = []
            for line in kept:
                if not line.strip() and out and not out[-1].strip():
                    continue
                out.append(line)
            res = "\n".join(out)
            if res.strip():
                return res
        if mode == "function":
            return original

    targets = _find_targets(lines)
    if not targets:
        return original

    start = _pick_index(targets, original[:256])
    end = _block_end(lines, start)

    # Also drop an immediately preceding comment, so the removal does not leave
    # a comment describing code that is no longer there.
    first = start
    while first > 0 and lines[first - 1].strip().startswith("#"):
        first -= 1

    kept = lines[:first] + lines[end + 1:]

    # Collapse the blank-line run the removal may have created, so spacing
    # matches ordinary formatting.
    out: List[str] = []
    for line in kept:
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line)
    result = "\n".join(out)
    return result if result.strip() else original


def omission_report(original: str, corrupted: str) -> dict:
    """What was removed. For the benchmark record, not for the agent."""
    o, c = original.split("\n"), corrupted.split("\n")
    removed = [l for l in o if l not in c]
    return {
        "bytes_before": len(original),
        "bytes_after": len(corrupted),
        "delta": len(original) - len(corrupted),
        "lines_removed": len(o) - len(c),
        "removed_text": "\n".join(removed)[:400],
        "changed": original != corrupted,
    }
