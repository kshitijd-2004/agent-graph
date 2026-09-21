"""Dataset loading and splitting for detector training.

Provides:
- DetectorDataset: Wraps StaticGraphData / TemporalGraphData lists with
  train/val/test splits grouped by execution_id.
- create_dataloaders: Returns PyG DataLoaders for static GNN training.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

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
    """Split graph indices into train/val/test, stratified by label and grouped
    by task-instance group_id.

    All graphs sharing the same group_id stay in the same split to
    prevent data leakage.  A group_id identifies a single task fixture /
    instance — e.g. ``("code_review", "review_loop", "LEP_HANDOFF_CORRUPTION")`` —
    so benign and perturbed variants of the same underlying task land
    together.

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

    # Label each group by the max label in that group
    group_labels = {}
    for gid, idxs in group_to_indices.items():
        group_labels[gid] = max(graph_labels[i] for i in idxs)

    benign_groups = [g for g, lbl in group_labels.items() if lbl == 0.0]
    mal_groups = [g for g, lbl in group_labels.items() if lbl == 1.0]

    rng.shuffle(benign_groups)
    rng.shuffle(mal_groups)

    def _split(groups, n_train, n_val):
        return groups[:n_train], groups[n_train:n_train + n_val], groups[n_train + n_val:]

    # Proportional split
    n_train_b = max(1, int(len(benign_groups) * train_frac)) if benign_groups else 0
    n_val_b   = max(1, int(len(benign_groups) * val_frac)) if benign_groups else 0
    tr_b, va_b, te_b = _split(benign_groups, n_train_b, n_val_b)

    n_train_m = max(1, int(len(mal_groups) * train_frac)) if mal_groups else 0
    n_val_m   = max(1, int(len(mal_groups) * val_frac)) if mal_groups else 0
    tr_m, va_m, te_m = _split(mal_groups, n_train_m, n_val_m)

    train_gids = tr_b + tr_m
    val_gids   = va_b + va_m
    test_gids  = te_b + te_m

    train_idx = [i for gid in train_gids for i in group_to_indices[gid]]
    val_idx   = [i for gid in val_gids   for i in group_to_indices[gid]]
    test_idx  = [i for gid in test_gids  for i in group_to_indices[gid]]

    logger.info(
        "Split %d graphs: train=%d (groups=%d), val=%d (groups=%d), test=%d (groups=%d)",
        n, len(train_idx), len(train_gids),
        len(val_idx), len(val_gids),
        len(test_idx), len(test_gids),
    )
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

    For static GNN training (StaticGraphData with PyG tensors).

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
