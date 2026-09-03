# Agent Graph v3 - Codebase Summary

## 1. Overall Architecture

AgentGraph v3 is a benchmark for studying how controlled Local Execution Perturbations (LEPs) propagate, diffuse, recover, or cause downstream failures across multi-agent execution graphs. The system:

1. **Defines topologies** (execution graphs of agents with handoff rules)
2. **Registers LEPs** (boundary-aware injection points that simulate controlled perturbations)
3. **Generates traces** (simulated agent execution with injected perturbations)
4. **Evaluates traces** (determines exposure, consumption, propagation, recovery, downstream failure, and task success)

### Directory Structure

```
agent-graph-v3/
├── schemas/          # Data models (LEPConfig, TraceEvent, TopologyConfig, etc.)
├── leps/             # LEP implementations (tool corruption, memory poisoning, etc.)
├── generation/       # Trace generation engine (runner.py, stage_runner.py)
├── workflows/        # Topology definitions and strategies
├── tasks/            # Task families (financial_analysis, code_review, etc.)
├── evaluators/       # Trace evaluators
├── environment/      # Workspace, file system simulation
├── pilot/            # Pilot harness for running experiments
├── benchmark/        # Benchmark runner
└── manifest/         # Task and LEP manifest files
```

## 2. Topology System

### TopologyConfig (`generation/topology.py`)

The core topology definition:

```python
@dataclass
class TopologyConfig:
    topology_id: str           # "linear_2", "linear_3", "review_loop",
                               # "branch_and_verify", "coordinator_workers"
    display_name: str
    stages: List[Stage]        # Each stage = one agent turn
    handoff_rules: List[HandoffRule]  # How payloads flow between stages
    exit_stage: str            # Which stage can terminate
    max_iterations: int
    max_review_cycles: int
    metadata: Dict[str, Any]
```

**Stage** represents one agent's turn:
- `stage_id`: Unique identifier
- `agent_role`: Semantic role ("researcher", "analyst", "coordinator", etc.)
- `agent_id`: Actual agent instance
- `stage_type`: "execute", "coordinate", "branch", "merge"
- `max_turns`: Max turns this stage can take
- `can_handoff`: Whether this stage can emit handoffs
- `can_finalize`: Whether this stage can terminate workflow

**HandoffRule** defines flow:
- `from_stage` / `to_stage`: Agent roles
- `required`: Whether handoff is mandatory
- `label_on_ignore` / `label_on_consume`: Labels for tracking

### Available Topologies

1. **linear_2**: A → B → done (2 agents, sequential)
2. **linear_3**: A → B → C → done (3 agents, sequential)
3. **review_loop**: Producer → Reviewer → (back to producer) → done
4. **branch_and_verify**: Researcher branch + Analyst branch, both converging at Verifier → done
5. **coordinator_workers**: Coordinator → Worker A, Worker B, Worker C → Coordinator → FINAL

### TopologyFactory

Located in `generation/topology.py`, builds TopologyConfig instances from topology_id + task family.

## 3. LEP (Local Execution Perturbation) System

### LEPConfig Schema

```python
@dataclass
class LEPConfig:
    code: str                          # "LEP_TOOL_RESULT_CORRUPTION"
    name: str                          # Human-readable name
    category: str                      # Category for grouping
    description: str                   # What this LEP does
    target_agent: str                  # Which agent to target
    target_tool: str                   # Which tool to corrupt
    severity: str                      # "low", "medium", "high"
    supports_deterministic_provenance: bool  # Can we track propagation?
    propagation_mode: str              # How perturbation spreads
    task_family: str                   # Task family this LEP applies to
    variant: str                       # Variant of the LEP
    topology_target: Optional[str]     # "branch:researcher", "worker:worker_a", etc.
    injection_stage: Optional[str]     # When to inject
```

Note: `propagation_mode` is a scenario-level concept owned by `WorkflowConfig`, not LEPConfig. The LEPConfig field above documents what the codebase currently stores, but the authoritative owner is the scenario/workflow level.

### LEP Registry (`leps/registry.py`)

Maps LEP codes to implementations:

```python
LEP_REGISTRY = {
    "LEP_TOOL_RESULT_CORRUPTION": ToolResultCorruptionLEP,
    "LEP_INDIRECT_PROMPT_INJECTION": IndirectPromptInjectionLEP,
    "LEP_MEMORY_POISONING": MemoryPoisoningLEP,
    "LEP_HANDOFF_CORRUPTION": HandoffCorruptionLEP,
    "LEP_INPUT_DISREGARD": InputDisregardLEP,
}
```

### Boundary Routing

LEPs are only evaluated at semantically correct boundaries:

```python
BOUNDARY_LEPS = {
    "tool_result": {
        "LEP_TOOL_RESULT_CORRUPTION",
        "LEP_INDIRECT_PROMPT_INJECTION",
    },
    "agent_handoff": {
        "LEP_HANDOFF_CORRUPTION",
        "LEP_INPUT_DISREGARD",
    },
    "memory_write": {
        "LEP_MEMORY_POISONING",
    },
}
```

This means:
- **Tool result LEPs** only fire when a tool result is returned
- **Handoff LEPs** only fire during agent handoffs
- **Memory LEPs** only fire during memory writes

### LEPOrchestrator

The central coordinator that:
1. Creates LEP instances from LEPConfigs
2. Evaluates triggers against trace events (boundary-aware)
3. Coordinates injection across tool calls, memory, and handoffs
4. Tracks propagation through events
5. Resets state between runs

Key methods:
- `register_lep(config)`: Register a LEP
- `set_topology(topology, propagation_mode)`: Validate and resolve topology_target
- `evaluate_for_boundary(event)`: Evaluate only LEPs for this event's boundary
- `fire_injection(code, event)`: Execute a LEP injection
- `mark_fired_origin(code, target)`: Record that a LEP fired at an origin point
- `set_max_origins(code, n)`: Configure origin budget before execution
- `get_firing_state(code)`: Inspect per-LEP firing state
- `reset()`: Clear all state for new run

### LEP Implementations

#### 1. ToolResultCorruptionLEP
- **Boundary**: tool_result
- **Mechanism**: Corrupts tool output (wrong values, missing data, etc.)
- **Trigger**: When a specific tool is called by a target agent
- **Effect**: Returns perturbed result instead of real one

#### 2. IndirectPromptInjectionLEP
- **Boundary**: tool_result
- **Mechanism**: Injects malicious instructions into tool output
- **Trigger**: When tool result contains injection payload
- **Effect**: Agent follows injected instructions instead of original task

#### 3. MemoryPoisoningLEP
- **Boundary**: memory_write
- **Mechanism**: Writes poisoned data to memory store
- **Trigger**: When agent writes to memory
- **Effect**: Subsequent agents read poisoned data

#### 4. HandoffCorruptionLEP
- **Boundary**: agent_handoff
- **Mechanism**: Corrupts handoff payload
- **Trigger**: When handoff occurs between specific agents
- **Effect**: Receiving agent gets corrupted data

#### 5. InputDisregardLEP
- **Boundary**: agent_handoff
- **Mechanism**: Makes agent disregard incoming handoff
- **Trigger**: When handoff arrives at target agent
- **Effect**: Agent ignores handoff and acts on incomplete info

## 4. Trace Generation

### Runner (`generation/runner.py`)

Main entry point for trace generation:
1. Loads task and LEP configs
2. Builds topology
3. Creates LEP orchestrator
4. Passes topology and propagation_mode to orchestrator
5. Runs agents through topology stages
6. Records events and injections

### StageRunner (`generation/stage_runner.py`)

Executes individual stages:
1. Runs agent with tools
2. Records events at boundaries
3. Evaluates LEP triggers at boundaries
4. Applies injections when LEPs fire
5. Handles handoffs between stages

### Event Types (Current Implementation)

```python
class TraceEventType(Enum):
    USER_INPUT = "user_input"
    AGENT_THINK = "agent_think"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    HANDOFF = "handoff"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    SYSTEM_INIT = "system_init"
    FINALIZE = "finalize"
    ERROR = "error"
```

### Event Types (Target Design)

The newer event-as-node design uses these names:
- `REASONING` instead of `AGENT_THINK`
- `AGENT_HANDOFF` instead of `HANDOFF`
- `MEMORY_RETRIEVAL` instead of `MEMORY_READ`
- `FINAL_RESPONSE` instead of `FINALIZE`
- `TOPOLOGY_TRANSITION` for stage changes

These names are aspirational. The current codebase uses the older enum names above. Migration to the target names is future work.

## 5. Propagation Modes

### Authoritative Propagation Invariants

```
single_origin:
    exactly 1 injection origin

one_to_many:
    exactly 1 shared upstream injection origin
    N downstream consumers
    no per-consumer reinjection

many_to_one:
    exactly 1 origin per selected worker
    M2O-1 = 1 origin
    M2O-2 = 2 origins
    M2O-3 = 3 origins
    all selected worker outputs converge at final Coordinator

all modes:
    propagation emerges from actual execution dependencies
    LEP labels never create graph edges
    recovery + task_success is a valid outcome
```

### How propagation_mode Affects Execution

The `propagation_mode` is set at scenario level in `WorkflowConfig`. It affects:

- **Origin budget**: How many times a LEP can fire (set via `set_max_origins()`)
- **Valid targets**: `topology_target` validation rules per mode
- **Propagation tracking**: How edges and events are annotated in the trace

#### O2M Shared-Artifact Semantics

In `one_to_many` mode, the LEP mutates one real upstream event/artifact once. All workers consume that same lineage. There is no per-consumer reinjection — the same perturbation flows through the execution graph to every downstream consumer.

### Current Implementation Status

- `WorkflowConfig.propagation_mode` field: implemented
- `LEPFiringState` with origin budget: implemented
- `evaluate_for_boundary()` origin budget check: implemented
- Actual O2M/M2O firing logic in stage_runner: **not yet implemented**

## 6. Topology-Aware Targeting

### topology_target Field

LEPs can specify exactly where to inject using `topology_target`:
- `None`: No explicit topology restriction; canonical scenarios should still resolve a default structural target. A missing config should not allow unrestricted firing — the system resolves a canonical target for the topology.
- `"branch:<role>"`: Target a specific branch in branch_and_verify
- `"worker:<role>"`: Target a specific worker in coordinator_workers
- `"upstream:<role>"`: Target upstream coordinator (one-to-many only)

### Validation

The `topology_target` module validates:
- Prefix matches topology type (`branch:` only for `branch_and_verify`)
- Role exists in topology
- `upstream:` only valid for `coordinator_workers` + `one_to_many`

### Default Targets

Canonical injection targets per topology:
- **linear_2/3**: First agent in sequence
- **review_loop**: Producer at first iteration
- **branch_and_verify**: Researcher branch
- **coordinator_workers**: Depends on propagation_mode and topology_target

## 7. Firing State Management

### LEPFiringState

Tracks per-LEP, per-target firing across scenario:

```python
@dataclass
class LEPFiringState:
    max_origins: int                         # Max times this LEP can fire
    fired_origin_count: int                  # How many times it has fired
    fired_targets: set                       # Which targets were perturbed
    eligible_occurrence_counts: Dict[str, int]  # Per-target occurrence counts
```

State is maintained per LEP code and per target. There is no global `already_fired` set — the origin budget and target tracking together enforce correct semantics.

This enables:
- **Origin budget enforcement**: LEP fires at most N times
- **Target tracking**: Know which stages were perturbed
- **Mode-aware firing**: Different semantics for single_origin vs many-to-one vs one-to-many

### Integration Points

- `evaluate_for_boundary()`: Checks origin budget before evaluating
- `mark_fired_origin(code, target)`: Records when LEP fires (called from stage_runner)
- `set_max_origins(code, n)`: Configures budget before execution
- `get_firing_state(code)`: Inspect state for debugging or evaluation

## 8. Dependencies & Graph Building

### Trace Dependencies

The system tracks:
- Which events depend on which (execution dependency / information-flow chain)
- Whether edges carry perturbations
- Propagation roles (origin, transfer, transformation, etc.)

### Edge Annotations

```python
@dataclass
class EdgeAnnotation:
    edge_id: str
    source_event_id: str
    target_event_id: str
    relation_type: str              # "information_flow"
    carries_perturbation: bool
    propagation_role: str           # ORIGIN, TRANSFER, PROPAGATED, etc.
    causal_strength: float          # execution dependency / information-flow strength (0.0-1.0)
    transformation_desc: str
    metadata: Dict[str, Any]
```

Propagation roles:
- **ORIGIN**: First injection event
- **TRANSFER**: Direct handoff/forward
- **TRANSFORMATION**: Information was modified
- **STORAGE**: Written to memory/file
- **CONVERGENCE**: Multiple sources combined
- **RECOVERY**: Detection/correction
- **TERMINAL_IMPACT**: Final failure event
- **PROPAGATED**: Edge carries perturbation from upstream

Note: Injection is fundamentally a node/event property, not something that should create graph structure. Edge annotations may indicate they carry perturbed information, but injection itself is not represented as a synthetic edge role.

Note: `causal_strength` is an optional edge feature representing execution dependency / information-flow strength. The system does not perform formal causal inference.

## 9. Task Families

Task families define the problem domain:
- **code_review**: Code review and security audit
- **financial_analysis**: Financial report analysis
- **research_synthesis**: Multi-source research
- **competitive_intelligence**: Competitive intelligence analysis

Each family has:
- Task specification
- Workspace fixtures (input files)
- Evaluator (determines success/failure)
- Agent prompts

## 10. Key Integration Points

### Scenario Execution Flow

1. **Setup**:
   - Load LEP configs from manifest
   - Build topology
   - Register LEPs with orchestrator
   - Set topology and propagation_mode on orchestrator
   - Configure origin budgets per LEP

2. **Execution**:
   - For each stage in topology:
     - Run agent
     - At each boundary:
       - Evaluate LEP triggers
       - If fired: apply injection, mark origin
     - Handle handoffs
     - Record events

3. **Evaluation**:
   - Analyze trace for perturbation propagation
   - Determine exposure, consumption, propagation, recovery, downstream failure, and task success
   - Compare with ground truth

### Data Flow

```
Task Spec + LEP Configs + WorkflowConfig(propagation_mode)
    ↓
TopologyFactory → TopologyConfig
    ↓
LEPOrchestrator.register_leps(configs)
LEPOrchestrator.set_topology(topology, propagation_mode)
LEPOrchestrator.set_max_origins(code, n)  # per LEP, per mode
    ↓
StageRunner executes stages
    ↓
At boundaries: evaluate_for_boundary() → fire_injection() → mark_fired_origin(code, target)
    ↓
Trace with injections
    ↓
Evaluator determines exposure, consumption, propagation, recovery, downstream failure, task success
```

## 11. Current State & Recent Changes

### Implemented

- Topology definitions and graph building (5 topologies)
- 5 LEP implementations with boundary-aware routing
- LEPOrchestrator with firing state management
- Topology-aware targeting with validation
- Pilot harness for running experiments
- Trace generation with event recording
- Propagation mode field and origin budget enforcement
- Edge annotation system with propagation roles

### Planned / Not Yet Implemented

- O2M/M2O firing logic in stage_runner
- Per-target occurrence tracking for M2O-2, M2O-3
- O2M shared-artifact semantics enforcement
- Complete benchmark runner integration
- Migration to target event names (REASONING, AGENT_HANDOFF, etc.)
- Comprehensive test coverage

### Recent Commits
1. Final topology and LEP wired in
2. Pilot memory poisoning working
3. Fixed role-name mismatch
4. Memory subsystem for LEP_MEMORY_POISONING
5. Removed is_abnormal_event flag

### Current Branch
`refactor/v3-benchmark` - Working on wiring full benchmark, adding propagation mode support

### Uncommitted Changes
- `generation/runner.py`: Propagation mode passed to orchestrator
- `leps/registry.py`: LEPFiringState, origin budget, mark_fired_origin
- `leps/topology_target.py`: Full validation including upstream: prefix
- `leps/tool_result_corruption.py`: Modified
- `schemas/scenario.py`: Added propagation_mode field
- `schemas/edge_labels.py`: Added PROPAGATED and IS_INJECTION roles
- `pilot/config.py`: Updated for branch_and_verify topology
- `pilot/run_pilot.py`: Updated for branch_and_verify topology
- Pilot output files (ground truth, records, traces)
