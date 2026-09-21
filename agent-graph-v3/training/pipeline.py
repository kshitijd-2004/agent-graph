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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np

from generation.event_graph_builder import DependsOnGraphBuilder, EventGraph
from generation.event_graph_snapshot import TemporalSnapshotBuilder
from generation.heuristic_detector import DetectionResult, HeuristicDetector
from encoder import GraphEncoder, StaticGraphData, TemporalGraphData
from detectors.static_gnn import StaticGNN, DetectionOutput as StaticOutput
from detectors.tgnn import TemporalGNN, TemporalDetectionOutput
from detectors.hybrid import HybridDetector, HybridDetectionOutput
from training.dataset import DetectorDataset, create_dataloaders
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
    """
    detector_type: str
    best_val_auroc: float
    best_epoch: int
    test_metrics: Dict[str, Any]
    train_history: List[Dict[str, float]]


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

        Uses fixture_id — the stable identifier shared by all variants
        (benign + LEP) of the same underlying task fixture — so they
        stay in the same train/val/test split.
        """
        meta = getattr(trace, "metadata", {}) or {}
        return meta.get("fixture_id", "unknown")

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
            group_id=data.get("group_id", ""),
            events=events,
            variant=variant,
            labels=labels or TraceLabels(),
            metadata=data.get("metadata", {}),
        )
        return trace

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
            model = StaticGNN(node_feature_dim=24, hidden_dim=64, num_layers=3, dropout=0.1)
        elif detector_type == "tgnn":
            model = TemporalGNN(
                node_feature_dim=24, memory_dim=64, time_dim=16, dropout=0.1
            )
        elif detector_type == "hybrid":
            model = HybridDetector(
                node_feature_dim=24, memory_dim=64, time_dim=16, fusion_dim=32, dropout=0.1
            )
        else:
            raise ValueError(f"Unknown detector type: {detector_type}")

        # Create trainer
        ckpt_dir = self.checkpoint_dir / detector_type
        trainer = DetectorTrainer(
            model=model,
            model_type=model_type,
            device="auto",
            lr=lr,
            checkpoint_dir=ckpt_dir,
        )

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
        )

        # Evaluate on test set
        test_results = trainer.evaluate(
            dataset.test_graphs, dataset.test_labels, batch_size=batch_size
        )

        result = PipelineResult(
            detector_type=detector_type,
            best_val_auroc=trainer.best_val_auroc,
            best_epoch=trainer.best_epoch,
            test_metrics=test_results,
            train_history=history["history"],
        )

        logger.info(
            "%s: best_val_auroc=%.4f, test_auroc=%.4f, test_f1=%.4f",
            detector_type,
            result.best_val_auroc,
            test_results["metrics"]["auroc"],
            test_results["metrics"]["f1"],
        )

        return result

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
        # Labels from actual downstream failure, not from variant
        # Build graphs and collect labels/group IDs together to stay aligned
        event_graphs = []
        all_labels = []
        all_group_ids = []

        logger.info("Step 2: Building EventGraphs (per-trace metadata)")
        for trace in all_traces:
            try:
                meta = getattr(trace, "metadata", {}) or {}
                topo = meta.get("topology", "unknown")
                task = meta.get("task_family", "unknown")
                graph = self.graph_builder.build(
                    trace, topology_name=topo, task_family=task, strict=False,
                )
                if graph.num_nodes > 0:
                    event_graphs.append(graph)
                    all_labels.append(float(trace.labels.downstream_failure))
                    all_group_ids.append(self._task_instance_id(trace))
            except Exception as e:
                logger.warning("Skipping trace %s: %s", trace.trace_id, e)

        if len(event_graphs) < 2:
            raise ValueError(
                f"Need at least 2 EventGraphs for training, got {len(event_graphs)}"
            )

        logger.info(
            "Built %d EventGraphs, %d with downstream_failure=True",
            len(event_graphs), sum(all_labels),
        )

        # Step 3: Encode
        logger.info("Step 3: Encoding graphs")
        static_data, temporal_data = self.encode_graphs(event_graphs)

        # Step 4: Build snapshots for static GNN
        if "static_gnn" in detector_types:
            logger.info("Step 4: Building temporal snapshots for static GNN")
            snapshot_builder = TemporalSnapshotBuilder(snapshot_interval=snapshot_interval)
            # Build snapshots from each EventGraph
            snapshot_graphs = []
            snapshot_labels = []
            snapshot_group_ids = []
            for eg, lbl, gid in zip(event_graphs, all_labels, all_group_ids):
                snaps = snapshot_builder.build_from_event_graph(eg)
                for snap in snaps:
                    snapshot_graphs.append(snap)
                    snapshot_labels.append(lbl)
                    snapshot_group_ids.append(gid)

            # Encode snapshots (pass labels explicitly since encode_graphs no longer defaults)
            snap_static, _ = self.encode_graphs(snapshot_graphs, snapshot_labels)

            # Override dataset for static GNN
            static_dataset = self.build_dataset(
                snap_static, [], snapshot_labels, snapshot_group_ids,
                train_frac=train_frac, val_frac=val_frac, prefer_temporal=False,
            )
        else:
            static_dataset = None

        # Step 5: Build dataset for temporal models
        temporal_detectors = [d for d in detector_types if d in ("tgnn", "hybrid")]
        if temporal_detectors:
            temporal_dataset = self.build_dataset(
                static_data, temporal_data, all_labels, all_group_ids,
                train_frac=train_frac, val_frac=val_frac, prefer_temporal=True,
            )
        else:
            temporal_dataset = None

        # Step 6: Train and evaluate
        results = {}

        for det_type in detector_types:
            logger.info("Training %s detector...", det_type)
            if det_type == "static_gnn" and static_dataset is not None:
                result = self.train_detector(
                    det_type, static_dataset,
                    num_epochs=num_epochs, batch_size=batch_size, patience=patience, lr=lr,
                )
            elif temporal_dataset is not None:
                result = self.train_detector(
                    det_type, temporal_dataset,
                    num_epochs=num_epochs, batch_size=batch_size, patience=patience, lr=lr,
                )
            else:
                logger.warning("No dataset available for %s, skipping", det_type)
                continue
            results[det_type] = result

        # Step 7: Heuristic baselines
        if use_heuristic_baselines and temporal_dataset is not None:
            logger.info("Evaluating heuristic baselines...")
            heuristic_results = self._evaluate_heuristics(
                temporal_dataset, benign_traces
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

    def _evaluate_heuristics(
        self, dataset: DetectorDataset, benign_traces: List[Any]
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

                # Fit on training data
                if mode_name != "random":
                    detector.fit(dataset.train_graphs)

                # Evaluate on test set
                test_preds = []
                test_labels = dataset.test_labels
                for g in dataset.test_graphs:
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
                serializable[key] = {
                    "detector_type": value.detector_type,
                    "best_val_auroc": value.best_val_auroc,
                    "best_epoch": value.best_epoch,
                    "test_metrics": value.test_metrics,
                    "train_history": value.train_history,
                }
            elif hasattr(value, "to_string"):  # DataFrame
                serializable[key] = value.to_dict(orient="records")
            else:
                serializable[key] = str(value)

        with open(output_path, "w") as f:
            json.dump(serializable, f, indent=2, default=str)

        logger.info("Results saved to %s", output_path)
