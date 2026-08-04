# AgentGraphs — Task II: graph benchmark layer

Converts agent execution traces into temporal graphs and runs the
static → temporal model ladder for LEP detection and failure prediction.

```
traces/*.jsonl
  ├─ agentgraph.py   convert → entity-node graph → static (PyG) + temporal (DyGLib) tensors
  ├─ validate.py     data-quality guards (report, don't compensate)
  ├─ train.py        splits · metrics · model ladder (GAT / JODIE / TGN)
  └─ visualize.py    NetworkX structure / timeline / benign-vs-malignant views
```

## Run

```bash
pip install -r requirements.txt
python agentgraph.py --traces traces --out out --stage detect
python validate.py   --traces traces
python train.py      --data out --traces traces --model logreg --diagnose
python visualize.py  --traces traces --all --out figs
```

## Design

**Entities are nodes; events are edges.** Agents, tools, memory store, user,
system are persistent nodes. Each event is a timestamped directed edge
`source → target`. One graph = one run (`trace_id`, sorted by `event_id`).

*Why not events-as-nodes:* TGN's memory is per-persistent-node. If each event
were a node, every node would be a one-shot with no history, so memory could
not carry an early injection forward to a later failure — collapsing TGN toward
a static model.

**Features and labels are split at the source.** Model inputs are structural
only. Everything the labeller added (`lep_*`, `downstream_failure`,
`caused_by_event`, `propagates_to`) is held-out ground truth. Feeding
propagation in and predicting propagation would leak the answer.

**Both model views come from one IR, one encoder, one label set** — the
experimental control. Swapping the model is the only thing that changes.

**Schema drift is one config block** at the top of `agentgraph.py`. A new attack
method or agent framework changes the trace fields, not the pipeline.

## Files

| file | role |
|---|---|
| `agentgraph.py` | convert + export (static + temporal tensors) |
| `validate.py` | data-quality guards |
| `train.py` | eval harness + model ladder interface |
| `visualize.py` | NetworkX visualisations |
| `DESIGN.md` | full design doc |

## Status

Converter, export, validator, harness and visualisation run end-to-end on real
traces. GAT / JODIE / TGN are declared against the harness interface and wire
into PyG / DyGLib next. See `DESIGN.md §8` for current data blockers.
