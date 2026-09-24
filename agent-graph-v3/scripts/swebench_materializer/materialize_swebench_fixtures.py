#!/usr/bin/env python3
"""
Materialize AgentProp code_review fixtures from the frozen 100 SWE-bench Verified IDs.

What it does:
1. Loads the official SWE-bench Verified dataset to resolve base_commit/version metadata.
2. Clones each unique repository once into a cache.
3. Reads task-relevant files at the exact pre-fix base_commit with `git show`
   (no working-tree checkout required).
4. Writes those files under each fixture's workspace/.
5. Enriches manifest.json with base_commit/version and validates required files.
6. Checks that the gold patch applies cleanly to the base commit without modifying it.
7. Emits validation_report.json and exits non-zero if any fixture fails.

The gold solution patch is NEVER applied to the workspace.
"""

import argparse, ast, hashlib, json, math, os, re, shutil, subprocess, sys
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.validate_fixture import validate_and_write
from scripts.swebench_materializer.migrate_manifests import migrate

def run(cmd, cwd=None, check=True, text=True):
    p = subprocess.run(cmd, cwd=cwd, text=text, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr}")
    return p

def load_verified():
    try:
        from datasets import load_dataset
    except ImportError:
        raise SystemExit("Missing dependency: pip install datasets")
    ds = load_dataset("SWE-bench/SWE-bench_Verified", split="test")
    return {row["instance_id"]: dict(row) for row in ds}

def ensure_repo(cache_root: Path, repo: str):
    owner, name = repo.split("/", 1)
    dest = cache_root / owner / name
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--filter=blob:none", "--no-checkout",
             f"https://github.com/{repo}.git", str(dest)])
    return dest

def ensure_commit(repo_dir: Path, commit: str):
    ok = run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo_dir, check=False)
    if ok.returncode != 0:
        run(["git", "fetch", "origin", commit, "--depth=1"], cwd=repo_dir)

def read_at_commit(repo_dir: Path, commit: str, relpath: str):
    # Avoid universal-newline translation: small fixtures retain blob bytes.
    p = run(["git", "show", f"{commit}:{relpath}"], cwd=repo_dir, check=False, text=False)
    if p.returncode != 0:
        return None
    return p.stdout.decode("utf-8")


CONTEXT_LINES = 20
TOKEN_BUDGET = 8000
DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def estimate_fixture_tokens(fixture_dir: Path, manifest: dict) -> int:
    """Same bytes/4 estimate and exclusions as check_fixture; no gate changes."""
    clean = {key: value for key, value in manifest.items() if key != "validation"}
    size = len(json.dumps(clean, ensure_ascii=False).encode())
    size += sum(path.stat().st_size for path in fixture_dir.rglob("*")
                if path.is_file() and path.name != "manifest.json")
    return math.ceil(size / 4)


def merge_ranges(ranges):
    """Merge overlapping/adjacent 1-based inclusive original-source ranges."""
    merged = []
    for start, end in sorted(ranges):
        if start > end:
            continue
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def _start(node):
    return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])


def _small_docstring(body, lines):
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        node = body[0]
        if node.end_lineno - node.lineno < CONTEXT_LINES and len("".join(lines[node.lineno - 1:node.end_lineno]).encode()) <= 2000:
            return [(node.lineno, node.end_lineno)]
    return []


def _ast_context(tree, nodes, lines, parents):
    """Retain complete statements near anchors and headers of their scopes.

    A neighbor extending outside the fixed 20-line window is omitted entirely.
    Enclosing functions/classes contribute headers, not their entire bodies.
    Other compound ancestors (if/try/etc.) stay whole to preserve their suites.
    """
    ranges = _small_docstring(tree.body, lines)

    def module_imports(node):
        if isinstance(node, DEFINITIONS):
            return False
        return isinstance(node, (ast.Import, ast.ImportFrom)) or any(
            module_imports(child) for child in ast.iter_child_nodes(node))

    for statement in tree.body:
        if module_imports(statement):
            ranges.append((_start(statement), statement.end_lineno))

    for node in nodes:
        # Whole compound ancestors avoid orphaned else/except clauses.
        anchor = node
        ancestor = parents.get(node)
        while ancestor is not None and not isinstance(ancestor, ast.Module):
            if not isinstance(ancestor, DEFINITIONS):
                anchor = ancestor
            ancestor = parents.get(ancestor)
        scope = parents[anchor]
        start, end = _start(anchor), anchor.end_lineno
        lo, hi = max(1, start - CONTEXT_LINES), min(len(lines), end + CONTEXT_LINES)
        body = scope.body
        lo = max(lo, _start(body[0]))
        if not isinstance(scope, ast.Module):
            hi = min(hi, scope.end_lineno)
        for sibling in body:
            first, last = _start(sibling), sibling.end_lineno
            if first < lo <= last:
                lo = last + 1
            if first <= hi < last:
                hi = first - 1
        ranges.append((min(lo, start), max(hi, end)))
        while not isinstance(scope, ast.Module):
            # Header includes multiline declarations and decorators, unchanged.
            first_body = _start(scope.body[0])
            ranges.append((_start(scope), max(scope.lineno, first_body - 1)))
            ranges.extend(_small_docstring(scope.body, lines))
            scope = parents[scope]
    return ranges


def _patch_hunks(source, relpath, patch):
    """Verify each relevant hunk's old lines against the exact pre-fix source."""
    original = source.splitlines()
    patch_lines = patch.splitlines()
    current_file = None
    hunks = []
    for index, line in enumerate(patch_lines):
        if line.startswith("--- "):
            current_file = line[6:] if line.startswith("--- a/") else None
        if current_file != relpath or not line.startswith("@@ "):
            continue
        match = re.match(r"@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", line)
        if not match:
            return []
        start, count = int(match[1]), int(match[2] or 1)
        old = []
        for body_line in patch_lines[index + 1:]:
            if body_line.startswith(("@@ ", "diff --git ", "--- ")):
                break
            if body_line.startswith((" ", "-")):
                old.append(body_line[1:])
            elif not body_line.startswith(("+", "\\")):
                break
        if len(old) != count or start < (1 if count else 0) or start + count - 1 > len(original):
            return []
        if count and original[start - 1:start + count - 1] != old:
            return []
        if not count and start > len(original):
            return []
        hunks.append({"start": start, "count": count})
    return hunks


def extract_python_context(source: str, relpath: str, symbols: list[str], patch: str):
    """One deterministic extraction attempt; unsafe results retain full source."""
    lines = source.splitlines(keepends=True)
    symbols = sorted(set(symbol.strip().removesuffix("()") for symbol in symbols))
    nodes = []
    missing = []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        tree = None
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)} if tree else {}
    if tree:
        for symbol in symbols:
            matches = []
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                names, parent = [node.name], parents.get(node)
                while parent is not None:
                    if isinstance(parent, DEFINITIONS):
                        names.insert(0, parent.name)
                    parent = parents.get(parent)
                if symbol == (".".join(names) if "." in symbol else node.name):
                    matches.append(node)
            nodes.extend(matches)
            if not matches:
                missing.append(symbol)

    strategy = "required_function"
    reason = None
    hunks = []
    if tree is None or not symbols or missing:
        strategy = "patch_window"
        reason = "ast_parse_failed" if tree is None else "missing_required_symbol" if missing else "no_required_symbols"
        hunks = _patch_hunks(source, relpath, patch)
        if not hunks:
            return source, {"strategy": "full_file", "reason": reason + ":no_verified_patch_hunks"}
        ranges = []
        for hunk in hunks:
            start = max(1, hunk["start"])
            end = max(start, hunk["start"] + hunk["count"] - 1)
            if tree:
                # Align a patch window to the enclosing method/statement, so
                # a method's indentation or body is never arbitrarily cut.
                def anchors(body):
                    for stmt in body:
                        if _start(stmt) <= end and stmt.end_lineno >= start:
                            if isinstance(stmt, ast.ClassDef) and start >= _start(stmt.body[0]):
                                yield from anchors(stmt.body)
                            else:
                                yield stmt
                located = list(anchors(tree.body))
                nodes.extend(located)
                ranges.append((start, end))
                if located:
                    continue
            ranges.append((max(1, start - CONTEXT_LINES), min(len(lines), end + CONTEXT_LINES)))
        if tree and nodes:
            ranges.extend(_ast_context(tree, nodes, lines, parents))
    else:
        ranges = _ast_context(tree, nodes, lines, parents)

    included = merge_ranges(ranges)
    extracted = "".join("".join(lines[start - 1:end]) for start, end in included)
    try:
        ast.parse(extracted)
    except (SyntaxError, ValueError):
        return source, {"strategy": "full_file", "reason": "unsafe_extracted_syntax", "attempted_strategy": strategy}
    if not extracted.strip() or len(extracted.encode()) >= len(source.encode()):
        return source, {"strategy": "full_file", "reason": "no_source_reduction", "attempted_strategy": strategy}
    context = {"strategy": strategy, "symbols": symbols, "original_line_count": len(lines),
               "included_ranges": included, "context_lines": CONTEXT_LINES, "extractor_version": 1,
               "original_sha256": hashlib.sha256(source.encode()).hexdigest(),
               "extracted_sha256": hashlib.sha256(extracted.encode()).hexdigest()}
    if reason:
        context.update(fallback_reason=reason, patch_hunks=hunks)
    return extracted, context


def apply_source_context(fixture_dir: Path, manifest: dict, gold_patch: str):
    """Preserve small fixtures; extract oversized code_review sources once.

    Return gate-compatible before/after estimates, including provenance. Size
    results live in the materializer report, avoiding self-referential counts.
    """
    context = {name: {"strategy": "full_file"} for name in manifest["required_files"]}
    manifest["materialization"]["source_context"] = context
    before = estimate_fixture_tokens(fixture_dir, manifest)
    if before > TOKEN_BUDGET and manifest.get("task_family") == "code_review":
        for name in context:
            path = fixture_dir / name
            if path.suffix != ".py" or not path.is_file():
                continue
            symbols = [issue["function"] for issue in manifest.get("required_issues", [])
                       if re.sub(r":\d+(?:-\d+)?$", "", issue.get("location", "")) == name
                       and issue.get("function")]
            source = path.read_bytes().decode("utf-8")
            extracted, context[name] = extract_python_context(source, name, symbols, gold_patch)
            if extracted != source:
                path.write_bytes(extracted.encode("utf-8"))
    return before, estimate_fixture_tokens(fixture_dir, manifest)

def patch_applies(repo_dir: Path, commit: str, patch_text: str, temp_root: Path):
    # Use a temporary detached worktree so `git apply --check` is evaluated
    # against exactly base_commit without touching the cache repository.
    wt = Path(tempfile.mkdtemp(prefix="patch-check-", dir=temp_root))
    run(["git", "worktree", "add", "--detach", str(wt), commit], cwd=repo_dir)
    try:
        patch_file = wt / ".agentprop_gold.patch"
        patch_file.write_text(patch_text)
        p = run(["git", "apply", "--check", str(patch_file)], cwd=wt, check=False)
        return p.returncode == 0, p.stderr.strip()
    finally:
        run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo_dir, check=False)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selection", default=str(Path(__file__).with_name("selection.json")))
    ap.add_argument("--draft-manifests", default=str(Path(__file__).with_name("draft_manifests")))
    ap.add_argument("--output", default=str(ROOT / "workspace_fixtures"))
    ap.add_argument("--cache", default=".cache/agentprop-swebench")
    ap.add_argument("--temp", default=".cache/agentprop-swebench-worktrees")
    ap.add_argument("--limit", type=int, default=1,
                    help="Materialize only first N fixtures for a smoke test")
    ap.add_argument("--instance-id", action="append", default=[],
                    help="Select a frozen instance ID (repeatable); --limit applies after selection")
    args = ap.parse_args()
    if args.limit < 1:
        ap.error("--limit must be positive")

    selection = json.loads(Path(args.selection).read_text())
    ids = [entry["instance_id"] for entry in selection["fixtures"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Selection contains duplicate source.instance_id values")
    if set(args.instance_id) - set(ids):
        ap.error("Requested instance ID is not in the frozen selection")
    chosen = [entry for entry in selection["fixtures"]
              if not args.instance_id or entry["instance_id"] in args.instance_id][:args.limit]
    official = load_verified()

    out_root, cache_root, temp_root = map(
    lambda p: Path(p).resolve(),
    (args.output, args.cache, args.temp)
    )
    out_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)

    draft_by_instance = {}
    for mp in Path(args.draft_manifests).glob("*/manifest.json"):
        m = migrate(json.loads(mp.read_text()))
        if m["source"]["instance_id"] in draft_by_instance and not m.get("variant_of"):
            raise ValueError("Draft manifests contain duplicate source.instance_id values")
        draft_by_instance[m["source"]["instance_id"]] = (mp.parent, m)

    report = {"total": len(chosen), "passed": 0, "failed": 0, "fixtures": []}

    for rec in chosen:
        iid = rec["instance_id"]
        item = {"instance_id": iid, "ok": False, "errors": [], "warnings": []}
        try:
            if iid not in official:
                raise RuntimeError("ID missing from official SWE-bench Verified dataset")
            if iid not in draft_by_instance:
                raise RuntimeError("ID missing from draft manifests")

            row = official[iid]
            draft_dir, manifest = draft_by_instance[iid]
            repo = row["repo"]
            base = row["base_commit"]
            repo_dir = ensure_repo(cache_root, repo)
            ensure_commit(repo_dir, base)

            dest = out_root / manifest["fixture_id"]
            if dest.exists():
                raise FileExistsError(f"Refusing to overwrite existing fixture: {dest}")
            dest.mkdir(parents=True)
            workspace = dest


            # Files touched by the gold/test patches are the deterministic minimum
            # task-relevant workspace. Add any manifest-required files as well.
            requested = []
            for f in manifest.get("required_files", []):
                if f not in requested:
                    requested.append(f)

            missing = []
            for rel in requested:
                if Path(rel).is_absolute() or ".." in Path(rel).parts:
                    raise ValueError(f"Unsafe required file: {rel}")
                content = read_at_commit(repo_dir, base, rel)
                if content is None:
                    # A file may be newly created by the solution/test patch; it
                    # correctly does not exist in the pre-fix workspace.
                    missing.append(rel)
                    continue
                target = workspace / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content.encode("utf-8"))

            gold_patch = row.get("patch") or manifest["oracle"].get("gold_patch", "")
            applies, apply_err = patch_applies(repo_dir, base, gold_patch, temp_root)

            manifest["source"]["base_commit"] = base
            manifest["source"]["version"] = row.get("version")
            manifest["source"]["difficulty"] = row.get("difficulty")
            manifest["source"]["created_at"] = row.get("created_at")
            manifest["oracle"]["fail_to_pass"] = row.get("FAIL_TO_PASS", manifest["oracle"]["fail_to_pass"])
            manifest["oracle"]["pass_to_pass"] = row.get("PASS_TO_PASS", manifest["oracle"]["pass_to_pass"])
            manifest["materialization"] = {
                "workspace_state": "pre_fix_base_commit",
                "base_commit": base,
                "requested_files": requested,
                "present_files": [f for f in requested if f not in missing],
                "absent_at_base_commit": missing,
                "gold_patch_applies_cleanly": applies,
            }
            before, after = apply_source_context(dest, manifest, gold_patch)
            item["estimated_tokens_before"] = before
            item["estimated_tokens_after"] = after
            item["source_context"] = manifest["materialization"]["source_context"]
            # oracle is construction-only (gold_patch drove extraction above;
            # fail_to_pass/pass_to_pass are SWE-bench metadata, never read at
            # runtime by the benchmark engine, evaluators, LEPs, or anomaly
            # detection).  Strip it to save tokens in the workspace manifest.
            manifest.pop("oracle", None)
            (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))

            # Required attack targets must exist at base commit.
            for key in ("tool_result", "prompt_injection"):
                target = manifest.get("attack", {}).get(key, {}).get("target_file")
                if target and target in missing:
                    item["errors"].append(f"{key} target absent at base commit: {target}")
            if not applies:
                item["errors"].append("gold patch does not apply cleanly: " + apply_err[:500])

            # A required file must exist in the pre-fix workspace.
            if missing:
                item["errors"].append(
                    "required files absent at base commit: " + ", ".join(missing)
                )

            validation = validate_and_write(dest, fixture_roots=[out_root])
            item["validation"] = validation
            if not validation["passed"]:
                item["errors"].extend(validation["errors"] or ["Fixture acceptance gate failed"])

            item["base_commit"] = base
            item["fixture_id"] = manifest["fixture_id"]
            item["ok"] = not item["errors"]
        except Exception as e:
            item["errors"].append(str(e))

        if item["ok"]:
            report["passed"] += 1
        else:
            report["failed"] += 1
        report["fixtures"].append(item)
        print(("PASS" if item["ok"] else "FAIL"), iid)

    Path("validation_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ("total","passed","failed")}, indent=2))
    sys.exit(1 if report["failed"] else 0)

if __name__ == "__main__":
    main()
