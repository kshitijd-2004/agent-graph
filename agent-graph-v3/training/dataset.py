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
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import torch
from torch_geometric.loader import DataLoader as PyGDataLoader

from encoder import StaticGraphData, TemporalGraphData
from generation.feature_schema import OBSERVABLE_NODE_FEATURE_DIM

logger = logging.getLogger(__name__)


def _stratified_execution_split(
    graph_labels: List[float],
    execution_ids: List[str],
    train_frac: float = 0.7,
    val_frac: float = 0.15,
    seed: int = 42,
) -> Tuple[List[int], List[int], List[int]]:
    """Split graph indices into train/val/test, stratified by label and grouped
    by execution_id.

    All graphs sharing the same execution_id stay in the same split to
    prevent data leakage (prefixes from the same execution must not be
    split across train/val/test).

    Args:
        graph_labels:   List of float labels (1.0 = malignant, 0.0 = benign).
        execution_ids:  List of execution_id strings, one per graph.
        train_frac:     Fraction for training (default 0.7).
        val_frac:       Fraction for validation (default 0.15).
        seed:           Random seed for reproducibility.

    Returns:
        (train_indices, val_indices, test_indices) — each a list of
        integer indices into the input arrays.
    """
    rng = random.Random(seed)
    n = len(graph_labels)

    # Group indices by execution_id
    exec_to_indices: Dict[str, List[int]] = defaultdict(list)
    for i, eid in enumerate(execution_ids):
        exec_to_indices[eid].append(i)

    # Group execution_ids by their label (use the max label in each group)
    exec_labels = {}
    for eid, idxs in exec_to_indices.items():
        labels = [graph_labels[i] for i in idxs]
        exec_labels[eid] = max(labels)  # if any graph in exec is malignant, label as malignant

    # Stratify: separate benign vs malignant executions
    benign_execs = [eid for eid, lbl in exec_labels.items() if lbl == 0.0]
    mal_execs = [eid for eid, lbl in exec_labels.items() if lbl == 1.0]

    rng.shuffle(benign_execs)
    rng.shuffle(mal_execs)

    def _split_execs(execs, train_n, val_n):
        train = execs[:train_n]
        val = execs[train_n:train_n + val_n]
        test = execs[train_n + val_n:]
        return train, val, test

    # Proportional split
    total_execs = len(exec_labels)
    train_n_b = max(1, int(len(benign_execs) * train_frac)) if benign_execs else 0
    val_n_b = max(1, int(len(benign_execs) * val_frac)) if benign_execs else 0
    train_b, val_b, test_b = _split_execs(benign_execs, train_n_b, val_n_b)

    train_n_m = max(1, int(len(mal_execs) * train_frac)) if mal_execs else 0
    val_n_m = max(1, int(len(mal_execs) * val_frac)) if mal_execs else 0
    train_m, val_m, test_m = _split_execs(mal_execs, train_n_m, val_n_m)

    train_execs = train_b + train_m
    val_execs = val_b + val_m
    test_execs = test_b + test_m

    # Map back to graph indices
    train_idx = []
    val_idx = []
    test_idx = []
    for eid in train_execs:
        train_idx.extend(exec_to_indices[eid])
    for eid in val_execs:
        val_idx.extend(exec_to_indices[eid])
    for eid in test_execs:
        test_idx.extend(exec_to_indices[eid])

    logger.info(
        "Split %d graphs: train=%d (execs=%d), val=%d (execs=%d), test=%d (execs=%d)",
        n, len(train_idx), len(train_execs),
        len(val_idx), len(val_execs),
        len(test_idx), len(test_execs),
    )

    return train_idx, val_idx, test_idx


@dataclass
class DetectorDataset:
    """Dataset for detector training with train/val/test split.

    Wraps lists of StaticGraphData or TemporalGraphData objects and
    provides PyTorch DataLoaders. Splits are stratified by label and
    grouped by execution_id to prevent data leakage.

    Attributes:
        train_graphs:   Training graphs.
        val_graphs:     Validation graphs.
        test_graphs:    Test graphs.
        train_labels:   Training labels.
        val_labels:     Validation labels.
        test_labels:    Test labels.
    """

    train_graphs: List[Union[StaticGraphData, TemporalGraphData]]
    val_graphs: List[Union[StaticGraphData, TemporalGraphData]]
    test_graphs: List[Union[StaticGraphData, TemporalGraphData]]
    train_labels: List[float]
    val_labels: List[float]
    test_labels: List[float]

    @classmethod
    def from_encoded_graphs(
        cls,
        static_graphs: List[StaticGraphData],
        temporal_graphs: List[TemporalGraphData],
        labels: List[float],
        execution_ids: List[str],
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
            execution_ids:    List of execution_id strings.
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

        train_idx, val_idx, test_idx = _stratified_execution_split(
            labels, execution_ids, train_frac=train_frac, val_frac=val_frac, seed=seed
        )

        def _subset(indices):
            return [graphs[i] for i in indices], [labels[i] for i in indices]

        train_g, train_l = _subset(train_idx)
        val_g, val_l = _subset(val_idx)
        test_g, test_l = _subset(test_idx)

        return cls(
            train_graphs=train_g,
            val_graphs=val_g,
            test_graphs=test_g,
            train_labels=train_l,
            val_labels=val_l,
            test_labels=test_l,
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
