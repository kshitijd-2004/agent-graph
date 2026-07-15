# AgentGraphs — Design Document (Task II)

**Author:** Sashank · **Status:** draft for discussion · **Date:** 2026-07-14

Task II = the graph benchmark layer. Task I (trace collection/schema) is KJ's.

---

## 1. Framing: two stages, in order

**Goal.** Test whether Local Execution Perturbations (LEPs) in multi-agent
traces are (a) *structurally detectable* and (b) *predictive* of downstream
failure.

- **Stage 1 — Detectability.** Is the perturbation signal present and separable
  in the graph at all? Can a model tell a perturbed run from a clean one, and
  localise *which* event was perturbed?
- **Stage 2 — Prediction.** Given only events *before* a failure, can we
  forecast that failure?

**The order is not cosmetic.** If risk is not detectable, prediction is
meaningless — there is nothing to predict *from*. Stage 1 is the validity check
that licenses Stage 2. Collapsing them into one "prediction" story would
misrepresent what we have actually shown.

**Central hypothesis (the ladder).** Performance should climb
**static → temporal → temporal-with-memory**. The contribution is the *ordering
and its explanation*, not any one model winning. A null result — static matches
TGN — is a real finding: it would mean risk here is locally detectable and does
not require temporal propagation.

---

## 2. Shared graph substrate

Nodes = **entities** (agents, tools, `user`, `system`, `internal`), persistent
with stable ids. Edges = **events**, timestamped, directed `source → target`.
One graph = one run (`trace_id`, sorted by `event_id`).

*Why entity-as-node:* TGN's memory is **per-persistent-node**. If events were
nodes, each would be a one-shot with no history, so memory could not accumulate
an early injection and carry it forward — collapsing TGN toward a static model.
Persistent entities are what make the temporal mechanism testable.

**Fairness = the real engineering.** Every model consumes the same IR, the same
encoder, the same labels, the same splits. Only the model changes. If the static
baseline and TGN saw different features, any gap would be a pipeline artifact,
not evidence about propagation.

---

## 3. Anomaly classification — static

**Setup.** Time is discarded. One aggregated graph per run: PyG `Data` with
`x` (node features), `edge_index`, `edge_attr` (encoded event features).

**Models.** Rule/structural floor (degree, centrality, vanilla-vs-perturbed
output diff) → **GAT** (attention-weighted message passing) → optionally MPNN.

**How an anomaly is classified.**
- *Edge/event level:* a classification head over each edge's representation —
  concatenate `[h_src ‖ h_dst ‖ edge_attr]` → MLP → P(this event is anomalous).
  GAT's attention lets a node weight a suspicious neighbour more heavily than a
  benign one, which is the mechanism we are testing at the static level.
- *Run/graph level:* pool node embeddings (mean/max/attention) → MLP →
  P(this run contains a perturbation).

**What static can and cannot see.** It can capture *structural* irregularity —
an odd tool being called, an unusual agent→tool pattern, an event whose local
neighbourhood looks wrong. It **cannot** represent that an event at t=13 caused
an event at t=95, because it has no ordering. This is exactly why it is the
control: if static already separates the classes, temporal machinery buys us
nothing and we should say so.

---

## 4. Anomaly classification — temporal

**Setup.** The ordered event stream: `(src, dst, t, msg)` consumed in
timestamp order.

**Models.** **JODIE** (evolving embeddings, weaker memory/aggregation — the
"time helps" control) → **TGN** (primary). **APAN** noted as future
deployment work (async, low-latency); not in the prototype.

**Library.** **DyGLib** — implements JODIE, TGN, APAN et al. behind a unified
interface with a shared training harness. Using it, rather than three separate
codebases, is what makes the ladder an apples-to-apples comparison.

**How an anomaly is classified.**
- Each entity carries a **memory vector**, updated by a GRU every time an event
  touches it.
- At event *e* = (u → v, t): read the current memories of *u* and *v*, compute
  temporal-attention embeddings over their recent neighbours (with time
  encoding), then `[z_u(t) ‖ z_v(t) ‖ msg_e]` → MLP → P(anomalous / will lead
  to failure).
- **The mechanism under test:** when a perturbation hits `agent_002` at event
  13, it writes into that agent's memory. The memory persists. So when the same
  agent acts at event 95, the model scores that action against a state that
  *already encodes* the earlier compromise. Static cannot do this; that gap is
  the hypothesis.
- **Key ablation: TGN with vs. without memory.** This isolates whether *memory*
  (not merely time) is what captures propagation. Without this ablation, a TGN
  win is uninterpretable.

**Stage 2 constraint.** Strictly causal — at event *e* the model may use only
events with `t < t_e`. No peeking at the failure or at any label field.

---

## 5. Metrics

- **AUC-PR / average precision** — labels are heavily imbalanced; accuracy is
  meaningless here.
- **Localisation accuracy** (Stage 1) — is the flagged event the injected one?
- **Lead time** (Stage 2) — how many events *before* the failure can we flag it?
  A model that only fires *at* the failure has no predictive value. This is the
  headline number for the temporal claim.
- **Memory-ablation delta** — TGN minus TGN-without-memory.

---

## 6. Benchmarks and related work

**Attack side — how perturbation enters and propagates.** These motivate the
project and are candidate *realistic* perturbation sources (vs. hand-crafted
LEPs):
- **AgentPoison** (NeurIPS 2024) — backdoors an agent by poisoning its memory /
  RAG base; trigger → retrieved poison → harmful action. The canonical
  injection→delayed-effect structure.
- **MemMorph** (2026) — memory poisoning that biases *tool selection*; the
  failure is a wrong tool call, i.e. an edge-level event. Notably, the bias
  persists across turns and re-activates on each retrieval — temporal
  persistence, which is what TGN memory should catch.
- **ShadowMerge** (2026) — poisons **graph-structured** agent memory via
  relation-channel conflicts; evaluated on ToolEmu. Closest to our substrate.
- **Autonomous LLM Agent Worms** (2026) — multi-hop, cross-agent, *temporal
  re-entry* propagation. The cascade we want to predict, at the multi-agent
  scale we are targeting.

**Environment / trace sources.** AgentDojo (single-agent, prompt-injection +
tool safety) and ToolEmu (single-agent, tool-execution emulation) — both give
agent→tool traces but no handoff edges.

**Detection side (related, not a benchmark).** **SentinelAgent** — a runtime
graph-based MAS monitor. Methodological reference for graph schema and
anomaly tiers; it is a *monitoring framework*, not a dataset.

**Positioning.** The four attack papers are the offence; AgentGraphs is the
**early-warning defence** — predicting the downstream failure from the early
signal. ShadowMerge (graph memory) and the Worms paper (temporal multi-hop) are
the closest counterparts.

---

## 7. Implementation status

**Built and running on KJ's real traces:**
- `agentgraph.py` — the whole pipeline: JSONL → entity-node graph IR → encoded
  tensors in **both views**: static (PyG `Data`) and temporal (DyGLib stream:
  `src, dst, t, msg, y`). The field-config block at the top is the single point
  of update when the trace schema drifts.
- `validate.py` — data-quality guards. Findings in §8.

Verified: 25 runs, 11 nodes, 156 events/run, `edge_attr (156, 73)`,
temporal `msg (156, 73)`. Both views export to `.npz`.

**Not built:** the models. GAT / JODIE / TGN are next, and are **blocked on
usable labels** (§8), not on engineering.

---

## 8. Data blockers (found by running the validator on the current traces)

These are not cosmetic — each one independently prevents an experiment.

1. **`downstream_failure` is an artifact.** In all 14 labelled runs the failure
   sits on the **last** event with `failure_type = non_termination` — i.e. the
   trace hit max length. It is not a consequence of any injection.
2. **Benign runs carry the same failure label.** 8 runs have
   `downstream_failure = True` with **zero** injected LEPs. If clean runs "fail"
   too, the label does not separate the classes — **Stage 1 is untestable on
   it**, and a model would learn "trace length ⇒ failure".
3. **No causal ground truth.** `caused_by_event` and `propagates_to` are
   **entirely empty** across every trace. Without them the injection→failure
   link does not exist, so propagation cannot be measured *or* validated.
4. **LEP taxonomy conflict.** Code `2.3` means *Task Derailment* in some files
   and *Tool Corruption* in others; `3.1` is *Premature Termination* in the PDF
   but *Output Corruption* in the data, tagged `FC2` where its number implies
   `FC3`.
5. **Injection density ~28–33%.** 43/156 events marked `lep_injected`. A
   *Local* Execution Perturbation should be sparse; at this density there is
   barely an early-signal→late-failure gap left to model.
6. **`traces_new/` has injections but zero failures**; `traces/` has failures
   but they are artifacts. **Neither folder currently supports Stage 2.**

**What we need to unblock:** sparse injections (1–3 per run), a failure that is
*caused by* the injection and absent from the paired benign run, and
`caused_by_event` / `propagates_to` populated so the gap is measurable.

---

## 9. Scaling and integration

- **Schema drift** is absorbed in one file (top of `agentgraph.py`); no field name is
  hard-coded elsewhere.
- **Volume:** conversion is O(events) and streams per-run; the current 25 runs ×
  156 events is trivial. Scaling to 10³–10⁴ runs needs only batched `.npz`
  shards, which the export layer already emits per run.
- **Encoder:** currently a deterministic hashing text encoder (zero deps). For
  real experiments, swap to a **frozen** sentence embedder — one line to swap, and it must stay fixed across every model in the ladder.
- **Multi-agent is already supported:** the current traces have 2 agents, 5
  tools, and `agent_handoff` edges — so handoff workflows need no schema change.
- **New workflows / attack sources** (AgentPoison-, ShadowMerge-style) plug in
  as new trace folders; the converter is agnostic to how the perturbation was
  produced.

---

## 10. Sequencing

1. **Now (no data dependency):** run GAT on the current graphs to validate the
   full path end-to-end — structural sanity, not a scientific result.
2. **Once labels are fixed:** Stage 1 (separability + localisation), then Stage 2
   (prediction + lead time) with the full ladder and the memory ablation.
3. **Later:** APAN / deployment variant; realistic attack-derived perturbations;
   handoff-heavy workflows.

---

## 11. Open questions for discussion

- Are `lep_severity` / `lep_location` legitimate model *features*, or labels?
  (Currently held out — they are labeller-added, so treating them as inputs
  risks leakage.)
- Should `internal` be a single shared node or one per agent? It is currently a
  shared sink for `reasoning` events, which may create false structural
  coupling between agents.
- Stage 2 target granularity: per-event failure forecasting, or run-level?
- Which frozen sentence embedder, and what dimension?
