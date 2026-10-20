# Injection reachability investigation

Inspected local `production_run/node_*/rejected_traces/*lep_trace.json` on
2026-10-07. This is a later/different snapshot than the reported 97 cases:
106 rejected perturbed traces have different expected and actual origin counts.
103 have zero origins; three have one of two required origins. No extra-origin
case appears in this inspected set. The shard audit text files contain only 101
entries and are not a complete snapshot of the available trace files.

## Evidence and causes

| Cases | Evidence | Cause |
| --- | --- | --- |
| 58 | Coordinator/worker + memory poisoning + one-to-many; all lack a coordinator write before its first handoff | Optional boundary is treated as a planned opportunity, but is not guaranteed by the coordinator's actual role |
| 45 | All research/SciFact; only user input, system init, and initial topology transition; `backend_connection_error` | No model response, so no tool/memory/handoff boundary exists |
| 3 | Financial tool-result corruption + many-to-one; one of two origins | First worker rewrites the source file; second trigger fires but fixture replacements produce no change |

Of the 58 memory cases, 57 finish normally before admission rejects them and one
hits the event limit. There are 73 research cases: 45 backend failures and 28
missing upstream memory writes. These counts describe this snapshot, not rates
normalized by how often each fixture family was scheduled.

Concrete evidence:

- `production_run/node_0/rejected_traces/coordinator_workers_fin_finqa_255_msi_2007_LEP_MEMORY_POISONING_one_to_many_00_lep_trace.json`:
  coordinator reads memory but never writes; specialist A and B write later.
  There is no eligible coordinator memory boundary. Later writes must not be
  used as replacement origins.
- `production_run/node_10/rejected_traces/branch_and_verify_res_scifact_295_988_1405_LEP_HANDOFF_CORRUPTION_many_to_one_00_lep_trace.json`:
  only three initialization events. The adjacent `../prod-3291540_10.err` log
  records connection refused, two retries, and failure for successive LEPs on
  this fixture. This is not evidence of a SciFact-specific injection predicate bug.
- `production_run/node_14/rejected_traces/branch_and_verify_fin_finqa_039_amat_2012_LEP_TOOL_RESULT_CORRUPTION_many_to_one_00_lep_trace.json`:
  the researcher overwrites `documents/report.md` with already-altered figures.
  `../prod-3291540_14.err` records the analyst trigger firing at event 18, but no
  successful corruption. The configured old numeric strings are absent.
  The other partial cases are `fin_finqa_103_cme_2010` and
  `fin_finqa_021_disca_2017`.

## Lifecycle through the implementation

1. `BenchmarkManifest.build_plan` filters fixture-supported topologies and LEPs
   and enumerates allowed propagation modes. This establishes structural
   compatibility, not a guarantee of a particular model-selected action.
2. `ScenarioRunner._execute_scenario` builds/remaps topology roles, binds
   `LEPOrchestrator.set_topology`, and configures the origin budget.
   `intended_origin_roles` binds one-to-many to the exit-stage role, which is
   the upstream coordinator on its initial invocation. Many-to-one binds the
   upstream roles entering the merge; single-origin respects explicit targets
   and the existing family defaults.
3. Tool-result corruption and indirect prompt injection need eligible source
   reads; memory poisoning needs `MEMORY_WRITE`; handoff corruption and input
   disregard need `AGENT_HANDOFF`. None can fire merely on a topology transition.
4. The one-to-many scheduler runs the coordinator first with finalization
   disabled, then workers, then the coordinator aggregation. The initial turn
   may hand off without writing memory: `role_category('coordinator')` is
   `reader`. Memory tools are available in shared modes, but availability is
   not an obligation to use them. Changing this contract just to obtain an
   injection would change the experiment and benign execution semantics.
5. `StageRunner.run_stage` offers real boundaries to
   `LEPOrchestrator.evaluate_for_boundary`. The orchestrator filters boundary
   type, origin roles, budgets, already-used roles, and the initial fan-out
   cutoff before evaluating predicates.
6. The matcher checks prerequisites, event/tool/path/content predicates and
   scoped occurrence counts. Research memory poisoning requires a preceding
   source read. A decision to fire is not proof of a material mutation.
7. StageRunner applies the LEP to the tool content, handoff/input context, or
   memory value. Memory writes preserve the agent's original arguments on the
   tool-call event and the actual stored value on the memory event.
8. A successful mutation is labeled and recorded with `mark_fired_origin`.
   Admission checks actual labeled origins, required distinct roles, ordering,
   terminal state, and expected counts. The disk audit checks rejected traces
   too. Previously its terse lines conflated missing boundaries, backend
   outages, and ineffective mutations.

The dominant failure is therefore a static-opportunity/dynamic-path mismatch,
not a dropped coordinator write or an incorrectly routed worker write. Existing
scripted tests always made the coordinator write, masking the optional path.
There was also an independent bookkeeping gap: memory poisoning labeled an
origin even when the replacement equaled the requested value.

## Changes and semantics

- `generation/runner.py`, `ScenarioRunner._execute_scenario`: reject memory LEP
  configurations without shared memory before inference. At the initial
  one-to-many handoff, stop an attempt with unfulfilled origins as
  `injection_target_unreachable`. Once that window closes, no later origin can
  satisfy the propagation semantics. Preserve expected count and real events;
  admission still rejects the attempt. Add private unmet-origin evidence on
  normal completion/window closure and backend/protocol failure paths.
- `leps/registry.py`, `evaluate_for_boundary`, `unfulfilled_origins`, `reset`:
  expose actual evaluation decisions, required/realized roles, boundary,
  budgets, and evaluation errors. Distinguish no eligible boundary, evaluated
  but unrealized target, fired trigger without mutation, and evaluation error.
  Do not replay probabilistic matchers or infer success from decisions.
- `leps/memory_poisoning.py`, `poison`, `record_write`, `get_origin_event_id`:
  compare with the original value and keep unchanged attempts out of poisoned
  memory/write-origin bookkeeping.
- `generation/stage_runner.py`, `run_stage`: count a memory origin only when
  its replacement changes the value and a memory store exists; retain private
  before/after hashes as mutation evidence.
- `benchmark/benchmark_runner.py`, `audit_injection_counts`: retain every
  mismatch and append the execution termination reason.

This is explicit handling of conditional runtime reachability, not a promise
that every scheduled attempt succeeds. No forced writes, fallback origin,
post-hoc expected-count changes, or relaxed audit are introduced. Successful
paths and benign coordination contracts stay unchanged. The unsuccessful
one-to-many path stops earlier; it does not generate a complete, nominally
perturbed workflow with zero perturbations. Backend failures retain their
original termination reason. Source-rewrite no-ops remain rejected.

## Validation

`tests/test_injection_reachability.py` adds 38 deterministic tests using a real
SciFact fixture and production configuration, runner, tools, LEPs and storage:

- All five LEPs across all five supported topology/mode combinations (25 cases).
- Optional coordinator write absent, and the identical benign path completing.
- A real stored memory replacement linked to its origin and differing from the
  original call; unchanged replacement rejected without a fake origin.
- Missing read prerequisite, unavailable memory configuration, evaluation error.
- Backend failure before any boundary for all five LEPs.
- Actual changed tool content, changed handoff hashes, and input-disregard
  instructions present in receiving model prompts.

Relevant suite: **207 passed** using
`/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m pytest` with:

```
tests/test_injection_reachability.py
tests/test_injection_count_regression.py
tests/test_injection_lifecycle_diagnosis.py
tests/test_memory_many_to_one.py
tests/test_runner_admission.py
tests/test_topology_targeting.py
tests/test_memory_integration.py
tests/test_fan_out_fan_in.py
tests/test_backend_retries.py
```

The broader `tests/test_benchmark_planning.py` run has 15 passes and five
pre-existing failures. Running the unchanged HEAD implementation in a separate
temporary baseline reproduced the same five failures: three fixed legacy
fixture-count assumptions, a legacy unsupported-LEP assumption, and a missing
legacy default smoke fixture. No production fixture files were changed.

These are deterministic execution tests and retrospective production evidence;
no new live model production run was launched. The exact original 97-case
snapshot and its reported extra-origin cases were not available in this set,
so this investigation does not claim to explain those extra-origin cases.

## Follow-up: replenishment, viability, and metric admission

The production runner does **not** replenish experimental cells. `run()` calls
`_execute()` once per plan entry, persists the result, then advances. Manifest
`num_repetitions` counts attempts, not accepted injections. The production job
requests one LEP repetition. `StageRunner._generate_with_retry()` makes at most
three attempts at a backend request (2s/4s backoff), without restarting the
scenario or resampling an injection path. `allow_retries=True` does not provide
a cell-filling loop. Production scripting waits for initial server readiness;
it does not replenish failed cells after execution.

Counting all saved coordinator/memory/one-to-many attempts, including accepted
traces, gives 71: 12 accepted, 58 zero-origin rejected, and one incomplete
one-origin run. All 12 accepted traces have a real, changed coordinator memory
value before initial fan-out. Thus the cell is *conditionally possible*, not
logically impossible or uniformly invalid. This observed 12/71 acceptance
fraction is descriptive; it does not establish independent retry probability
for every fixture. Nine of the 12 contain a non-coordinator memory retrieval;
three do not. A missing downstream retrieval after a valid injection is a
possible real non-propagation outcome, unlike a missing injection.

Recommendation for the current balanced production manifest: exclude this
triple by a joint LEP/topology/mode compatibility rule until the experimental
protocol is redesigned. Describe it as not supported by the current protocol
for reliable cell coverage, not as an impossible execution or proof that its
12 observed successes are invalid. Do not disable memory poisoning in the
other supported cells, or all one-to-many LEPs.

If retaining this cell is scientifically necessary, change the experimental
design explicitly: choose a task whose natural upstream work produces a
shared-memory artifact for multiple workers, with the same workflow in clean
and perturbed arms; alternatively define a distinct experiment that poisons an
existing shared-memory artifact before fan-out. The latter changes the current
MEMORY_WRITE intervention surface and needs its own documented implementation
and matching clean controls. Do not manufacture writes in the existing task.
Also examine clean-reference matching: the current planner normally emits
coordinator/worker benign references under `single_origin`, while the LEP cell
uses the initial coordinator turn of `one_to_many`.

Retry transient backend failures after service recovery with an explicit,
bounded attempt policy. Retrying missing-boundary paths is defensible only for
an explicitly conditional experiment (propagation given a naturally realized
eligible origin), with all rejection counts retained. It cannot guarantee
coverage where a fixture's reachability probability is zero, and accepting
only rare model-selected paths changes the population being measured. Never
retry because a valid injected trace failed to propagate. A replenishment
implementation would also need distinct attempt IDs, preserved rejected
artifacts, acceptance quotas, attempt caps, and explicit underfilled-cell
reporting; rerunning current scenario IDs can overwrite trace files.

### Metric verification result: directory isolation works, universal exclusion does not

The normal writer sends both `injection_target_unreachable` and
`backend_connection_error` traces to `rejected_traces/`. `PropagationAnalyzer`
loads only `traces/*_trace.json`; the standard shard merger copies only that
accepted directory. This keeps rejected attempts out of propagation analysis
in the normal output layout.

However, `_load_and_group_traces()` does not validate `dataset_eligible`,
termination reason, or expected origin counts. `_analyze_single_trace()` only
logs a warning for a missing origin and still constructs a result. Therefore
it is **not true** that these attempts cannot enter propagation metrics: if an
import/manual merge places them in `traces/`, they enter the denominator as
non-propagating runs. A backend failure after a partial injection is also
unsafe; testing only for at least one origin would not be a sufficient guard.

Executable verification with real SciFact execution and three clean reference
runs produced:

| Layout | Analyzed LEP runs | Outcome |
| --- | ---: | --- |
| Both rejected attempts in `rejected_traces/` | 0 | Correct exclusion |
| Identical files copied into `traces/` | 2 | Both counted as non-propagating; reported probability 0.0 |

A separate one-entry runner probe returned a rejected record: exactly one
`_execute` call occurred. `_summarize()` nevertheless reported one successful
run, zero failed runs, and a zero injection rate, because it counts errors and
all attempt records rather than dataset eligibility. These operational
summaries must not be interpreted as propagation probabilities or valid-cell
completion. Their rates include rejected attempts. The propagation analyzer's
coverage denominator likewise counts loaded LEP groups, not planned cells;
an entirely rejected cell disappears from that denominator.

Required metric hardening: validate admission at analysis ingestion, exclude
failed/incomplete/mismatched origins even when files are misplaced, distinguish
attempt yields from propagation conditional on valid injection, and calculate
coverage against the planned supported cells. Retain actual valid zero-
propagation outcomes. No further production code or manifest changes were made
in this follow-up; the verification identified this remaining admission gap.

The 71-attempt cell snapshot breaks down as follows: financial analysis has
9 accepted, 21 zero-origin rejected, and one incomplete injected attempt;
code review has 3 accepted and 8 zero-origin rejected; research synthesis has
0 accepted and 29 zero-origin rejected. This is further evidence against using
an aggregate acceptance fraction to promise per-fixture retry completion.
