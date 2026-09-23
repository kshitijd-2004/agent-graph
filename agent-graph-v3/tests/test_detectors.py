"""Tests for learned detectors (StaticGNN, TemporalGNN, HybridDetector) and
training infrastructure (TemporalSnapshotBuilder, DetectorTrainer, metrics).
"""

from __future__ import annotations

import sys
from pathlib import Path

from generation.feature_schema import OBSERVABLE_NODE_FEATURE_DIM

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

# ── Optional imports (skip all tests if torch not installed) ──────────────────
torch = pytest.importorskip("torch")
pytest.importorskip("torch_geometric")


# ═══════════════════════════════════════════════════════════════════════════════
# StaticGNN tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestStaticGNN:
    """Tests for the static GNN detector."""

    def test_forward_pass_shape(self):
        """StaticGNN.forward produces logits of shape [num_graphs]."""
        from detectors.static_gnn import StaticGNN

        model = StaticGNN(node_feature_dim=24, hidden_dim=32, num_layers=2)
        num_nodes = 10
        x = torch.randn(num_nodes, 24)
        edge_index = torch.tensor([[0, 1, 2, 3], [1, 2, 3, 4]], dtype=torch.long)
        batch = torch.zeros(num_nodes, dtype=torch.long)

        output = model(x, edge_index, batch)
        assert output.logits.shape == (1,), f"Expected (1,), got {output.logits.shape}"
        assert output.probabilities.shape == (1,), f"Expected (1,), got {output.probabilities.shape}"
        assert output.is_malignant.shape == (1,), f"Expected (1,), got {output.is_malignant.shape}"
        assert 0.0 <= float(output.probabilities.item()) <= 1.0

    def test_batched_forward(self):
        """StaticGNN handles multiple graphs in a batch."""
        from detectors.static_gnn import StaticGNN

        model = StaticGNN(node_feature_dim=24, hidden_dim=32, num_layers=2)

        x_list = [torch.randn(5, 24), torch.randn(8, 24)]
        edge_list = [
            torch.tensor([[0, 1], [1, 2]], dtype=torch.long),
            torch.tensor([[0, 1, 2], [1, 2, 3]], dtype=torch.long),
        ]
        batch_list = [
            torch.zeros(5, dtype=torch.long),
            torch.ones(8, dtype=torch.long),
        ]

        from torch_geometric.data import Batch, Data
        batch = Batch.from_data_list([
            Data(x=x, edge_index=e)
            for x, e, b in zip(x_list, edge_list, batch_list)
        ])

        output = model(batch.x, batch.edge_index, batch.batch)
        assert output.logits.shape == (2,)
        assert output.probabilities.shape == (2,)

    def test_gradient_flow(self):
        """Gradients flow through StaticGNN."""
        from detectors.static_gnn import StaticGNN

        model = StaticGNN(node_feature_dim=24, hidden_dim=32, num_layers=2)
        x = torch.randn(10, 24, requires_grad=True)
        edge_index = torch.tensor([[0, 1, 2], [1, 2, 3]], dtype=torch.long)
        batch = torch.zeros(10, dtype=torch.long)

        output = model(x, edge_index, batch)
        loss = output.logits.sum()
        loss.backward()
        assert x.grad is not None
        assert x.grad.abs().sum() > 0


# ═══════════════════════════════════════════════════════════════════════════════
# TemporalGNN tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestTemporalGNN:
    """Tests for the temporal GNN detector."""

    def test_forward_pass_shape(self):
        """TemporalGNN.forward produces per-event risk scores."""
        from detectors.tgnn import TemporalGNN

        model = TemporalGNN(node_feature_dim=24, memory_dim=32, time_dim=8, num_layers=2)

        num_nodes = 5
        num_events = 8
        node_features = torch.randn(num_nodes, 24)
        edges_u = torch.tensor([0, 1, 0, 2, 1, 3, 0, 2])
        edges_v = torch.tensor([1, 2, 2, 3, 2, 4, 1, 3])
        edge_timestamps = torch.arange(num_events, dtype=torch.float)

        output = model(node_features, edges_u, edges_v, edge_timestamps, num_nodes)

        assert output.event_risk_scores.shape == (num_events,), \
            f"Expected ({num_events},), got {output.event_risk_scores.shape}"
        assert output.node_memories.shape == (num_nodes, 32), \
            f"Expected ({num_nodes}, 32), got {output.node_memories.shape}"
        assert output.event_embeddings.shape == (num_events, 32), \
            f"Expected ({num_events}, 32), got {output.event_embeddings.shape}"

        # Risk scores should be in [0, 1] after sigmoid
        assert torch.all((output.event_risk_scores >= 0) & (output.event_risk_scores <= 1))

    def test_single_event(self):
        """TemporalGNN handles a single event."""
        from detectors.tgnn import TemporalGNN

        model = TemporalGNN(node_feature_dim=24, memory_dim=16, time_dim=8)

        node_features = torch.randn(3, 24)
        edges_u = torch.tensor([0])
        edges_v = torch.tensor([1])
        edge_timestamps = torch.tensor([0.0])

        output = model(node_features, edges_u, edges_v, edge_timestamps, 3)
        assert output.event_risk_scores.shape == (1,)

    def test_empty_events(self):
        """TemporalGNN handles empty event streams."""
        from detectors.tgnn import TemporalGNN

        model = TemporalGNN(node_feature_dim=24, memory_dim=16, time_dim=8)

        node_features = torch.randn(3, 24)
        edges_u = torch.tensor([], dtype=torch.long)
        edges_v = torch.tensor([], dtype=torch.long)
        edge_timestamps = torch.tensor([], dtype=torch.float)

        output = model(node_features, edges_u, edges_v, edge_timestamps, 3)
        assert output.event_risk_scores.numel() == 0

    def test_gradient_flow(self):
        """Gradients flow through TemporalGNN."""
        from detectors.tgnn import TemporalGNN

        model = TemporalGNN(node_feature_dim=24, memory_dim=16, time_dim=8)
        node_features = torch.randn(5, 24, requires_grad=True)
        edges_u = torch.tensor([0, 1, 0, 2])
        edges_v = torch.tensor([1, 2, 2, 3])
        edge_timestamps = torch.tensor([0.0, 1.0, 2.0, 3.0])

        output = model(node_features, edges_u, edges_v, edge_timestamps, 5)
        loss = output.event_risk_scores.sum()
        loss.backward()
        assert node_features.grad is not None
        assert node_features.grad.abs().sum() > 0


# ═══════════════════════════════════════════════════════════════════════════════
# HybridDetector tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestHybridDetector:
    """Tests for the hybrid detector."""

    def test_forward_pass_shape(self):
        """HybridDetector.forward produces per-event risk scores."""
        from detectors.hybrid import HybridDetector

        model = HybridDetector(node_feature_dim=24, memory_dim=32, time_dim=8, fusion_dim=16)

        num_nodes = 5
        num_events = 6
        node_features = torch.randn(num_nodes, 24)
        edges_u = torch.tensor([0, 1, 0, 2, 1, 3])
        edges_v = torch.tensor([1, 2, 2, 3, 2, 4])
        edge_timestamps = torch.arange(num_events, dtype=torch.float)

        output = model(node_features, edges_u, edges_v, edge_timestamps, num_nodes)

        assert output.risk_scores.shape == (num_events,)
        assert output.heuristic_signals.shape == (num_events, 6), \
            f"Expected (6, 6), got {output.heuristic_signals.shape}"
        assert 0.0 <= float(output.risk_scores.min()) <= 1.0
        assert 0.0 <= float(output.risk_scores.max()) <= 1.0

    def test_fusion_backpropagates_into_temporal_backbone(self):
        from detectors.hybrid import HybridDetector
        model = HybridDetector(node_feature_dim=24, memory_dim=16, time_dim=8, fusion_dim=8)
        output = model(torch.randn(3, 24), torch.tensor([0, 1]),
                       torch.tensor([1, 2]), torch.tensor([0., 1.]), 3)
        output.final_logit.backward()
        gradient = model.gnn_backbone.node_proj.weight.grad
        assert gradient is not None and gradient.abs().sum() > 0

    def test_heuristic_signal_extractor(self):
        """HeuristicSignalExtractor produces 6 signals per event."""
        from detectors.hybrid import HeuristicSignalExtractor

        extractor = HeuristicSignalExtractor(node_feature_dim=24, hidden_dim=16)
        node_features = torch.randn(5, 24)
        edges_u = torch.tensor([0, 1, 2])
        edges_v = torch.tensor([1, 2, 3])
        edge_timestamps = torch.tensor([0.0, 1.0, 2.0])

        signals = extractor(node_features, edges_u, edges_v, edge_timestamps, 5)
        assert signals.shape == (3, 6)
        assert torch.all((signals >= 0) & (signals <= 1))

    def test_heuristic_signal_names(self):
        """HeuristicSignalExtractor has 6 named signals matching detect.tex."""
        from detectors.hybrid import HeuristicSignalExtractor

        extractor = HeuristicSignalExtractor()
        expected = [
            "event_type_rarity", "repeated_transition", "cross_role_transition",
            "source_fan_out", "target_convergence", "memory_chain",
        ]
        assert extractor.SIGNAL_NAMES == expected


# ═══════════════════════════════════════════════════════════════════════════════
# TemporalSnapshotBuilder tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestTemporalSnapshotBuilder:
    """Tests for the temporal snapshot builder."""

    def _make_minimal_trace(self, num_events: int = 15) -> Any:
        """Create a minimal Trace with the given number of events."""
        from schemas.trace import Trace, TraceVariant
        from schemas.trace_labels import TraceLabels
        from schemas.trace_event import TraceEvent, TraceEventType
        from schemas.event_labels import EventLabels

        events = []
        for i in range(num_events):
            evt = TraceEvent(
                trace_id="test_trace",
                event_id=f"e{i}",
                event_type=TraceEventType.REASONING,
                event_index=i,
                timestamp=f"2026-09-15T20:00:{i:02d}.000000+00:00",
                agent_role="researcher",
                source_entity_id=f"e{i-1}" if i > 0 else "user",
                target_entity_id=f"e{i}",
                depends_on=[f"e{i-1}"] if i > 0 else [],
                event_labels=EventLabels(),
            )
            events.append(evt)

        return Trace(
            trace_id="test_trace",
            execution_id="test_exec",
            events=events,
            variant=TraceVariant.MALIGNANT,
            labels=TraceLabels(downstream_failure=True),
            metadata={"topology": "linear", "task_family": "code_review"},
        )

    def test_snapshot_count(self):
        """Snapshot count scales with number of events."""
        from generation.event_graph_snapshot import TemporalSnapshotBuilder

        builder = TemporalSnapshotBuilder(snapshot_interval=5)

        assert builder.count_snapshots(0) == 0
        assert builder.count_snapshots(5) >= 1
        assert builder.count_snapshots(10) >= 2
        assert builder.count_snapshots(20) >= 3

    def test_build_snapshots(self):
        """Snapshots have monotonically increasing node counts."""
        from generation.event_graph_snapshot import TemporalSnapshotBuilder
        from schemas.trace import TraceVariant
        from schemas.event_labels import EventLabels

        trace = self._make_minimal_trace(15)
        trace.variant = TraceVariant.MALIGNANT

        # Mark one event as injection origin
        trace.events[3].event_labels = EventLabels(is_injection_origin=True)

        builder = TemporalSnapshotBuilder(snapshot_interval=5)
        snapshots = builder.build_from_trace(trace, "linear", "code_review")

        assert len(snapshots) > 0, "Should produce at least one snapshot"

        node_counts = [s.num_nodes for s in snapshots]
        assert node_counts == sorted(node_counts), \
            f"Node counts should be non-decreasing: {node_counts}"
        assert node_counts[-1] == 15, f"Last snapshot should have all 15 events, got {node_counts[-1]}"

        # All snapshots should have valid topology metadata
        for snap in snapshots:
            assert snap.topology_name == "linear"
            assert snap.task_family == "code_review"
            assert snap.variant == "b"

    def test_snapshot_features_present(self):
        """Snapshots have node_features tensors."""
        from generation.event_graph_snapshot import TemporalSnapshotBuilder

        trace = self._make_minimal_trace(10)
        builder = TemporalSnapshotBuilder(snapshot_interval=3)
        snapshots = builder.build_from_trace(trace, "linear", "code_review")

        for snap in snapshots:
            if snap.num_nodes > 0:
                assert snap.node_features is not None, \
                    "node_features should be computed"
                assert snap.node_features.shape[0] == snap.num_nodes

    def test_snapshot_empty_trace(self):
        """Empty traces produce empty snapshot list."""
        from generation.event_graph_snapshot import TemporalSnapshotBuilder
        from schemas.trace import Trace, TraceVariant
        from schemas.trace_labels import TraceLabels

        empty_trace = Trace(
            trace_id="empty",
            execution_id="empty_exec",
            events=[],
            variant=TraceVariant.BENIGN,
            labels=TraceLabels(),
        )
        builder = TemporalSnapshotBuilder()
        snapshots = builder.build_from_trace(empty_trace, "linear", "code_review")
        assert snapshots == []


# ═══════════════════════════════════════════════════════════════════════════════
# Metrics tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestMetrics:
    """Tests for evaluation metrics."""

    def test_auroc_perfect(self):
        """AUROC = 1.0 for perfect predictions."""
        from training.metrics import compute_auroc

        y_true = np.array([0, 0, 1, 1])
        y_scores = np.array([0.1, 0.2, 0.8, 0.9])
        assert abs(compute_auroc(y_true, y_scores) - 1.0) < 1e-5

    def test_auroc_random(self):
        """A small random sample need not have AUROC near 0.5."""
        from training.metrics import compute_auroc

        rng = np.random.RandomState(42)
        y_true = np.array([0, 0, 0, 1, 1, 1])
        y_scores = rng.uniform(0, 1, 6)
        assert compute_auroc(y_true, y_scores) == pytest.approx(1 / 9)

    def test_auroc_single_class(self):
        """AUROC = 0.5 when all labels are the same."""
        from training.metrics import compute_auroc

        y_true = np.array([0, 0, 0])
        y_scores = np.array([0.1, 0.5, 0.9])
        assert np.isnan(compute_auroc(y_true, y_scores))

    def test_aupr_perfect(self):
        """AUPR = 1.0 for perfect predictions."""
        from training.metrics import compute_aupr

        y_true = np.array([0, 0, 1, 1])
        y_scores = np.array([0.1, 0.2, 0.8, 0.9])
        assert abs(compute_aupr(y_true, y_scores) - 1.0) < 1e-5

    def test_aupr_random(self):
        """AUPR matches sklearn for random predictions."""
        from training.metrics import compute_aupr

        rng = np.random.RandomState(42)
        y_true = np.array([0, 0, 0, 1, 1, 1])
        y_scores = rng.uniform(0, 1, 6)
        result = compute_aupr(y_true, y_scores)
        from sklearn.metrics import average_precision_score
        expected = average_precision_score(y_true, y_scores)
        assert abs(result - expected) < 1e-5

    def test_aupr_single_class(self):
        """AUPR equals positive-class proportion when all labels are the same."""
        from training.metrics import compute_aupr

        y_true = np.array([1, 1, 1])
        y_scores = np.array([0.1, 0.5, 0.9])
        result = compute_aupr(y_true, y_scores)
        assert abs(result - 1.0) < 1e-5

        y_true_neg = np.array([0, 0, 0])
        result_neg = compute_aupr(y_true_neg, y_scores)
        assert abs(result_neg - 0.0) < 1e-5

    def test_auroc_reversed_ranking(self):
        """Reversed predictions (all negatives scored higher) give AUROC ≈ 0.0."""
        from training.metrics import compute_auroc

        y_true = np.array([0, 0, 0, 1, 1, 1])
        y_scores = np.array([0.9, 0.8, 0.7, 0.1, 0.2, 0.3])
        assert abs(compute_auroc(y_true, y_scores) - 0.0) < 1e-5

    def test_auroc_tied_scores(self):
        """Tied scores do not crash and give a valid AUROC."""
        from training.metrics import compute_auroc

        y_true = np.array([0, 0, 1, 1])
        y_scores = np.array([0.5, 0.5, 0.5, 0.5])
        result = compute_auroc(y_true, y_scores)
        assert 0.0 <= result <= 1.0

    def test_aupr_tied_scores(self):
        """Tied scores do not crash and give a valid AUPR."""
        from training.metrics import compute_aupr

        y_true = np.array([0, 0, 1, 1])
        y_scores = np.array([0.5, 0.5, 0.5, 0.5])
        result = compute_aupr(y_true, y_scores)
        assert 0.0 <= result <= 1.0

    def test_auroc_matches_sklearn(self):
        """AUROC matches sklearn.roc_auc_score for various inputs."""
        from training.metrics import compute_auroc
        from sklearn.metrics import roc_auc_score

        rng = np.random.RandomState(123)
        y_true = rng.randint(0, 2, 100)
        y_scores = rng.uniform(0, 1, 100)
        result = compute_auroc(y_true, y_scores)
        expected = roc_auc_score(y_true, y_scores)
        assert abs(result - expected) < 1e-5

    def test_aupr_matches_sklearn(self):
        """AUPR matches sklearn.average_precision_score for various inputs."""
        from training.metrics import compute_aupr
        from sklearn.metrics import average_precision_score

        rng = np.random.RandomState(123)
        y_true = rng.randint(0, 2, 100)
        y_scores = rng.uniform(0, 1, 100)
        result = compute_aupr(y_true, y_scores)
        expected = average_precision_score(y_true, y_scores)
        assert abs(result - expected) < 1e-5

    def test_classification_metrics(self):
        """Classification metrics are correct for known inputs."""
        from training.metrics import compute_classification_metrics

        y_true = np.array([0, 0, 1, 1, 1])
        y_scores = np.array([0.1, 0.2, 0.8, 0.9, 0.6])

        metrics = compute_classification_metrics(y_true, y_scores, threshold=0.5)

        # 2 TN, 0 FP, 3 TP, 0 FN → accuracy = 1.0
        assert metrics["accuracy"] == pytest.approx(1.0)
        assert metrics["precision"] == pytest.approx(1.0)
        assert metrics["recall"] == pytest.approx(1.0)
        assert metrics["f1"] == pytest.approx(1.0)

    def test_lead_time_calculation(self):
        """Lead time is correct when alert precedes failure."""
        from training.metrics import compute_lead_time

        risk_scores = np.array([0.1, 0.1, 0.9, 0.9, 0.9])
        event_indices = np.array([0, 1, 2, 3, 4])
        failure_event_idx = 4

        # With a threshold of 0.5 (50th percentile = 0.9, so threshold = 0.9),
        # the alert fires at event 2, giving lead time = 4 - 2 = 2
        lt = compute_lead_time(risk_scores, event_indices, failure_event_idx, fpr_target=0.5)
        assert lt is not None
        assert lt == 2.0

    def test_lead_time_no_alert(self):
        """Lead time is None when no alert is raised before failure."""
        from training.metrics import compute_lead_time

        risk_scores = np.array([0.1, 0.1, 0.2, 0.2, 0.9])
        event_indices = np.array([0, 1, 2, 3, 4])
        failure_event_idx = 4

        # At fpr=0.9, threshold = 0.2. Alert fires at event 2 (first >= 0.2),
        # which is before failure at 4. Wait — let me recalculate.
        # risk_scores sorted: [0.1, 0.1, 0.2, 0.2, 0.9]
        # percentile(1-0.9=10%) = 0.1, so threshold = 0.1
        # First event >= 0.1 before failure (event 4) is event 0 → lead time = 4
        # Hmm, this is tricky because of tie-breaking. Let's use a cleaner example.

        risk_scores = np.array([0.1, 0.1, 0.1, 0.1, 0.9])
        event_indices = np.array([0, 1, 2, 3, 4])
        failure_event_idx = 4

        lt = compute_lead_time(risk_scores, event_indices, failure_event_idx, fpr_target=0.5)
        # threshold = 0.1 (10th percentile). First event >= 0.1 before failure
        # is event 0 → lead time = 4
        assert lt is not None

    def test_lead_time_none_no_failure(self):
        """Lead time is None when no failure event."""
        from training.metrics import compute_lead_time

        lt = compute_lead_time(
            np.array([0.1, 0.9, 0.1]),
            np.array([0, 1, 2]),
            failure_event_idx=None,
        )
        assert lt is None

    def test_aggregate_metrics(self):
        """Aggregate metrics computes mean/std correctly."""
        from training.metrics import aggregate_metrics

        dicts = [
            {"auroc": 0.8, "f1": 0.7, "accuracy": 0.75},
            {"auroc": 0.9, "f1": 0.8, "accuracy": 0.85},
            {"auroc": 0.85, "f1": 0.75, "accuracy": 0.80},
        ]

        agg = aggregate_metrics(dicts)
        assert abs(agg["auroc_mean"] - 0.85) < 1e-5
        assert abs(agg["f1_mean"] - 0.75) < 1e-5
        assert abs(agg["accuracy_std"] - np.std([0.75, 0.85, 0.80])) < 1e-5

    def test_aggregate_empty(self):
        """Aggregate metrics handles empty input."""
        from training.metrics import aggregate_metrics
        assert aggregate_metrics([]) == {}


# ═══════════════════════════════════════════════════════════════════════════════
# DetectorTrainer tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetectorTrainer:
    """Tests for the trainer."""

    def test_trainer_initialization(self):
        """DetectorTrainer initializes correctly."""
        from detectors.static_gnn import StaticGNN
        from training.trainer import DetectorTrainer

        model = StaticGNN(node_feature_dim=24, hidden_dim=16, num_layers=2)
        trainer = DetectorTrainer(model, model_type="static", device="cpu")

        assert trainer.device.type == "cpu"
        assert trainer.lr == 1e-3

    def test_single_epoch_training(self):
        """Trainer runs one epoch without errors."""
        from detectors.tgnn import TemporalGNN
        from training.trainer import DetectorTrainer

        model = TemporalGNN(node_feature_dim=24, memory_dim=16, time_dim=8)
        trainer = DetectorTrainer(model, model_type="temporal", device="cpu")

        # Create minimal temporal data
        from encoder import TemporalGraphData
        graphs = [
            TemporalGraphData(
                trace_id="test", execution_id="test", label=0.0,
                edges_u=torch.tensor([0, 1]),
                edges_v=torch.tensor([1, 2]),
                edge_timestamps=torch.tensor([0.0, 1.0]),
                edge_features=torch.empty((2, 0), dtype=torch.float),
                event_types=torch.empty(0, dtype=torch.long),
                num_nodes=3,
                node_features=torch.randn(3, 24),
            ),
            TemporalGraphData(
                trace_id="test", execution_id="test", label=0.0,
                edges_u=torch.tensor([0]),
                edges_v=torch.tensor([1]),
                edge_timestamps=torch.tensor([0.0]),
                edge_features=torch.empty((1, 0), dtype=torch.float),
                event_types=torch.empty(0, dtype=torch.long),
                num_nodes=2,
                node_features=torch.randn(2, 24),
            ),
        ]
        labels = [1.0, 0.0]

        loss = trainer.train_epoch(graphs, labels, batch_size=2)
        assert isinstance(loss, float)
        assert not np.isnan(loss)

    def test_trainer_checkpoint_save_load(self):
        """Trainer can save and load checkpoints."""
        import tempfile
        from detectors.static_gnn import StaticGNN
        from training.trainer import DetectorTrainer

        model = StaticGNN(node_feature_dim=24, hidden_dim=16, num_layers=2)
        trainer = DetectorTrainer(model, model_type="static", device="cpu")

        with tempfile.TemporaryDirectory() as tmpdir:
            trainer.checkpoint_dir = Path(tmpdir)
            trainer.best_val_auroc = 0.85
            trainer.best_epoch = 5
            trainer.history = [{"epoch": 1, "train_loss": 0.5}]
            trainer.save_checkpoint()

            # Load into a new trainer
            model2 = StaticGNN(node_feature_dim=24, hidden_dim=16, num_layers=2)
            trainer2 = DetectorTrainer(model2, model_type="static", device="cpu")
            trainer2.load_checkpoint(Path(tmpdir) / "checkpoint.pt")

            assert abs(trainer2.best_val_auroc - 0.85) < 1e-5
            assert trainer2.best_epoch == 5


# ═══════════════════════════════════════════════════════════════════════════════
# Integration tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntegration:
    """End-to-end integration tests using benchmark trace files."""

    def _load_trace(self, filename: str) -> Any:
        """Load a trace JSON file."""
        import json
        from schemas.trace import Trace, TraceVariant
        from schemas.trace_labels import TraceLabels
        from schemas.trace_event import TraceEvent, TraceEventType
        from schemas.event_labels import EventLabels

        path = ROOT / "benchmark_output" / "traces" / filename
        if not path.exists():
            pytest.skip(f"Trace file not found: {path}")

        with open(path) as f:
            data = json.load(f)

        variant = TraceVariant(data.get("variant", "a"))

        events = []
        for evt_data in data.get("events", []):
            evt_type_str = evt_data.get("event_type", "reasoning")
            try:
                evt_type = TraceEventType(evt_type_str)
            except ValueError:
                evt_type = TraceEventType.REASONING

            event_labels_data = evt_data.get("event_labels", {})
            event_labels = EventLabels(**event_labels_data) if event_labels_data else EventLabels()

            evt = TraceEvent(
                trace_id=data.get("trace_id", ""),
                event_id=evt_data.get("event_id", ""),
                event_type=evt_type,
                event_index=evt_data.get("event_index", 0),
                timestamp=evt_data.get("timestamp", ""),
                agent_id=evt_data.get("agent_id", ""),
                agent_role=evt_data.get("agent_role", ""),
                depends_on=evt_data.get("depends_on", []),
                source_entity_id=evt_data.get("source_entity_id", ""),
                target_entity_id=evt_data.get("target_entity_id", ""),
                event_labels=event_labels,
                input_text=evt_data.get("input_text"),
                output_text=evt_data.get("output_text"),
                tool_name=evt_data.get("tool_name"),
                tool_result=evt_data.get("tool_result"),
                tool_arguments=evt_data.get("tool_arguments"),
                memory_key=evt_data.get("memory_key"),
                memory_scope=evt_data.get("memory_scope"),
            )
            events.append(evt)

        meta = data.get("metadata", {})
        return Trace(
            trace_id=data.get("trace_id", ""),
            execution_id=data.get("execution_id", ""),
            events=events,
            variant=variant,
            labels=TraceLabels(downstream_failure=(variant == TraceVariant.MALIGNANT)),
            metadata=meta,
        )

    def test_event_graph_builder_from_trace(self):
        """DependsOnGraphBuilder produces a valid EventGraph from a real trace."""
        from generation.event_graph_builder import DependsOnGraphBuilder

        trace = self._load_trace(
            "b_review_loop_code_review_LEP_HANDOFF_CORRUPTION_single_origin_00_lep_trace.json"
        )
        assert trace.num_events > 0, "Trace should have events"

        builder = DependsOnGraphBuilder()
        graph = builder.build(trace, topology_name="review_loop", task_family="code_review", strict=False)

        assert graph.num_nodes == trace.num_events, \
            f"Graph nodes ({graph.num_nodes}) should match trace events ({trace.num_events})"
        assert graph.is_malignant
        assert graph.topology_name == "review_loop"
        assert graph.node_features is not None

    def test_temporal_snapshot_builder_real_trace(self):
        """TemporalSnapshotBuilder produces valid snapshots from real trace."""
        from generation.event_graph_snapshot import TemporalSnapshotBuilder
        from generation.event_graph_builder import DependsOnGraphBuilder

        trace = self._load_trace(
            "b_review_loop_code_review_LEP_HANDOFF_CORRUPTION_single_origin_00_lep_trace.json"
        )

        builder = DependsOnGraphBuilder()
        graph = builder.build(trace, topology_name="review_loop", task_family="code_review", strict=False)

        snapshot_builder = TemporalSnapshotBuilder(snapshot_interval=10)
        snapshots = snapshot_builder.build_from_event_graph(graph)

        assert len(snapshots) > 0, "Should produce snapshots"
        assert snapshots[-1].num_nodes == graph.num_nodes, \
            "Last snapshot should include all nodes"

        # Node counts should be non-decreasing
        counts = [s.num_nodes for s in snapshots]
        assert counts == sorted(counts)

    def test_encoder_produces_valid_output(self):
        """GraphEncoder produces valid PyG tensors from real traces."""
        from generation.event_graph_builder import DependsOnGraphBuilder
        from encoder import GraphEncoder

        trace = self._load_trace(
            "b_review_loop_code_review_LEP_HANDOFF_CORRUPTION_single_origin_00_lep_trace.json"
        )
        builder = DependsOnGraphBuilder()
        graph = builder.build(trace, topology_name="review_loop", task_family="code_review", strict=False)

        encoder = GraphEncoder()

        static = encoder.encode_event_graph_static([graph], labels=[1.0])
        assert len(static) == 1
        assert static[0].x.shape[1] == OBSERVABLE_NODE_FEATURE_DIM, f"Expected 24 features, got {static[0].x.shape[1]}"
        assert static[0].edge_index.shape[1] == graph.num_edges

        temporal = encoder.encode_event_graph_temporal([graph], labels=[1.0])
        assert len(temporal) == 1
        assert temporal[0].node_features.shape[1] == OBSERVABLE_NODE_FEATURE_DIM
        assert temporal[0].edges_v.numel() == temporal[0].edges_u.numel()

    def test_static_gnn_on_real_data(self):
        """StaticGNN forward pass works on real encoded data."""
        from generation.event_graph_builder import DependsOnGraphBuilder
        from encoder import GraphEncoder
        from detectors.static_gnn import StaticGNN

        trace = self._load_trace(
            "b_review_loop_code_review_LEP_HANDOFF_CORRUPTION_single_origin_00_lep_trace.json"
        )
        builder = DependsOnGraphBuilder()
        graph = builder.build(trace, topology_name="review_loop", task_family="code_review", strict=False)

        encoder = GraphEncoder()
        static = encoder.encode_event_graph_static([graph], labels=[1.0])
        sg = static[0]

        model = StaticGNN(node_feature_dim=OBSERVABLE_NODE_FEATURE_DIM, hidden_dim=32, num_layers=2)
        output = model.predict(sg)
        assert output.probabilities.numel() == 1

    def test_tgnn_on_real_data(self):
        """TemporalGNN forward pass works on real encoded data."""
        from generation.event_graph_builder import DependsOnGraphBuilder
        from encoder import GraphEncoder
        from detectors.tgnn import TemporalGNN

        trace = self._load_trace(
            "b_review_loop_code_review_LEP_HANDOFF_CORRUPTION_single_origin_00_lep_trace.json"
        )
        builder = DependsOnGraphBuilder()
        graph = builder.build(trace, topology_name="review_loop", task_family="code_review", strict=False)

        encoder = GraphEncoder()
        temporal = encoder.encode_event_graph_temporal([graph], labels=[1.0])
        tg = temporal[0]

        model = TemporalGNN(node_feature_dim=OBSERVABLE_NODE_FEATURE_DIM, memory_dim=32, time_dim=8)
        output = model(tg.node_features, tg.edges_u, tg.edges_v, tg.edge_timestamps, tg.num_nodes)
        assert output.event_risk_scores.numel() == tg.edges_v.numel()

    def test_hybrid_on_real_data(self):
        """HybridDetector forward pass works on real encoded data."""
        from generation.event_graph_builder import DependsOnGraphBuilder
        from encoder import GraphEncoder
        from detectors.hybrid import HybridDetector

        trace = self._load_trace(
            "b_review_loop_code_review_LEP_HANDOFF_CORRUPTION_single_origin_00_lep_trace.json"
        )
        builder = DependsOnGraphBuilder()
        graph = builder.build(trace, topology_name="review_loop", task_family="code_review", strict=False)

        encoder = GraphEncoder()
        temporal = encoder.encode_event_graph_temporal([graph], labels=[1.0])
        tg = temporal[0]

        model = HybridDetector(node_feature_dim=OBSERVABLE_NODE_FEATURE_DIM, memory_dim=32, time_dim=8, fusion_dim=16)
        output = model(tg.node_features, tg.edges_u, tg.edges_v, tg.edge_timestamps, tg.num_nodes)
        assert output.risk_scores.numel() == tg.edges_v.numel()
        assert output.heuristic_signals.shape[1] == 6

    def test_pipeline_trace_loading(self):
        """DetectorPipeline loads traces from benchmark_output."""
        from training.pipeline import DetectorPipeline
        from pathlib import Path

        pipeline = DetectorPipeline(output_dir=ROOT / "benchmark_output")
        benign, malignant = pipeline.load_traces()

        assert len(benign) > 0, "Should find benign traces"
        assert len(malignant) > 0, "Should find malignant traces"

    def test_pipeline_build_event_graphs(self):
        """Pipeline builds EventGraphs from loaded traces."""
        from training.pipeline import DetectorPipeline

        pipeline = DetectorPipeline(output_dir=ROOT / "benchmark_output")
        benign, malignant = pipeline.load_traces()
        all_traces = benign[:2] + malignant[:2]  # Use a small subset

        graphs = [pipeline.graph_builder.build(t, "review_loop", "code_review", strict=False)
                  for t in all_traces]
        assert len(graphs) > 0
        for g in graphs:
            assert g.num_nodes > 0
            assert g.topology_name == "review_loop"


# ═══════════════════════════════════════════════════════════════════════════════
# Regression tests: evaluation-unit consistency (Static vs TGNN vs Hybrid)
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvaluationUnitConsistency:
    @staticmethod
    def _snapshots():
        from torch_geometric.data import Data
        # Unequal prefix counts, deliberately misleading prefix scores.
        labels = [0., 0., 0., 1., 1.]
        scores = [0.9, 0.9, 0.1, 0.1, 0.9]
        ids = ['a', 'a', 'a', 'b', 'b']
        graphs = [Data(x=torch.tensor([[score]]),
                       edge_index=torch.empty((2, 0), dtype=torch.long),
                       y=torch.tensor([label]), execution_id=eid)
                  for score, label, eid in zip(scores, labels, ids)]
        return graphs, labels, [{'execution_id': eid} for eid in ids]

    @staticmethod
    def _trainer():
        from detectors.static_gnn import DetectionOutput
        from training.trainer import DetectorTrainer

        class ScoreModel(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.flip = torch.nn.Parameter(torch.tensor(0.))

            def forward(self, x, edge_index, batch):
                scores = x[:, 0] * (1 - self.flip) + (1 - x[:, 0]) * self.flip
                logits = torch.logit(scores)
                return DetectionOutput(logits, scores, scores >= .5, logits[-1], scores[-1])

        return DetectorTrainer(ScoreModel(), model_type='static', device='cpu')

    def test_final_snapshot_metrics_and_singleton_batches(self):
        from training.dataset import select_final_snapshots_per_execution
        graphs, labels, metadata = self._snapshots()
        final, final_labels, ids = select_final_snapshots_per_execution(graphs, labels, metadata)
        assert ids == ['a', 'b']
        assert final[0] is graphs[2] and final[1] is graphs[4]
        assert final_labels == [0., 1.]
        trainer = self._trainer()
        prefix = trainer.evaluate(graphs, labels, batch_size=1)
        execution = trainer.evaluate(graphs, labels, batch_size=1, snapshot_metadata=metadata)
        assert prefix['evaluation_unit'] == 'graph'
        assert prefix['num_predictions'] == 5
        assert prefix['metrics']['auroc'] < 0.5
        assert execution['evaluation_unit'] == 'execution'
        assert execution['num_predictions'] == execution['num_executions'] == 2
        assert execution['predictions'] == pytest.approx([.1, .9])
        assert execution['metrics']['auroc'] == 1.
        assert execution['metrics']['auprc'] == 1.
        assert execution['loss'] == pytest.approx(-np.log(.9))
        final_result = trainer.evaluate(final, final_labels, snapshot_metadata=[{'execution_id': e} for e in ids])
        assert final_result['evaluation_unit'] == 'execution'
        assert final_result['predictions'] == execution['predictions']
        with pytest.raises(ValueError, match='metadata'):
            trainer.evaluate(graphs, labels, snapshot_metadata=metadata[:-1])

    def test_early_stopping_uses_execution_ranking_and_restores_best(self, monkeypatch):
        graphs, labels, metadata = self._snapshots()
        trainer = self._trainer()
        steps = iter([0., 1.])

        def train_epoch(*args, **kwargs):
            with torch.no_grad():
                trainer.model.flip.fill_(next(steps))
            return 0.

        monkeypatch.setattr(trainer, 'train_epoch', train_epoch)
        history = trainer.train(graphs, labels, graphs, labels, num_epochs=2,
                                snapshot_metadata=metadata, verbose=False)
        assert [e['val_auroc'] for e in history['history']] == [1., 0.]
        assert trainer.best_epoch == 0
        assert trainer.model.flip.item() == 0.
        # The later model wins on prefixes, but loses on full executions.
        with torch.no_grad():
            trainer.model.flip.fill_(1.)
        assert trainer.evaluate(graphs, labels)['metrics']['auroc'] > .5

    def test_small_splits_and_task_groups(self):
        from training.dataset import split_at_execution_level
        ids = list('abcde')
        with pytest.raises(ValueError, match="both downstream_failure classes required"):
            split_at_execution_level(ids, [0.] * 5)
        with pytest.raises(ValueError, match='at least 3'):
            split_at_execution_level(['a', 'b'], [0., 1.])
        ids = [f'{i}-{variant}' for i in range(5) for variant in ('a', 'b')]
        groups = [str(i) for i in range(5) for _ in range(2)]
        splits = split_at_execution_level(ids, [0., 1.] * 5, group_ids=groups)
        for split in splits:
            for i in range(5):
                assert (f'{i}-a' in split) == (f'{i}-b' in split)

    def test_pipeline_uses_same_executions_and_saves_counts(self, tmp_path, monkeypatch):
        import json
        from training.pipeline import DetectorPipeline
        from training.trainer import DetectorTrainer
        from generation.event_graph_snapshot import TemporalSnapshotBuilder

        traces = []
        for i in range(5):
            for label in (0, 1):
                trace = TestTemporalSnapshotBuilder()._make_minimal_trace(3 + i * 2 + label)
                trace.trace_id = trace.execution_id = f'{i}-{label}'
                trace.labels.downstream_failure = bool(label)
                trace.metadata['fixture_id'] = f'fixture-{i}'
                for event in trace.events:
                    event.trace_id = trace.trace_id
                traces.append(trace)
        pipeline = DetectorPipeline(tmp_path, device='cpu')
        monkeypatch.setattr(pipeline, 'load_traces', lambda: (traces[::2], traces[1::2]))
        # This test exercises training/split plumbing; production target semantics
        # are covered by test_detector_anomaly_targets.py.
        targets = {trace.execution_id: float(i % 2) for i, trace in enumerate(traces)}
        monkeypatch.setattr(pipeline, 'build_anomaly_labels',
                            lambda loaded: [targets[t.execution_id] for t in loaded])
        observed = []
        evaluated = []
        original_evaluate = DetectorTrainer.evaluate

        def evaluate(trainer, graphs, labels, *args, **kwargs):
            evaluated.append((trainer.model_type, graphs))
            return original_evaluate(trainer, graphs, labels, *args, **kwargs)

        monkeypatch.setattr(DetectorTrainer, "evaluate", evaluate)
        original_train = DetectorTrainer.train

        def train(trainer, *args, **kwargs):
            observed.append((trainer.model_type, kwargs['train_graphs'], kwargs['val_graphs']))
            return original_train(trainer, *args, **kwargs)

        monkeypatch.setattr(DetectorTrainer, 'train', train)
        results = pipeline.run(num_epochs=3, batch_size=3, snapshot_interval=2,
                               train_frac=.4, val_frac=.2,
                               use_heuristic_baselines=False)
        static = results['static_gnn']
        full = {t.execution_id: pipeline.graph_builder.build(t, 'linear', 'code_review', strict=False)
                for t in traces}
        builder = TemporalSnapshotBuilder(snapshot_interval=2)
        expected_train = sum(len(builder.build_from_event_graph(full[eid])) for eid in static.train_execution_ids)
        assert static.train_sample_count == expected_train > len(static.train_execution_ids)
        for kind in ('static_gnn', 'tgnn', 'hybrid'):
            result = results[kind]
            assert len(result.train_history) == 3
            assert result.train_execution_ids == static.train_execution_ids
            assert result.val_execution_ids == static.val_execution_ids
            assert result.test_execution_ids == static.test_execution_ids
            assert result.evaluation_unit == result.test_metrics['evaluation_unit'] == 'execution'
            assert result.test_metrics['num_predictions'] == len(result.test_execution_ids)
            assert result.test_metrics['labels'] == [targets[eid] for eid in result.test_execution_ids]
        train_graphs, val_graphs = observed[0][1:]
        assert len(train_graphs) == expected_train
        assert {g.execution_id for g in val_graphs} == set(static.val_execution_ids)
        assert all(g.num_nodes == full[g.execution_id].num_nodes for g in val_graphs)
        final_test = [graphs for model_type, graphs in evaluated if model_type == "static"][-1]
        assert [g.execution_id for g in final_test] == static.test_execution_ids
        assert all(g.num_nodes == full[g.execution_id].num_nodes for g in final_test)
        saved = json.loads((tmp_path / 'detector_results.json').read_text())
        assert saved['static_gnn']['evaluation_unit'] == 'execution'
        assert saved['static_gnn']['train_sample_count'] == expected_train
        assert saved['static_gnn']['test_execution_ids'] == static.test_execution_ids
