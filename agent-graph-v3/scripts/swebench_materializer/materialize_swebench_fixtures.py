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

import argparse, json, os, re, shutil, subprocess, sys
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.validate_fixture import validate_and_write
from scripts.swebench_materializer.migrate_manifests import migrate

def run(cmd, cwd=None, check=True):
    p = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE,
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
    p = run(["git", "show", f"{commit}:{relpath}"], cwd=repo_dir, check=False)
    if p.returncode != 0:
        return None
    return p.stdout

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
    args = ap.parse_args()
    if args.limit < 1:
        ap.error("--limit must be positive")

    selection = json.loads(Path(args.selection).read_text())
    ids = [entry["instance_id"] for entry in selection["fixtures"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Selection contains duplicate source.instance_id values")
    chosen = selection["fixtures"][:args.limit] if args.limit else selection["fixtures"]
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
                target.write_text(content)

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
