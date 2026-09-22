"""Dataset loading and splitting for detector training.

Provides:
- DetectorDataset: Wraps StaticGraphData / TemporalGraphData lists with
  train/val/test splits grouped by execution_id.
- create_dataloaders: Returns PyG DataLoaders for static GNN training.
- select_final_snapshots_per_execution: Select one final/full-run snapshot
  per execution for static GNN headline evaluation.
- split_at_execution_level: Split by execution_id (not by individual snapshot),
  ensuring train/val/test use disjoint executions.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import torch
from torch_geometric.loader import DataLoader as PyGDataLoader

from encoder import StaticGraphData, TemporalGraphData
from generation.feature_schema import OBSERVABLE_NODE_FEATURE_DIM

logger = logging.getLogger(__name__)


def _stratified_group_split(
    graph_labels: List[float],
    group_ids: List[str],
    train_frac: float = 0.7,
    val_frac: float = 0.15,
    seed: int = 42,
) -> Tuple[List[int], List[int], List[int]]:
    """Split graph indices into train/val/test, grouped by task-instance group_id.

    All graphs sharing the same group_id stay in the same split to
    prevent data leakage.  Groups are shuffled deterministically and
    split proportionally.  The group label may be mixed (benign + LEP
    variants of the same task instance), so stratification by max-label
    is intentionally omitted.

    Args:
        graph_labels:  List of float labels (1.0 = malignant, 0.0 = benign).
        group_ids:     List of task-instance identifiers, one per graph.
        train_frac:    Fraction for training (default 0.7).
        val_frac:      Fraction for validation (default 0.15).
        seed:          Random seed for reproducibility.

    Returns:
        (train_indices, val_indices, test_indices)
    """
    rng = random.Random(seed)
    n = len(graph_labels)

    # Group indices by group_id
    group_to_indices: Dict[str, List[int]] = defaultdict(list)
    for i, gid in enumerate(group_ids):
        group_to_indices[gid].append(i)

    # Shuffle all groups and split proportionally (no stratification
    # — each group is a mixed task instance with benign + LEP runs).
    groups = list(group_to_indices.keys())
    rng.shuffle(groups)

    n_groups = len(groups)
    n_train = int(n_groups * train_frac)
    n_val = int(n_groups * val_frac)

    train_gids = groups[:n_train]
    val_gids = groups[n_train:n_train + n_val]
    test_gids = groups[n_train + n_val:]

    train_idx = [i for gid in train_gids for i in group_to_indices[gid]]
    val_idx   = [i for gid in val_gids   for i in group_to_indices[gid]]
    test_idx  = [i for gid in test_gids  for i in group_to_indices[gid]]

    logger.info(
        "Split %d graphs: train=%d (groups=%d), val=%d (groups=%d), test=%d (groups=%d)",
        n, len(train_idx), len(train_gids),
        len(val_idx), len(val_gids),
        len(test_idx), len(test_gids),
    )

    # Assertions: leakage-free, non-empty splits, both classes present
    train_set = set(train_gids)
    val_set   = set(val_gids)
    test_set  = set(test_gids)
    assert train_set.isdisjoint(val_set), "train/val group overlap"
    assert train_set.isdisjoint(test_set), "train/test group overlap"
    assert val_set.isdisjoint(test_set), "val/test group overlap"
    assert len(train_set) > 0, "train split is empty"
    assert len(val_set) > 0, "val split is empty"
    assert len(test_set) > 0, "test split is empty"

    for name, labels in [
        ("train", [graph_labels[i] for i in train_idx]),
        ("val",   [graph_labels[i] for i in val_idx]),
        ("test",  [graph_labels[i] for i in test_idx]),
    ]:
        pos = sum(labels)
        neg = len(labels) - pos
        print(f"{name}: N={len(labels)}, positive={pos}, negative={neg}")
        assert pos > 0, f"{name}: NO POSITIVES"
        assert neg > 0, f"{name}: NO NEGATIVES"

    print(f"train groups: {len(train_set)}")
    print(f"val groups:   {len(val_set)}")
    print(f"test groups:  {len(test_set)}")
    print("PASS: leakage-free grouped split with both classes")

    return train_idx, val_idx, test_idx


@dataclass
class DetectorDataset:
    """Dataset for detector training with train/val/test split.

    Wraps lists of StaticGraphData or TemporalGraphData objects and
    provides PyTorch DataLoaders. Splits are stratified by label and
    grouped by task-instance group_id to prevent data leakage.

    Attributes:
        train_graphs:   Training graphs.
        val_graphs:     Validation graphs.
        test_graphs:    Test graphs.
        train_labels:   Training labels.
        val_labels:     Validation labels.
        test_labels:    Test labels.
        _train_group_ids: Group IDs in train split (for sanity checks).
        _val_group_ids:   Group IDs in val split.
        _test_group_ids:  Group IDs in test split.
    """

    train_graphs: List[Union[StaticGraphData, TemporalGraphData]]
    val_graphs: List[Union[StaticGraphData, TemporalGraphData]]
    test_graphs: List[Union[StaticGraphData, TemporalGraphData]]
    train_labels: List[float]
    val_labels: List[float]
    test_labels: List[float]
    _train_group_ids: List[str] = field(default_factory=list)
    _val_group_ids: List[str] = field(default_factory=list)
    _test_group_ids: List[str] = field(default_factory=list)

    @classmethod
    def from_encoded_graphs(
        cls,
        static_graphs: List[StaticGraphData],
        temporal_graphs: List[TemporalGraphData],
        labels: List[float],
        group_ids: List[str],
        train_frac: float = 0.7,
        val_frac: float = 0.15,
        seed: int = 42,
        prefer_temporal: bool = True,
    ) -> "DetectorDataset":
        """Create a DetectorDataset from encoded graphs.

        Uses TemporalGraphData if available (preferred for TGNN/Hybrid),
        otherwise falls back to StaticGraphData.

        Args:
            static_graphs:    List of StaticGraphData objects.
            temporal_graphs:  List of TemporalGraphData objects.
            labels:           List of float labels (1.0 = malignant, 0.0 = benign).
            group_ids:        List of task-instance group identifiers.  All
                              graphs sharing a group_id stay in the same split.
            train_frac:       Training fraction.
            val_frac:         Validation fraction.
            seed:             Random seed.
            prefer_temporal:  If True and temporal_graphs are available, use
                              them for the dataset.

        Returns:
            DetectorDataset with train/val/test splits.
        """
        # Choose graph type
        graphs: List[Union[StaticGraphData, TemporalGraphData]]
        if prefer_temporal and len(temporal_graphs) == len(labels):
            graphs = temporal_graphs
        elif len(static_graphs) == len(labels):
            graphs = static_graphs
        else:
            raise ValueError(
                f"Graph count mismatch: static={len(static_graphs)}, "
                f"temporal={len(temporal_graphs)}, labels={len(labels)}"
            )

        if not (len(graphs) == len(labels) == len(group_ids)):
            raise ValueError(
                f"Length mismatch: graphs={len(graphs)}, labels={len(labels)}, "
                f"group_ids={len(group_ids)} — all must be equal"
            )

        train_idx, val_idx, test_idx = _stratified_group_split(
            labels, group_ids, train_frac=train_frac, val_frac=val_frac, seed=seed
        )

        def _subset(indices):
            return [graphs[i] for i in indices], [labels[i] for i in indices], [group_ids[i] for i in indices]

        train_g, train_l, train_gids = _subset(train_idx)
        val_g, val_l, val_gids = _subset(val_idx)
        test_g, test_l, test_gids = _subset(test_idx)

        # Assert no group leaks across splits
        train_set = set(train_gids)
        val_set   = set(val_gids)
        test_set  = set(test_gids)
        overlap_train_val  = train_set & val_set
        overlap_train_test = train_set & test_set
        overlap_val_test   = val_set & test_set
        if overlap_train_val or overlap_train_test or overlap_val_test:
            raise AssertionError(
                f"Split group overlap detected! train∩val={overlap_train_val}, "
                f"train∩test={overlap_train_test}, val∩test={overlap_val_test}"
            )

        return cls(
            train_graphs=train_g,
            val_graphs=val_g,
            test_graphs=test_g,
            train_labels=train_l,
            val_labels=val_l,
            test_labels=test_l,
            _train_group_ids=train_gids,
            _val_group_ids=val_gids,
            _test_group_ids=test_gids,
        )

    def __len__(self) -> int:
        return len(self.train_graphs) + len(self.val_graphs) + len(self.test_graphs)

    def class_distribution(self) -> dict:
        """Return the class distribution across splits."""
        def _count(labels):
            pos = sum(1 for l in labels if l == 1.0)
            neg = sum(1 for l in labels if l == 0.0)
            return {"positive": pos, "negative": neg, "total": len(labels)}

        return {
            "train": _count(self.train_labels),
            "val": _count(self.val_labels),
            "test": _count(self.test_labels),
        }


def create_dataloaders(
    dataset: DetectorDataset,
    batch_size: int = 16,
    num_workers: int = 0,
) -> Tuple[PyGDataLoader, PyGDataLoader, PyGDataLoader]:
    """Create PyG DataLoaders from a DetectorDataset.

    For static GNN training (StaticGraphData inherits PyG Data).

    Args:
        dataset:      DetectorDataset with train/val/test splits.
        batch_size:   Batch size for all loaders.
        num_workers:  Number of DataLoader workers (default 0).

    Returns:
        (train_loader, val_loader, test_loader)
    """
    from torch_geometric.data import Batch

    def _collate_fn(batch):
        """Collate StaticGraphData objects into a PyG Batch."""
        return Batch.from_data_list(batch)

    train_loader = PyGDataLoader(
        dataset.train_graphs,
        batch_size=batch_size,
        shuffle=True,
        num_workers=num_workers,
        collate_fn=_collate_fn,
    )
    val_loader = PyGDataLoader(
        dataset.val_graphs,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        collate_fn=_collate_fn,
    )
    test_loader = PyGDataLoader(
        dataset.test_graphs,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        collate_fn=_collate_fn,
    )

    return train_loader, val_loader, test_loader


# ── Execution-level helpers ─────────────────────────────────────────────────────


def select_final_snapshots_per_execution(
    snapshots: List[Any],
    labels: List[float],
    metadata: Optional[List[Dict[str, Any]]] = None,
) -> tuple[List[Any], List[float], List[str]]:
    """Select exactly one final/full-run snapshot per execution from a list.

    The snapshot builder guarantees the final event is included in the last
    snapshot.  This function picks that last snapshot for each execution,
    collapsing multiple snapshots per execution down to a single one.

    Parent execution identity is carried via ``snapshot.execution_id`` (set
    by ``TemporalSnapshotBuilder._build_snapshot``) or from ``metadata[i]
    ["execution_id"]``.

    Args:
        snapshots: List of StaticGraphData or TemporalGraphData objects,
                   one per snapshot.  Order must match ``labels`` and
                   ``metadata``.
        labels:    Float labels [len(snapshots)].
        metadata:  Optional list of metadata dicts (one per snapshot) with
                   an ``execution_id`` key.  If None, uses
                   ``snapshots[i].execution_id``.

    Returns:
        (final_snapshots, final_labels, execution_ids) — one entry per
        distinct execution, where each entry is the last snapshot for that
        execution.
    """
    if len(snapshots) != len(labels):
        raise ValueError(
            f"Length mismatch: snapshots={len(snapshots)}, labels={len(labels)}"
        )

    if metadata is not None and len(metadata) != len(snapshots):
        raise ValueError("metadata and snapshots must have equal lengths")

    # Group snapshots by execution_id
    exec_to_indices: Dict[str, List[int]] = defaultdict(list)
    for i in range(len(snapshots)):
        eid = (
            metadata[i]["execution_id"]
            if metadata is not None
            else getattr(snapshots[i], "execution_id", None)
        )
        if not eid:
            raise ValueError("Each snapshot must have an execution_id")
        exec_to_indices[eid].append(i)

    # Select the last snapshot per execution
    final_snapshots: List[Any] = []
    final_labels: List[float] = []
    final_exec_ids: List[str] = []

    for eid in exec_to_indices:
        indices = exec_to_indices[eid]
        last_idx = indices[-1]  # final snapshot (builder guarantees this has all events)
        final_snapshots.append(snapshots[last_idx])
        final_labels.append(labels[last_idx])
        final_exec_ids.append(eid)

    return final_snapshots, final_labels, final_exec_ids


# ── Final-snapshot dataset ──────────────────────────────────────────────────────


@dataclass
class FinalSnapshotDataset:
    """A dataset of one final/full-run snapshot per execution.

    Used for static GNN validation and test headline evaluation.  Carries
    snapshot_metadata so that ``DetectorTrainer.evaluate()`` can aggregate
    predictions at the execution level.

    Attributes:
        graphs:            List of StaticGraphData objects (one per execution).
        labels:            Float labels [len(graphs)].
        snapshot_metadata: Metadata dicts (one per graph) with at least
                           ``execution_id``.
        execution_ids:     Ordered execution ID list.
    """

    graphs: List[Any]
    labels: List[float]
    snapshot_metadata: List[Dict[str, Any]] = field(default_factory=list)
    execution_ids: List[str] = field(default_factory=list)

    def __post_init__(self):
        if len(self.graphs) != len(self.labels):
            raise ValueError("graphs and labels must have equal lengths")
        if not self.execution_ids:
            self.execution_ids = (
                [m["execution_id"] for m in self.snapshot_metadata]
                if self.snapshot_metadata else [g.execution_id for g in self.graphs]
            )
        if not self.snapshot_metadata:
            self.snapshot_metadata = [{"execution_id": eid} for eid in self.execution_ids]
        if not (len(self.graphs) == len(self.execution_ids) == len(self.snapshot_metadata)):
            raise ValueError("graphs, execution_ids and metadata must have equal lengths")
        if len(set(self.execution_ids)) != len(self.execution_ids):
            raise ValueError("FinalSnapshotDataset requires one graph per execution")
        if [m["execution_id"] for m in self.snapshot_metadata] != self.execution_ids:
            raise ValueError("metadata and execution_ids must agree")

    def __len__(self) -> int:
        return len(self.graphs)


def split_at_execution_level(
    execution_ids: List[str],
    labels: List[float],
    train_frac: float = 0.7,
    val_frac: float = 0.15,
    seed: int = 42,
    group_ids: Optional[List[str]] = None,
) -> tuple[set, set, set]:
    """Split distinct execution IDs into train/val/test groups.

    Optional group_ids keep related task-instance executions together.

    This is a pure set-level split: it returns disjoint sets of
    execution IDs for each split, guaranteeing that no execution appears
    in more than one split.

    Unlike ``_stratified_group_split``, this function returns the raw
    execution ID sets, not graph indices.  Callers are responsible for
    deriving per-split indices.

    Args:
        execution_ids: List of execution ID strings (one per graph).
        labels:       Float labels [len(execution_ids)].
        train_frac:   Fraction for training (default 0.7).
        val_frac:     Fraction for validation (default 0.15).
        seed:         Random seed for reproducibility.

    Returns:
        (train_eids, val_eids, test_eids) — disjoint sets of execution IDs.
    """
    import random

    rng = random.Random(seed)

    # Deduplicate execution IDs
    unique_eids = list(dict.fromkeys(execution_ids))  # preserves insertion order
    if group_ids is not None:
        if len(group_ids) != len(execution_ids):
            raise ValueError("group_ids and execution_ids must have equal lengths")
        eid_groups = {}
        for eid, gid in zip(execution_ids, group_ids):
            if eid in eid_groups and eid_groups[eid] != gid:
                raise ValueError("An execution cannot belong to multiple task groups")
            eid_groups[eid] = gid
        unique_eids = list(dict.fromkeys(group_ids))
    rng.shuffle(unique_eids)

    n = len(unique_eids)
    if len(execution_ids) != len(labels):
        raise ValueError("execution_ids and labels must have equal lengths")
    if not (0 < train_frac < 1 and 0 < val_frac < 1 and train_frac + val_frac < 1):
        raise ValueError("train/val fractions must leave room for all three splits")
    if n < 3:
        raise ValueError("Need at least 3 executions for train/val/test splits")
    n_train = min(max(1, int(n * train_frac)), n - 2)
    n_val = min(max(1, int(n * val_frac)), n - n_train - 1)

    train_eids = set(unique_eids[:n_train])
    val_eids = set(unique_eids[n_train:n_train + n_val])
    test_eids = set(unique_eids[n_train + n_val:])

    if group_ids is not None:
        train_eids, val_eids, test_eids = (
            {eid for eid, gid in eid_groups.items() if gid in split}
            for split in (train_eids, val_eids, test_eids)
        )

    # Assert non-empty
    assert len(train_eids) > 0, "train split has no executions"
    assert len(val_eids) > 0, "val split has no executions"
    assert len(test_eids) > 0, "test split has no executions"

    # Assert disjoint
    assert train_eids.isdisjoint(val_eids), "train/val execution overlap"
    assert train_eids.isdisjoint(test_eids), "train/test execution overlap"
    assert val_eids.isdisjoint(test_eids), "val/test execution overlap"

    return train_eids, val_eids, test_eids
