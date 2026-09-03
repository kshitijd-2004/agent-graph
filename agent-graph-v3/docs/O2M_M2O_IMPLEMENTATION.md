# O2M/M2O Implementation Summary

## What Was Completed

### 1. Propagation Mode Semantics (registry.py)
- Extended `LEPFiringState` with `eligible_occurrence_counts` dict
- Added `mark_fired_origin(code, target=None)` — records target + increments count
- Added `set_max_origins(code, max)` — configures per-LEP origin budgets
- `evaluate_for_boundary` now supports:
  - `single_origin`: 1 origin everywhere
  - `one_to_many`: 1 upstream origin; consumers NOT reinjected
  - `many_to_one`: 1 origin per worker; coordinator skipped after all fire

### 2. Topology-Aware Target Selection (topology_target.py)
- `resolve_target_stage(lep_config, topology, propagation_mode)` validates:
  - `branch:<role>` → only valid for branch_and_verify
  - `worker:<role>` → only valid for coordinator_workers
  - `upstream:<role>` → only valid for coordinator_workers + one_to_many
- `TOPOLOGY_TARGETS` dict defines default injection points per topology
- `get_default_topology_target(topology_id)` returns the default

### 3. O2M Shared-Artifact Semantics (runner.py)
- `_o2m_shared_artifacts` dict stores coordinator handoff payloads
- Coordinator's handoff registered as single shared artifact
- Workers annotate TOPOLOGY_TRANSITION event when consuming shared artifact
- Propagation tracker annotates lineage for downstream analysis

### 4. PropagationTracker Enhancements (propagation_tracker.py)
- Added `get_lineage(lep_code, origin_event_id)` — lookup by key
- Added `post_process_stage_events(events, agent_role)` — detect recovery
- Fixed duplicate `_lineage_id` and `post_process_stage_events` definitions

### 5. Tests Created
- `test_o2m_m2o_propagation.py` — 68 tests across 7 classes
- Fixed `test_topology_targeting.py` — updated stale assertions

### 6. Files Modified
| File | Changes |
|------|---------|
| `leps/registry.py` | LEPFiringState, mark_fired_origin, set_max_origins, evaluate_for_boundary |
| `leps/topology_target.py` | resolve_target_stage, TOPOLOGY_TARGETS, get_default_topology_target |
| `generation/runner.py` | O2M shared artifacts, consumption tracking |
| `generation/propagation_tracker.py` | get_lineage, post_process_stage_events |
| `generation/handoff.py` | (already complete from prior session) |
| `tests/test_o2m_m2o_propagation.py` | 68 new tests |
| `tests/test_topology_targeting.py` | Fixed stale assertions |
