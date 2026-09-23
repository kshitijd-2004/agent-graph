"""Training supervision reuses production behavioral propagation analysis."""
from copy import deepcopy

import pytest
import torch

from benchmark.propagation_analysis import PropagationAnalyzer
from generation.feature_schema import OBSERVABLE_NODE_FEATURE_DIM
from training.pipeline import DetectorPipeline, PipelineResult
from test_behavioral_anomaly import (
    action, BAD, FINANCIAL, _mk_trace, _mk_lep_trace,
)


@pytest.fixture
def examples(tmp_path, monkeypatch):
    monkeypatch.setattr(PropagationAnalyzer, "_load_fixture_spec",
                        staticmethod(lambda ref: FINANCIAL))
    events = [action(i, f"step{i}", parent=i-1 if i else None) for i in range(3)]
    clean = [_mk_trace(f"clean{i}", deepcopy(events), repetition_index=i,
                      task_family="financial_analysis", fixture_id="synthetic")
             for i in range(10)]
    injected = []
    for i in range(10):
        for positive in (False, True):
            changed = deepcopy(events)
            changed[0].event_labels.is_injection_origin = True
            changed[0].output_text = BAD
            if positive:
                changed[2].output_text = BAD
            trace = _mk_lep_trace(f"lep{i}-{positive}", changed,
                ["LEP_TOOL_RESULT_CORRUPTION"], repetition_index=i,
                task_family="financial_analysis", fixture_id="synthetic")
            # Deliberately oppose task correctness and the requested target.
            trace.labels.downstream_failure = not positive
            injected.append(trace)
    return DetectorPipeline(tmp_path, device="cpu"), clean, injected


def test_real_behavioral_targets(examples):
    pipeline, clean, injected = examples
    clean[0].labels.downstream_failure = True
    assert pipeline.build_anomaly_labels(clean + injected) == [0.] * 10 + [0., 1.] * 10
    # An anomalous injection event alone is still not propagation.
    origin_only = deepcopy(injected[1])
    origin_only.events = origin_only.events[:1]
    assert pipeline.build_anomaly_labels(clean + [origin_only])[-1] == 0.
    disconnected = deepcopy(injected[1])
    disconnected.events[2].depends_on = []
    assert pipeline.build_anomaly_labels(clean + [disconnected])[-1] == 0.


def test_memory_reference_and_insufficient_coverage(examples, monkeypatch):
    pipeline, clean, injected = examples
    memory = deepcopy(clean)
    for trace in memory:
        trace.metadata["execution_variant"] = "memory_enabled"
    trace = deepcopy(injected[1])
    trace.metadata["lep_codes"] = ["LEP_MEMORY_POISONING"]
    with pytest.raises(ValueError, match="memory_enabled"):
        pipeline.build_anomaly_labels(clean + [trace])
    seen = []
    original = PropagationAnalyzer._analyze_single_trace
    def inspect(self, trace, ref, *args, **kwargs):
        seen.append(ref.execution_variant)
        return original(self, trace, ref, *args, **kwargs)
    monkeypatch.setattr(PropagationAnalyzer, "_analyze_single_trace", inspect)
    pipeline.build_anomaly_labels(clean + memory + [trace])
    assert seen == ["memory_enabled"]
    with pytest.raises(ValueError, match="distinct repetitions"):
        pipeline.build_anomaly_labels([clean[0]] * 10 + [injected[0]])


def test_target_and_perturbation_labels_are_not_features(examples):
    pipeline, clean, injected = examples
    trace = deepcopy(injected[1])
    def encode(label):
        graph = pipeline.graph_builder.build(trace, topology_name="linear",
            task_family="financial_analysis", strict=False)
        return pipeline.encode_graphs([graph], [label])
    static_before, temporal_before = encode(1.)
    trace.labels.downstream_failure = not trace.labels.downstream_failure
    for event in trace.events:
        event.event_labels.is_injection_origin = False
    static_after, temporal_after = encode(0.)
    assert static_before[0].y.item() == 1.
    assert static_after[0].y.item() == 0.
    assert static_before[0].x.shape[1] == OBSERVABLE_NODE_FEATURE_DIM
    assert torch.equal(static_before[0].x, static_after[0].x)
    assert torch.equal(static_before[0].edge_index, static_after[0].edge_index)
    assert torch.equal(temporal_before[0].node_features, temporal_after[0].node_features)
    assert temporal_before[0].edge_features.numel() == 0


@pytest.mark.parametrize("families", [["static_gnn", "tgnn", "hybrid"], ["static_gnn"]])
def test_all_families_share_targets_and_grouped_splits(examples, monkeypatch, families):
    pipeline, clean, injected = examples
    monkeypatch.setattr(pipeline, "load_traces", lambda: (clean, injected))
    expected = dict(zip([t.execution_id for t in clean + injected],
                        [0.] * 10 + [0., 1.] * 10))
    received = {}
    def capture(name, dataset):
        splits = []
        for split in ("train", "val", "test"):
            graphs = getattr(dataset, split + "_graphs")
            labels = getattr(dataset, split + "_labels")
            mapping = {}
            for graph, label in zip(graphs, labels):
                assert label == expected[graph.execution_id]
                assert graph.y.item() == label if hasattr(graph, "y") else graph.label == label
                mapping[graph.execution_id] = label
            splits.append(mapping)
        received[name] = splits
        return PipelineResult(name, 0., 0,
            {"labels": list(splits[2].values()), "predictions": list(splits[2].values())}, [])
    monkeypatch.setattr(pipeline, "_train_static_gnn",
                        lambda dataset, *args, **kwargs: capture("static_gnn", dataset))
    monkeypatch.setattr(pipeline, "_train_temporal_detector",
                        lambda name, dataset, **kwargs: capture(name, dataset))
    original = pipeline._evaluate_heuristics
    def heuristics(dataset, benign, **kwargs):
        capture("heuristics", dataset)
        return original(dataset, benign, **kwargs)
    monkeypatch.setattr(pipeline, "_evaluate_heuristics", heuristics)
    results = pipeline.run(detector_types=families, snapshot_interval=1)
    assert set(received) == set(families) | {"heuristics"}
    assert all(splits == received["static_gnn"] for splits in received.values())
    group_by_id = {t.execution_id: pipeline._task_instance_id(t) for t in clean + injected}
    groups = [{group_by_id[eid] for eid in split} for split in received["static_gnn"]]
    assert all(groups[i].isdisjoint(groups[j]) for i in range(3) for j in range(i))
    for name in ("random", "degree", "temporal"):
        assert results[name].test_metrics["labels"] == list(received["heuristics"][2].values())
        assert set(results[name].test_execution_ids) == set(received["heuristics"][2])
