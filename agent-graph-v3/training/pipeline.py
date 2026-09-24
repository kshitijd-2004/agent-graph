"""End-to-end detector training and evaluation pipeline.

Wires together trace loading, EventGraph construction, temporal snapshots,
encoding, training, and evaluation into a single orchestrator.

Usage::

    pipeline = DetectorPipeline(output_dir=Path("benchmark_output"))
    results = pipeline.run(
        detector_types=["static_gnn", "tgnn", "hybrid"],
        snapshot_interval=5,
        num_epochs=50,
    )
"""

from __future__ import annotations

import json
import logging
import pickle
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from generation.event_graph_builder import DependsOnGraphBuilder, EventGraph
from generation.event_graph_snapshot import TemporalSnapshotBuilder
from generation.feature_schema import OBSERVABLE_NODE_FEATURE_DIM
from generation.heuristic_detector import DetectionResult, HeuristicDetector
from encoder import GraphEncoder, StaticGraphData, TemporalGraphData
from detectors.static_gnn import StaticGNN, DetectionOutput as StaticOutput
from detectors.tgnn import TemporalGNN, TemporalDetectionOutput
from detectors.hybrid import HybridDetector, HybridDetectionOutput
from training.dataset import (DetectorDataset, FinalSnapshotDataset,
                              create_dataloaders, select_final_snapshots_per_execution)
from training.trainer import DetectorTrainer
from training.evaluator import DetectionEvaluator, BaselineComparator

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Results from a single detector training run.

    Attributes:
        detector_type:  Detector name (e.g. ``"static_gnn"``).
        best_val_auroc: Best validation AUROC achieved.
        best_epoch:     Epoch of best validation AUROC.
        test_metrics:   Test set metrics dict.
        train_history:  Per-epoch training history.
        evaluation_unit: ``"execution"`` or ``"graph"`` — what each
                         prediction in ``test_metrics`` corresponds to.
        train_sample_count: Number of training samples used.
        val_execution_count: Number of validation executions (if execution-level).
        test_execution_count: Number of test executions (if execution-level).
        train_execution_ids: Execution IDs in the train split.
        val_execution_ids:   Execution IDs in the val split.
        test_execution_ids:  Execution IDs in the test split.
    """
    detector_type: str
    best_val_auroc: float
    best_epoch: int
    test_metrics: Dict[str, Any]
    train_history: List[Dict[str, float]]
    evaluation_unit: str = "graph"
    train_sample_count: int = 0
    val_execution_count: int = 0
    test_execution_count: int = 0
    train_execution_ids: List[str] = field(default_factory=list)
    val_execution_ids: List[str] = field(default_factory=list)
    test_execution_ids: List[str] = field(default_factory=list)


class DetectorPipeline:
    """End-to-end detector training and evaluation pipeline.

    The pipeline:
    1. Load traces from benchmark_output/traces/
    2. Build EventGraphs from traces
    3. (For static GNN) Build temporal snapshots
    4. Encode graphs for the target detector type
    5. Train/val/test split (by group_id)
    6. Train the detector
    7. Evaluate on test set
    8. Compare all detector types

    Args:
        output_dir:     Root benchmark output directory (contains traces/).
        device:         Training device (``"auto"``, ``"cpu"``, ``"cuda"``).
        snapshot_interval: For static GNN: snapshot every N events.
        seed:           Random seed for reproducibility.
        checkpoint_dir: Directory for saving checkpoints.
    """

    def __init__(
        self,
        output_dir: Path,
        device: str = "auto",
        snapshot_interval: int = 5,
        seed: int = 42,
        checkpoint_dir: Optional[Path] = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.snapshot_interval = snapshot_interval
        self.seed = seed
        self.device = device
        self.graph_builder = DependsOnGraphBuilder()
        self.encoder = GraphEncoder()
        self.snapshot_builder = TemporalSnapshotBuilder(
            snapshot_interval=snapshot_interval
        )

        if checkpoint_dir:
            self.checkpoint_dir = Path(checkpoint_dir)
        else:
            self.checkpoint_dir = self.output_dir / "detector_checkpoints"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def load_traces(self) -> tuple[List[Any], List[Any]]:
        """Load traces from the benchmark output directory.

        Scans ``output_dir/traces/`` for trace JSON files, groups them
        into benign (variant=a) and malignant (variant=b).

        Returns:
            (benign_traces, malignant_traces) — lists of Trace objects.
        """
        import json
        from schemas.trace import Trace, TraceVariant
        from schemas.trace_labels import TraceLabels

        traces_dir = self.output_dir / "traces"
        if not traces_dir.exists():
            raise FileNotFoundError(f"Traces directory not found: {traces_dir}")

        benign_traces = []
        malignant_traces = []

        for trace_file in sorted(traces_dir.glob("*.json")):
            try:
                with open(trace_file) as f:
                    trace_data = json.load(f)

                # Determine variant from the stored data
                variant_str = trace_data.get("variant", "a")
                from schemas.trace import TraceVariant
                try:
                    variant = TraceVariant(variant_str)
                except ValueError:
                    variant = TraceVariant.BENIGN

                trace = self._dict_to_trace(trace_data, variant)
                if variant == TraceVariant.MALIGNANT:
                    malignant_traces.append(trace)
                else:
                    benign_traces.append(trace)

            except Exception as e:
                logger.warning("Failed to load trace %s: %s", trace_file, e)

        logger.info(
            "Loaded %d benign + %d malignant traces from %s",
            len(benign_traces), len(malignant_traces), traces_dir,
        )
        return benign_traces, malignant_traces

    @staticmethod
    def _task_instance_id(trace: Any) -> str:
        """Build a task-instance group identifier from trace metadata.

        Groups by fixture_id + topology + repetition so that all variants
        (benign, each LEP, each repetition) of the same underlying task
        instance stay together in the same split, preventing data leakage.
        """
        meta = getattr(trace, "metadata", {}) or {}
        fixture = meta.get("fixture_id", "unknown")
        topology = meta.get("topology", "unknown")
        repetition = meta.get("repetition_index", 0)
        return f"{fixture}|{topology}|{repetition}"

    def _dict_to_trace(self, data: dict, variant: Any = None) -> Any:
        """Convert a trace dict (from JSON) to a Trace object."""
        from schemas.trace import Trace, TraceVariant
        from schemas.trace_labels import TraceLabels
        from schemas.trace_event import TraceEvent, TraceEventType
        from datetime import datetime

        if variant is None:
            variant_str = data.get("variant", "a")
            try:
                variant = TraceVariant(variant_str)
            except ValueError:
                variant = TraceVariant.BENIGN

        labels = None
        if data.get("labels"):
            labels = TraceLabels(**data["labels"])

        events = []
        for evt_data in data.get("events", []):
            evt_type_str = evt_data.get("event_type", "reasoning")
            try:
                evt_type = TraceEventType(evt_type_str)
            except ValueError:
                evt_type = TraceEventType.REASONING

            event_labels = None
            if evt_data.get("event_labels"):
                from schemas.event_labels import EventLabels
                event_labels = EventLabels(**evt_data["event_labels"])

            evt = TraceEvent(
                trace_id=data.get("trace_id", ""),
                event_id=evt_data.get("event_id", ""),
                event_type=evt_type,
                event_index=evt_data.get("event_index", 0),
                timestamp=evt_data.get("timestamp", ""),
                stage_event_index=evt_data.get("stage_event_index"),
                agent_id=evt_data.get("agent_id", ""),
                agent_role=evt_data.get("agent_role", ""),
                depends_on=evt_data.get("depends_on", []),
                source_entity_id=evt_data.get("source_entity_id", ""),
                target_entity_id=evt_data.get("target_entity_id", ""),
                event_labels=event_labels,
                # Optional fields that may be present in serialized data
                input_text=evt_data.get("input_text"),
                output_text=evt_data.get("output_text"),
                tool_name=evt_data.get("tool_name"),
                tool_result=evt_data.get("tool_result"),
                tool_arguments=evt_data.get("tool_arguments"),
                tool_error=evt_data.get("tool_error"),
                memory_key=evt_data.get("memory_key"),
                memory_scope=evt_data.get("memory_scope"),
                document_path=evt_data.get("document_path"),
                provenance_ids=evt_data.get("provenance_ids", []),
                source_entity_type=evt_data.get("source_entity_type", ""),
                target_entity_type=evt_data.get("target_entity_type", ""),
            )
            events.append(evt)

        trace = Trace(
            trace_id=data.get("trace_id", ""),
            execution_id=data.get("execution_id", data.get("trace_id", "")),
            variant=variant,
            events=events,
            labels=labels or TraceLabels(),
            metadata=data.get("metadata", {}),
        )
        return trace

    def build_anomaly_labels(self, traces: List[Any]) -> List[float]:
        """Construct supervision using the benchmark's propagation semantics.

        Clean references are fixture/topology/variant specific and deduplicated
        by repetition, as in PropagationAnalyzer. Unassessable LEP executions
        must not silently become negative training examples.
        """
        from benchmark.behavioral_anomaly import MIN_CLEAN_RUNS_REQUIRED
        from benchmark.propagation_analysis import (
            CleanReference, PropagationAnalyzer, _select_execution_variant,
        )

        references = {}
        for trace in traces:
            meta = trace.metadata
            if meta.get("condition") != "benign" and meta.get("lep_codes"):
                continue
            fixture = meta.get("fixture_id", "")
            if not fixture:
                continue
            key = (fixture, meta.get("topology", "unknown"),
                   meta.get("execution_variant", "standard"))
            if key not in references:
                references[key] = CleanReference(
                    fixture, meta.get("task_family", "unknown"), key[1], key[2],
                )
            ref = references[key]
            repetition = meta.get("repetition_index", 0)
            if repetition not in ref.repetition_indices:
                ref.traces.append(trace)
                ref.repetition_indices.append(repetition)

        analyzer = PropagationAnalyzer(self.output_dir)
        labels = []
        for trace in traces:
            meta = trace.metadata
            codes = meta.get("lep_codes", [])
            if meta.get("condition") == "benign" or not codes:
                # Benchmark semantics require propagation from an LEP origin.
                labels.append(0.0)
                continue
            code = codes[0]  # Same cell identity as propagation analysis.
            key = (meta.get("fixture_id", ""), meta.get("topology", "unknown"),
                   _select_execution_variant(code))
            ref = references.get(key)
            if ref is None or ref.num_runs < MIN_CLEAN_RUNS_REQUIRED:
                raise ValueError(
                    f"Cannot construct behavioral anomaly target for {trace.trace_id}: "
                    f"clean reference {key} requires {MIN_CLEAN_RUNS_REQUIRED} "
                    "distinct repetitions"
                )
            result = analyzer._analyze_single_trace(
                trace, ref, code, meta.get("propagation_mode", "single_origin"),
                self.graph_builder, fixture_id=key[0],
            )
            labels.append(float(result.propagation_occurred))
        return labels

    def build_snapshots(
        self,
        event_graphs: List[EventGraph],
    ) -> List[List[EventGraph]]:
        """Build temporal snapshots for static GNN training.

        For each EventGraph, produces a list of incremental snapshots.

        Args:
            event_graphs: List of EventGraph objects.

        Returns:
            List of snapshot lists (one list per EventGraph).
        """
        all_snapshots = []
        for eg in event_graphs:
            snapshots = self.snapshot_builder.build_from_event_graph(eg)
            all_snapshots.append(snapshots)

        total_snapshots = sum(len(s) for s in all_snapshots)
        logger.info(
            "Built %d total snapshots from %d EventGraphs (avg %.1f per graph)",
            total_snapshots, len(event_graphs),
            total_snapshots / max(len(event_graphs), 1),
        )
        return all_snapshots

    def encode_graphs(
        self,
        event_graphs: List[EventGraph],
        labels: Optional[List[float]] = None,
    ) -> Tuple[List[StaticGraphData], List[TemporalGraphData]]:
        """Encode EventGraphs for detector training.

        Args:
            event_graphs: List of EventGraph objects.
            labels:       Optional explicit labels.

        Returns:
            (static_data, temporal_data) — both lists have the same length
            as event_graphs.
        """
        static = self.encoder.encode_event_graph_static(event_graphs, labels)
        temporal = self.encoder.encode_event_graph_temporal(event_graphs, labels)
        return static, temporal

    def build_dataset(
        self,
        static_data: List[StaticGraphData],
        temporal_data: List[TemporalGraphData],
        labels: List[float],
        group_ids: List[str],
        train_frac: float = 0.7,
        val_frac: float = 0.15,
        prefer_temporal: bool = True,
    ) -> DetectorDataset:
        """Create train/val/test dataset from encoded graphs.

        Args:
            static_data:    Encoded static graphs.
            temporal_data:  Encoded temporal graphs.
            labels:         Float labels.
            group_ids:  Group IDs (task-instance level) for grouped split.
            train_frac:     Training fraction.
            val_frac:       Validation fraction.
            prefer_temporal: Use temporal graphs if available.

        Returns:
            DetectorDataset with train/val/test splits.
        """
        dataset = DetectorDataset.from_encoded_graphs(
            static_graphs=static_data,
            temporal_graphs=temporal_data,
            labels=labels,
            group_ids=group_ids,
            train_frac=train_frac,
            val_frac=val_frac,
            seed=self.seed,
            prefer_temporal=prefer_temporal,
        )

        logger.info("Dataset distribution: %s", dataset.class_distribution())
        return dataset

    def train_detector(
        self,
        detector_type: str,
        dataset: DetectorDataset,
        num_epochs: int = 100,
        batch_size: int = 8,
        patience: int = 10,
        lr: float = 1e-3,
    ) -> PipelineResult:
        """Train a single detector type.

        Args:
            detector_type: One of ``"static_gnn"``, ``"tgnn"``, ``"hybrid"``.
            dataset:       DetectorDataset with train/val/test splits.
            num_epochs:    Maximum epochs.
            batch_size:    Batch size.
            patience:      Early stopping patience.
            lr:            Learning rate.

        Returns:
            PipelineResult with training history and test metrics.
        """
        # Determine input type
        prefer_temporal = detector_type in ("tgnn", "hybrid")
        model_type = detector_type.split("_")[0]  # "static" or "tgnn"

        # Initialize model
        if detector_type == "static_gnn":
            model = StaticGNN(node_feature_dim=OBSERVABLE_NODE_FEATURE_DIM, hidden_dim=64, num_layers=3, dropout=0.1)
        elif detector_type == "tgnn":
            model = TemporalGNN(
                node_feature_dim=OBSERVABLE_NODE_FEATURE_DIM, memory_dim=64, time_dim=16, dropout=0.1
            )
        elif detector_type == "hybrid":
            model = HybridDetector(
                node_feature_dim=OBSERVABLE_NODE_FEATURE_DIM, memory_dim=64, time_dim=16, fusion_dim=32, dropout=0.1
            )
        else:
            raise ValueError(f"Unknown detector type: {detector_type}")

        # Create trainer
        ckpt_dir = self.checkpoint_dir / detector_type
        trainer = DetectorTrainer(
            model=model,
            model_type=model_type,
            device=self.device,
            lr=lr,
            checkpoint_dir=ckpt_dir,
        )

        val_metadata = test_metadata = None
        if detector_type == "static_gnn":
            val_metadata = [{"execution_id": g.execution_id} for g in dataset.val_graphs]
            test_metadata = [{"execution_id": g.execution_id} for g in dataset.test_graphs]

        # Train
        history = trainer.train(
            train_graphs=dataset.train_graphs,
            train_labels=dataset.train_labels,
            val_graphs=dataset.val_graphs,
            val_labels=dataset.val_labels,
            num_epochs=num_epochs,
            batch_size=batch_size,
            patience=patience,
            verbose=True,
            snapshot_metadata=val_metadata,
        )

        # Evaluate on test set
        test_results = trainer.evaluate(
            dataset.test_graphs, dataset.test_labels, batch_size=batch_size,
            snapshot_metadata=test_metadata
        )

        result = PipelineResult(
            detector_type=detector_type,
            best_val_auroc=trainer.best_val_auroc,
            best_epoch=trainer.best_epoch,
            test_metrics=test_results,
            train_history=history["history"],
            evaluation_unit=test_results["evaluation_unit"],
        )

        logger.info(
            "%s: best_val_auroc=%.4f, test_auroc=%.4f, test_f1=%.4f",
            detector_type,
            result.best_val_auroc,
            test_results["metrics"]["auroc"],
            test_results["metrics"]["f1"],
        )

        return result

    def run_cross_validation(
        self, detector_types=None, snapshot_interval=5, num_epochs=100,
        batch_size=8, patience=10, lr=1e-4, weight_decay=1e-4,
        cv=None, use_heuristic_baselines=True,
    ):
        """Nested CV; epoch budget selected by complete inner OOF AUPRC.

        ``patience`` is unused: fixed epoch candidates replace split-specific
        early stopping. Static training uses all prefixes; scoring uses only
        the final graph of each execution.
        """
        import torch
        from training.cross_validation import NestedGroupCV
        cv = cv or NestedGroupCV(seed=self.seed)
        if num_epochs < 1:
            raise ValueError("num_epochs must be positive")
        benign, malignant = self.load_traces()
        traces = benign + malignant
        labels = self.build_anomaly_labels(traces)
        graphs, ys, groups = [], [], []
        for trace, label in zip(traces, labels):
            meta = getattr(trace, "metadata", {}) or {}
            graph = self.graph_builder.build(
                trace, topology_name=meta.get("topology", "unknown"),
                task_family=meta.get("task_family", "unknown"), strict=False)
            if graph.num_nodes == 0:
                raise ValueError(f"Empty execution graph: {graph.execution_id}")
            graphs.append(graph)
            ys.append(label)
            groups.append(self._task_instance_id(trace))
        eids = [g.execution_id for g in graphs]
        if len(set(eids)) != len(eids):
            raise ValueError("Expected unique execution IDs")
        static, temporal = self.encode_graphs(graphs, ys)
        final_static = dict(zip(eids, static))
        temporal_map = dict(zip(eids, temporal))
        label_map = dict(zip(eids, ys))
        snapshot_map = {}
        detector_types = detector_types or ["static_gnn", "tgnn", "hybrid"]
        if "static_gnn" in detector_types:
            builder = TemporalSnapshotBuilder(snapshot_interval=snapshot_interval)
            for eid, graph, label in zip(eids, graphs, ys):
                snapshots = builder.build_from_event_graph(graph)
                encoded, _ = self.encode_graphs(snapshots, [label] * len(snapshots))
                snapshot_map[eid] = encoded or [final_static[eid]]
        configurations = [{"epochs": e} for e in sorted({max(1, num_epochs//4),
                                                         max(1, num_epochs//2), num_epochs})]
        results = {}
        for kind in detector_types:
            def fit(train_ids, train_labels, pos_weight, config):
                torch.manual_seed(self.seed)
                np.random.seed(self.seed)
                common = dict(node_feature_dim=OBSERVABLE_NODE_FEATURE_DIM, dropout=0.1)
                if kind == "static_gnn":
                    model = StaticGNN(**common, hidden_dim=64, num_layers=3)
                elif kind == "tgnn":
                    model = TemporalGNN(**common, memory_dim=64, time_dim=16)
                elif kind == "hybrid":
                    model = HybridDetector(**common, memory_dim=64, time_dim=16, fusion_dim=32)
                else:
                    raise ValueError(f"Unknown detector: {kind}")
                trainer = DetectorTrainer(model, model_type="static" if kind == "static_gnn" else "temporal",
                                          device=self.device, lr=lr, weight_decay=weight_decay)
                trainer.criterion = torch.nn.BCEWithLogitsLoss(
                    pos_weight=torch.tensor(pos_weight, device=trainer.device))
                train_graphs, expanded_labels = [], []
                for eid, label in zip(train_ids, train_labels):
                    samples = snapshot_map[eid] if kind == "static_gnn" else [temporal_map[eid]]
                    train_graphs.extend(samples)
                    expanded_labels.extend([label] * len(samples))
                for _ in range(config["epochs"]):
                    trainer.train_epoch(train_graphs, expanded_labels, batch_size=batch_size)
                return trainer

            def predict(trainer, ids):
                mapping = final_static if kind == "static_gnn" else temporal_map
                # evaluate does a single inference pass; its unused loss/metrics
                # cannot influence fitting, configuration, or calibration.
                return trainer.evaluate([mapping[e] for e in ids],
                                        [label_map[e] for e in ids], batch_size=batch_size)["predictions"]

            results[kind] = cv.run(eids, ys, groups, fit, predict, configurations)
            results[kind].save(self.checkpoint_dir / f"{kind}_nested_cv.json")
        if use_heuristic_baselines:
            for name, config in [("random", HeuristicDetector.RANDOM),
                                 ("degree", HeuristicDetector.DEGREE_BASED),
                                 ("temporal", HeuristicDetector.TEMPORAL)]:
                mapping = final_static if name == "degree" else temporal_map
                def fit_baseline(ids, labels, weight, configuration):
                    np.random.seed(self.seed)
                    detector = HeuristicDetector(config)
                    if name != "random":
                        detector.fit([mapping[e] for e in ids])
                    return detector
                def predict_baseline(detector, ids):
                    predictions = [detector.detect(mapping[e]) for e in ids]
                    return [r.confidence if r.is_malignant else 1-r.confidence for r in predictions]
                results[name] = cv.run(eids, ys, groups, fit_baseline, predict_baseline, [{}])
                results[name].save(self.checkpoint_dir / f"{name}_nested_cv.json")
        return results

    def run(
        self,
        detector_types: List[str] = None,
        snapshot_interval: int = 5,
        num_epochs: int = 100,
        batch_size: int = 8,
        patience: int = 10,
        lr: float = 1e-3,
        train_frac: float = 0.7,
        val_frac: float = 0.15,
        use_heuristic_baselines: bool = True,
    ) -> Dict[str, Any]:
        """Run the full pipeline: load, encode, train, evaluate.

        Args:
            detector_types:       List of detector types to train.
            snapshot_interval:    Snapshot every N events (for static GNN).
            num_epochs:           Maximum training epochs per detector.
            batch_size:           Training batch size.
            patience:             Early stopping patience.
            lr:                   Learning rate.
            train_frac:           Training fraction.
            val_frac:             Validation fraction.
            use_heuristic_baselines: Also evaluate heuristic baselines.

        Returns:
            Dict mapping detector name → PipelineResult.
        """
        if detector_types is None:
            detector_types = ["static_gnn", "tgnn", "hybrid"]

        # Step 1: Load traces
        logger.info("Step 1: Loading traces from %s", self.output_dir)
        benign_traces, malignant_traces = self.load_traces()

        if not benign_traces and not malignant_traces:
            raise ValueError("No traces found. Run the benchmark first.")

        all_traces = benign_traces + malignant_traces
        # Supervision is built separately from detector-visible graph features.
        trace_anomaly_labels = self.build_anomaly_labels(all_traces)
        # Build graphs and collect labels/group IDs together to stay aligned
        event_graphs = []
        anomaly_labels = []
        all_group_ids = []

        logger.info("Step 2: Building EventGraphs (per-trace metadata)")
        for trace, anomaly_label in zip(all_traces, trace_anomaly_labels):
            try:
                meta = getattr(trace, "metadata", {}) or {}
                topo = meta.get("topology", "unknown")
                task = meta.get("task_family", "unknown")
                graph = self.graph_builder.build(
                    trace, topology_name=topo, task_family=task, strict=False,
                )
                if graph.num_nodes > 0:
                    event_graphs.append(graph)
                    anomaly_labels.append(anomaly_label)
                    all_group_ids.append(self._task_instance_id(trace))
            except Exception as e:
                logger.warning("Skipping trace %s: %s", trace.trace_id, e)

        if len(event_graphs) < 2:
            raise ValueError(
                f"Need at least 2 EventGraphs for training, got {len(event_graphs)}"
            )

        logger.info(
            "Built %d EventGraphs, %d with downstream behavioral anomaly=True",
            len(event_graphs), sum(anomaly_labels),
        )

        # Step 3: Encode
        logger.info("Step 3: Encoding graphs")
        static_data, temporal_data = self.encode_graphs(event_graphs, anomaly_labels)

        # Execution IDs per EventGraph (needed for execution-level split)
        execution_ids: List[str] = [
            eg.execution_id for eg in event_graphs
        ]

        # ── Step 4: Split at execution level ─────────────────────────────────
        # One shared split for ALL detectors: disjoint execution IDs.
        # This ensures Static, TGNN, and Hybrid headline evaluations
        # use the same held-out executions.
        logger.info("Step 4: Splitting at execution level")
        from training.dataset import split_at_execution_level
        train_eids, val_eids, test_eids = split_at_execution_level(
            execution_ids, anomaly_labels, group_ids=all_group_ids,
            train_frac=train_frac, val_frac=val_frac, seed=self.seed,
        )

        if len(set(execution_ids)) != len(execution_ids):
            raise ValueError("Expected a unique execution_id for each trace")

        # Map execution_id → index in event_graphs list
        exec_to_idx: Dict[str, int] = {}
        for i, eg in enumerate(event_graphs):
            exec_to_idx[eg.execution_id] = i

        train_indices = {exec_to_idx[eid] for eid in train_eids if eid in exec_to_idx}
        val_indices   = {exec_to_idx[eid] for eid in val_eids   if eid in exec_to_idx}
        test_indices  = {exec_to_idx[eid] for eid in test_eids  if eid in exec_to_idx}

        train_exec_ids_ordered  = [eid for eid in execution_ids if eid in train_eids]
        val_exec_ids_ordered    = [eid for eid in execution_ids if eid in val_eids]
        test_exec_ids_ordered   = [eid for eid in execution_ids if eid in test_eids]

        logger.info(
            "Execution split: train=%d, val=%d, test=%d (total=%d executions)",
            len(train_exec_ids_ordered), len(val_exec_ids_ordered),
            len(test_exec_ids_ordered), len(execution_ids),
        )

        # Assert the three detectors will see the same execution sets
        assert set(train_exec_ids_ordered) == train_eids
        assert set(val_exec_ids_ordered)   == val_eids
        assert set(test_exec_ids_ordered)  == test_eids

        # ── Step 5: Build datasets for each detector type ─────────────────────
        results = {}

        # ── Static GNN ────────────────────────────────────────────────────────
        if "static_gnn" in detector_types:
            logger.info("Step 5a: Building Static GNN datasets")
            snapshot_builder = TemporalSnapshotBuilder(snapshot_interval=snapshot_interval)

            # Build ALL snapshots from each EventGraph
            all_snapshots: List[Any] = []
            snap_labels: List[float] = []
            snap_metadata: List[Dict[str, Any]] = []
            snap_exec_ids: List[str] = []

            for eg, lbl in zip(event_graphs, anomaly_labels):
                snaps = snapshot_builder.build_from_event_graph(eg)
                for snap in snaps:
                    all_snapshots.append(snap)
                    snap_labels.append(lbl)
                    snap_exec_ids.append(eg.execution_id)
                    snap_metadata.append({
                        "execution_id": eg.execution_id,
                        "trace_id": snap.trace_id,
                        "num_nodes": snap.num_nodes,
                        "is_final": snap.num_nodes == eg.num_nodes,
                    })

            # Encode all snapshots
            snap_static, _ = self.encode_graphs(all_snapshots, snap_labels)

            # ── Training: ALL snapshots from training executions ─────────────
            train_snap_indices = [
                i for i, eid in enumerate(snap_exec_ids) if eid in train_eids
            ]
            train_snap_graphs  = [snap_static[i] for i in train_snap_indices]
            train_snap_labels  = [snap_labels[i] for i in train_snap_indices]
            train_snap_gids    = [snap_exec_ids[i] for i in train_snap_indices]
            train_snap_meta    = [snap_metadata[i] for i in train_snap_indices]

            # ── Val / Test: FINAL snapshot per execution ────────────────────
            def _select_final_snapshots(eid_set: set) -> tuple:
                """Select the final snapshot for each execution in eid_set."""
                indices = [i for i, eid in enumerate(snap_exec_ids) if eid in eid_set]
                graphs, labels, ids = select_final_snapshots_per_execution(
                    [snap_static[i] for i in indices],
                    [snap_labels[i] for i in indices],
                    [snap_metadata[i] for i in indices],
                )
                if set(ids) != eid_set:
                    raise ValueError("Missing final snapshots for held-out executions")
                metadata = [{"execution_id": eid} for eid in ids]
                return graphs, labels, metadata, ids

            val_snap_graphs, val_snap_labels, val_snap_meta, val_snap_gids = \
                _select_final_snapshots(val_eids)
            test_snap_graphs, test_snap_labels, test_snap_meta, test_snap_gids = \
                _select_final_snapshots(test_eids)

            # Build datasets
            from training.dataset import DetectorDataset
            train_sample_count = len(train_snap_graphs)

            # We need a dataset-like structure for val/test that carries
            # snapshot_metadata.  Use DetectorDataset for train (all snapshots)
            # and FinalSnapshotDataset for val/test (one per execution).
            from training.dataset import FinalSnapshotDataset

            static_train_dataset = DetectorDataset(
                train_graphs=train_snap_graphs,
                val_graphs=val_snap_graphs,
                test_graphs=test_snap_graphs,
                train_labels=train_snap_labels,
                val_labels=val_snap_labels,
                test_labels=test_snap_labels,
                _train_group_ids=train_snap_gids,
                _val_group_ids=val_snap_gids,
                _test_group_ids=test_snap_gids,
            )

            # Use a dataset wrapper that carries snapshot_metadata for
            # execution-level evaluation
            static_val_dataset = FinalSnapshotDataset(
                graphs=val_snap_graphs, labels=val_snap_labels,
                snapshot_metadata=val_snap_meta, execution_ids=list(val_snap_gids),
            )
            static_test_dataset = FinalSnapshotDataset(
                graphs=test_snap_graphs, labels=test_snap_labels,
                snapshot_metadata=test_snap_meta, execution_ids=list(test_snap_gids),
            )

        # ── Temporal models (TGNN, Hybrid) ───────────────────────────────────
        temporal_detectors = [d for d in detector_types if d in ("tgnn", "hybrid")]
        if temporal_detectors or use_heuristic_baselines:
            logger.info("Step 5b: Building temporal datasets (execution-level)")
            # Use execution-level split: one TemporalGraphData per execution
            train_temporal_graphs = [temporal_data[i] for i in sorted(train_indices)]
            train_temporal_labels = [anomaly_labels[i] for i in sorted(train_indices)]
            val_temporal_graphs   = [temporal_data[i] for i in sorted(val_indices)]
            val_temporal_labels   = [anomaly_labels[i] for i in sorted(val_indices)]
            test_temporal_graphs  = [temporal_data[i] for i in sorted(test_indices)]
            test_temporal_labels  = [anomaly_labels[i] for i in sorted(test_indices)]

            temporal_dataset = DetectorDataset(
                train_graphs=train_temporal_graphs,
                val_graphs=val_temporal_graphs,
                test_graphs=test_temporal_graphs,
                train_labels=train_temporal_labels,
                val_labels=val_temporal_labels,
                test_labels=test_temporal_labels,
                _train_group_ids=list(train_exec_ids_ordered),
                _val_group_ids=list(val_exec_ids_ordered),
                _test_group_ids=list(test_exec_ids_ordered),
            )
        else:
            temporal_dataset = None

        # ── Step 6: Train and evaluate each detector ──────────────────────────
        for det_type in detector_types:
            logger.info("Training %s detector...", det_type)

            if det_type == "static_gnn" and static_train_dataset is not None:
                result = self._train_static_gnn(
                    static_train_dataset, static_val_dataset, static_test_dataset,
                    num_epochs=num_epochs, batch_size=batch_size,
                    patience=patience, lr=lr,
                    train_sample_count=train_sample_count,
                    train_exec_ids=train_exec_ids_ordered,
                    val_exec_ids=val_exec_ids_ordered,
                    test_exec_ids=test_exec_ids_ordered,
                )
            elif det_type in ("tgnn", "hybrid") and temporal_dataset is not None:
                result = self._train_temporal_detector(
                    det_type, temporal_dataset,
                    num_epochs=num_epochs, batch_size=batch_size,
                    patience=patience, lr=lr,
                    train_exec_ids=train_exec_ids_ordered,
                    val_exec_ids=val_exec_ids_ordered,
                    test_exec_ids=test_exec_ids_ordered,
                )
            else:
                logger.warning("No dataset available for %s, skipping", det_type)
                continue

            results[det_type] = result

        # Step 7: Heuristic baselines
        if use_heuristic_baselines and temporal_dataset is not None:
            logger.info("Evaluating heuristic baselines...")
            heuristic_results = self._evaluate_heuristics(
                temporal_dataset, benign_traces,
                static_graphs={g.execution_id: g for g in static_data},
            )
            results.update(heuristic_results)

        # Step 8: Comparison
        logger.info("Step 8: Comparing detectors")
        comparator = BaselineComparator()
        for det_type, result in results.items():
            comparator.add_results(
                detector_name=det_type,
                y_true=result.test_metrics.get("labels", []),
                y_scores=result.test_metrics.get("predictions", []),
            )

        comparison_df = comparator.compare()
        results["_comparison"] = comparison_df

        logger.info("\n%s", comparator.summary())

        # Save results
        self._save_results(results)
        return results

    def _train_static_gnn(
        self,
        train_dataset: DetectorDataset,
        val_dataset: FinalSnapshotDataset,
        test_dataset: FinalSnapshotDataset,
        num_epochs: int,
        batch_size: int,
        patience: int,
        lr: float,
        train_sample_count: int,
        train_exec_ids: List[str],
        val_exec_ids: List[str],
        test_exec_ids: List[str],
    ) -> PipelineResult:
        """Train static GNN with execution-level headline evaluation.

        Training uses all snapshots from training executions.  Validation
        and test use the final snapshot per execution.  The trainer
        receives ``snapshot_metadata`` for execution-level aggregation.
        """
        from detectors.static_gnn import StaticGNN
        from generation.feature_schema import OBSERVABLE_NODE_FEATURE_DIM

        model = StaticGNN(
            node_feature_dim=OBSERVABLE_NODE_FEATURE_DIM,
            hidden_dim=64, num_layers=3, dropout=0.1,
        )

        ckpt_dir = self.checkpoint_dir / "static_gnn"
        trainer = DetectorTrainer(
            model=model, model_type="static", device=self.device,
            lr=lr, checkpoint_dir=ckpt_dir,
        )

        # Train on ALL snapshots from training executions
        history = trainer.train(
            train_graphs=train_dataset.train_graphs,
            train_labels=train_dataset.train_labels,
            val_graphs=val_dataset.graphs,
            val_labels=val_dataset.labels,
            num_epochs=num_epochs, batch_size=batch_size,
            patience=patience, verbose=True,
            snapshot_metadata=val_dataset.snapshot_metadata,
        )

        # Evaluate on FINAL snapshots per execution for test headline
        test_results = trainer.evaluate(
            test_dataset.graphs, test_dataset.labels,
            batch_size=batch_size,
            snapshot_metadata=test_dataset.snapshot_metadata,
        )

        return PipelineResult(
            detector_type="static_gnn",
            best_val_auroc=trainer.best_val_auroc,
            best_epoch=trainer.best_epoch,
            test_metrics=test_results,
            train_history=history["history"],
            evaluation_unit=test_results.get("evaluation_unit", "execution"),
            train_sample_count=train_sample_count,
            val_execution_count=len(val_dataset),
            test_execution_count=len(test_dataset),
            train_execution_ids=list(train_exec_ids),
            val_execution_ids=list(val_exec_ids),
            test_execution_ids=list(test_exec_ids),
        )

    def _train_temporal_detector(
        self,
        det_type: str,
        dataset: DetectorDataset,
        num_epochs: int,
        batch_size: int,
        patience: int,
        lr: float,
        train_exec_ids: List[str],
        val_exec_ids: List[str],
        test_exec_ids: List[str],
    ) -> PipelineResult:
        """Train TGNN or Hybrid detector (execution-level by design)."""
        from detectors.tgnn import TemporalGNN
        from detectors.hybrid import HybridDetector
        from generation.feature_schema import OBSERVABLE_NODE_FEATURE_DIM

        if det_type == "tgnn":
            model = TemporalGNN(
                node_feature_dim=OBSERVABLE_NODE_FEATURE_DIM,
                memory_dim=64, time_dim=16, dropout=0.1,
            )
        elif det_type == "hybrid":
            model = HybridDetector(
                node_feature_dim=OBSERVABLE_NODE_FEATURE_DIM,
                memory_dim=64, time_dim=16, fusion_dim=32, dropout=0.1,
            )
        else:
            raise ValueError(f"Unknown temporal detector: {det_type}")

        ckpt_dir = self.checkpoint_dir / det_type
        trainer = DetectorTrainer(
            model=model, model_type="tgnn", device=self.device,
            lr=lr, checkpoint_dir=ckpt_dir,
        )

        # Train: one TemporalGraphData per execution → naturally execution-level
        history = trainer.train(
            train_graphs=dataset.train_graphs,
            train_labels=dataset.train_labels,
            val_graphs=dataset.val_graphs,
            val_labels=dataset.val_labels,
            num_epochs=num_epochs, batch_size=batch_size,
            patience=patience, verbose=True,
        )

        # Evaluate: one final score per execution
        test_results = trainer.evaluate(
            dataset.test_graphs, dataset.test_labels,
            batch_size=batch_size,
        )

        return PipelineResult(
            detector_type=det_type,
            best_val_auroc=trainer.best_val_auroc,
            best_epoch=trainer.best_epoch,
            test_metrics=test_results,
            train_history=history["history"],
            evaluation_unit="execution",
            train_sample_count=len(dataset.train_graphs),
            val_execution_count=len(dataset.val_graphs),
            test_execution_count=len(dataset.test_graphs),
            train_execution_ids=list(train_exec_ids),
            val_execution_ids=list(val_exec_ids),
            test_execution_ids=list(test_exec_ids),
        )

    def _evaluate_heuristics(
        self, dataset: DetectorDataset, benign_traces: List[Any],
        static_graphs: Optional[Dict[str, StaticGraphData]] = None,
    ) -> Dict[str, PipelineResult]:
        """Evaluate heuristic baselines on the test set.

        Fits HeuristicDetector on training data, then evaluates on test.
        """
        results = {}

        for mode_name, config in [
            ("random", HeuristicDetector.RANDOM),
            ("degree", HeuristicDetector.DEGREE_BASED),
            ("temporal", HeuristicDetector.TEMPORAL),
        ]:
            try:
                detector = HeuristicDetector(config)

                train_graphs = dataset.train_graphs
                test_graphs = dataset.test_graphs
                if mode_name == "degree" and static_graphs is not None:
                    train_graphs = [static_graphs[g.execution_id] for g in train_graphs]
                    test_graphs = [static_graphs[g.execution_id] for g in test_graphs]

                # Fit on training data
                if mode_name != "random":
                    detector.fit(train_graphs)

                # Evaluate on test set
                test_preds = []
                test_labels = dataset.test_labels
                for g in test_graphs:
                    result = detector.detect(g)
                    test_preds.append(result.confidence if result.is_malignant else 1.0 - result.confidence)

                metrics = {
                    "loss": 0.0,
                    "metrics": {
                        "auroc": float(np.mean(test_preds)) if test_labels else 0.0,
                        "auprc": 0.0,
                        "f1": 0.0,
                        "accuracy": 0.0,
                        "precision": 0.0,
                        "recall": 0.0,
                    },
                    "predictions": test_preds,
                    "labels": test_labels,
                }

                # Compute actual metrics
                if test_preds and test_labels:
                    from training.metrics import compute_classification_metrics
                    metrics["metrics"] = compute_classification_metrics(
                        np.array(test_labels), np.array(test_preds)
                    )

                results[mode_name] = PipelineResult(
                    detector_type=mode_name,
                    best_val_auroc=metrics["metrics"].get("auroc", 0.0),
                    best_epoch=0,
                    test_metrics=metrics,
                    train_history=[],
                    evaluation_unit="execution",
                    train_sample_count=len(dataset.train_graphs),
                    val_execution_count=len(dataset.val_graphs),
                    test_execution_count=len(dataset.test_graphs),
                    train_execution_ids=[g.execution_id for g in dataset.train_graphs],
                    val_execution_ids=[g.execution_id for g in dataset.val_graphs],
                    test_execution_ids=[g.execution_id for g in dataset.test_graphs],
                )

                logger.info(
                    "%s: auroc=%.4f, f1=%.4f",
                    mode_name,
                    metrics["metrics"].get("auroc", 0.0),
                    metrics["metrics"].get("f1", 0.0),
                )

            except Exception as e:
                logger.warning("Heuristic %s failed: %s", mode_name, e)

        return results

    def _save_results(self, results: Dict[str, Any]) -> None:
        """Save pipeline results to disk."""
        output_path = self.output_dir / "detector_results.json"

        serializable = {}
        for key, value in results.items():
            if isinstance(value, PipelineResult):
                serializable[key] = asdict(value)
            elif hasattr(value, "to_string"):  # DataFrame
                serializable[key] = value.to_dict(orient="records")
            else:
                serializable[key] = str(value)

        with open(output_path, "w") as f:
            json.dump(serializable, f, indent=2, default=str)

        logger.info("Results saved to %s", output_path)
