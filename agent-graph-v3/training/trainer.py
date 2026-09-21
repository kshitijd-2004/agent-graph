"""Training loop and trainer for LEP detection models.

Provides:
- DetectorTrainer: Generic training loop for all detector types with
  PyG DataLoader (static GNN) or custom batching (TGNN/hybrid).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from torch_geometric.loader import DataLoader as PyGDataLoader

from training.metrics import compute_classification_metrics, compute_auroc

logger = logging.getLogger(__name__)


def _collate_static_with_labels(batch):
    """Collate (StaticGraphData, label) tuples into (PyG Batch, label_tensor)."""
    from torch_geometric.data import Batch
    graphs = [item[0] for item in batch]
    batch_labels = [item[1] for item in batch]
    batched = Batch.from_data_list(graphs)
    return batched, torch.tensor(batch_labels, dtype=torch.float)


def _collate_temporal(batch: List[Tuple]) -> Tuple:
    """Collate temporal graph samples into padded tensors.

    Each sample is a tuple of:
    (node_features, edges_u, edges_v, timestamps, num_nodes, label)

    Returns padded tensors with a mask.
    """
    node_features_list, edges_u_list, edges_v_list, timestamps_list = [], [], [], []
    labels_list = []
    num_nodes_list = []
    num_events_list = []

    for nf, eu, ev, ts, nn, lbl in batch:
        node_features_list.append(nf)
        edges_u_list.append(eu)
        edges_v_list.append(ev)
        timestamps_list.append(ts)
        labels_list.append(lbl)
        num_nodes_list.append(nn)
        num_events_list.append(len(ts))

    max_events = max(num_events_list) if num_events_list else 0
    max_nodes = max(num_nodes_list) if num_nodes_list else 0

    # Pad node features
    padded_nf = torch.zeros(len(batch), max_nodes, nf.size(-1))
    for i, nf in enumerate(node_features_list):
        padded_nf[i, :nf.size(0)] = nf

    # Pad event tensors
    feature_dim = node_features_list[0].size(-1) if node_features_list else 24
    padded_eu = torch.zeros(len(batch), max_events, dtype=torch.long)
    padded_ev = torch.zeros(len(batch), max_events, dtype=torch.long)
    padded_ts = torch.zeros(len(batch), max_events)
    event_mask = torch.zeros(len(batch), max_events, dtype=torch.bool)
    labels = torch.tensor(labels_list, dtype=torch.float)

    for i, (eu, ev, ts, ne) in enumerate(
        zip(edges_u_list, edges_v_list, timestamps_list, num_events_list)
    ):
        if ne > 0:
            padded_eu[i, :ne] = eu
            padded_ev[i, :ne] = ev
            padded_ts[i, :ne] = ts
            event_mask[i, :ne] = True

    return (
        padded_nf,       # [B, max_nodes, node_feat_dim]
        padded_eu,       # [B, max_events]
        padded_ev,       # [B, max_events]
        padded_ts,       # [B, max_events]
        event_mask,      # [B, max_events]
        labels,          # [B]
    )


class DetectorTrainer:
    """Train and evaluate LEP detection models.

    Handles training for all detector types:
    - StaticGNN: uses PyG DataLoader with standard batching
    - TemporalGNN: uses custom padded batching for variable-length event streams
    - HybridDetector: same as TemporalGNN (TGNN backbone)

    Args:
        model:           PyTorch model (StaticGNN, TemporalGNN, or HybridDetector).
        model_type:      One of ``"static"``, ``"temporal"``, ``"hybrid"``.
        device:          Device string (``"cpu"``, ``"cuda"``, ``"mps"``).
        lr:              Learning rate (default 1e-3).
        weight_decay:    Weight decay for AdamW (default 1e-4).
        checkpoint_dir:  Directory for saving checkpoints (default None).
    """

    def __init__(
        self,
        model: nn.Module,
        model_type: str = "static",
        device: str = "auto",
        lr: float = 1e-3,
        weight_decay: float = 1e-4,
        checkpoint_dir: Optional[Path] = None,
    ) -> None:
        self.model = model
        self.model_type = model_type
        self.lr = lr
        self.weight_decay = weight_decay

        # Device selection
        if device == "auto":
            self.device = torch.device(
                "cuda" if torch.cuda.is_available() else "cpu"
            )
        else:
            self.device = torch.device(device)

        self.model.to(self.device)
        self.optimizer = torch.optim.AdamW(
            model.parameters(), lr=lr, weight_decay=weight_decay
        )
        self.criterion = nn.BCEWithLogitsLoss()

        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        if self.checkpoint_dir:
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

        self.best_val_auroc = 0.0
        self.best_epoch = 0
        self.history: List[Dict[str, float]] = []

        logger.info(
            "Trainer initialized: model=%s, device=%s, lr=%.2e",
            model_type, self.device, lr,
        )

    def _get_temporal_batch(
        self,
        graphs: List,
        labels: List[float],
    ) -> Tuple:
        """Create a batch from temporal graph samples.

        Each element in graphs is a TemporalGraphData object.
        Returns padded tensors for the model.
        """
        samples = []
        for g, lbl in zip(graphs, labels):
            samples.append((
                g.node_features,
                g.edges_u,
                g.edges_v,
                g.edge_timestamps,
                g.num_nodes,
                torch.tensor(lbl, dtype=torch.float),
            ))
        return _collate_temporal(samples)

    def train_epoch(
        self,
        graphs: List,
        labels: List[float],
        batch_size: int = 8,
    ) -> float:
        """Train for one epoch.

        Args:
            graphs:      List of StaticGraphData or TemporalGraphData objects.
            labels:      Float labels [len(graphs)].
            batch_size:  Batch size.

        Returns:
            Average training loss.
        """
        self.model.train()
        total_loss = 0.0
        num_batches = 0

        if self.model_type == "static":
            # StaticGraphData is a PyG-compatible Data object with .x, .edge_index, .y
            loader = PyGDataLoader(graphs, batch_size=batch_size, shuffle=True)
            for batch_graphs in loader:
                batch_graphs = batch_graphs.to(self.device)
                self.optimizer.zero_grad()

                output = self.model(batch_graphs.x, batch_graphs.edge_index, batch_graphs.batch)
                logits = output.logits
                loss = self.criterion(logits, batch_graphs.y.squeeze())
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                self.optimizer.step()

                total_loss += loss.item()
                num_batches += 1
        else:
            # Temporal/hybrid: iterate over individual graphs
            indices = list(range(len(graphs)))
            np.random.shuffle(indices)

            for start in range(0, len(indices), batch_size):
                batch_idx = indices[start:start + batch_size]
                batch_graphs = [graphs[i] for i in batch_idx]
                batch_labels = [labels[i] for i in batch_idx]

                self.optimizer.zero_grad()

                # Process each graph individually (batching for temporal is complex)
                batch_loss = 0.0
                for g, lbl in zip(batch_graphs, batch_labels):
                    node_features = g.node_features.to(self.device)
                    edges_u = g.edges_u.to(self.device)
                    edges_v = g.edges_v.to(self.device)
                    timestamps = g.edge_timestamps.to(self.device)
                    label = torch.tensor(lbl, dtype=torch.float, device=self.device)

                    output = self.model(
                        node_features, edges_u, edges_v, timestamps, g.num_nodes
                    )
                    # Aggregate per-event risk to graph-level prediction
                    if hasattr(output, "risk_scores"):
                        graph_logit = output.risk_scores.mean()
                    else:
                        graph_logit = output.logits.mean()

                    loss = self.criterion(graph_logit.unsqueeze(0), label.unsqueeze(0))
                    batch_loss += loss

                batch_loss = batch_loss / len(batch_idx)
                batch_loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                self.optimizer.step()

                total_loss += batch_loss.item()
                num_batches += 1

        return total_loss / max(num_batches, 1)

    def evaluate(
        self,
        graphs: List,
        labels: List[float],
        batch_size: int = 16,
    ) -> Dict[str, Any]:
        """Evaluate the model on a dataset.

        Args:
            graphs:      List of StaticGraphData or TemporalGraphData objects.
            labels:      Float labels [len(graphs)].
            batch_size:  Batch size (for static GNN only).

        Returns:
            Dict with loss, metrics, predictions, and labels.
        """
        self.model.eval()
        total_loss = 0.0
        num_batches = 0
        all_preds = []
        all_labels = []

        with torch.no_grad():
            if self.model_type == "static":
                # StaticGraphData has .x, .edge_index, .y — PyG DataLoader can batch it
                loader = PyGDataLoader(graphs, batch_size=batch_size, shuffle=False)
                for batch_graphs in loader:
                    batch_graphs = batch_graphs.to(self.device)
                    output = self.model(batch_graphs.x, batch_graphs.edge_index, batch_graphs.batch)
                    logits = output.logits
                    batch_y = batch_graphs.y.squeeze()
                    loss = self.criterion(logits, batch_y)
                    total_loss += loss.item()
                    num_batches += 1

                    all_preds.extend(output.probabilities.cpu().numpy().tolist())
                    all_labels.extend(batch_y.cpu().numpy().tolist())
            else:
                for g, lbl in zip(graphs, labels):
                    node_features = g.node_features.to(self.device)
                    edges_u = g.edges_u.to(self.device)
                    edges_v = g.edges_v.to(self.device)
                    timestamps = g.edge_timestamps.to(self.device)
                    label = torch.tensor(lbl, dtype=torch.float, device=self.device)

                    output = self.model(
                        node_features, edges_u, edges_v, timestamps, g.num_nodes
                    )

                    if hasattr(output, "risk_scores"):
                        graph_pred = float(output.risk_scores.mean().item())
                    else:
                        graph_pred = float(output.probabilities.mean().item())

                    all_preds.append(graph_pred)
                    all_labels.append(lbl)

                    pred_tensor = torch.tensor(graph_pred, device=self.device)
                    loss = self.criterion(pred_tensor.unsqueeze(0), label.unsqueeze(0))
                    total_loss += loss.item()
                    num_batches += 1

        avg_loss = total_loss / max(num_batches, 1)
        all_preds = np.array(all_preds, dtype=float)
        all_labels = np.array(all_labels, dtype=float)

        metrics = compute_classification_metrics(all_labels, all_preds)
        metrics["loss"] = float(avg_loss)

        return {
            "loss": avg_loss,
            "metrics": metrics,
            "predictions": all_preds.tolist(),
            "labels": all_labels.tolist(),
        }

    def train(
        self,
        train_graphs: List,
        train_labels: List[float],
        val_graphs: List,
        val_labels: List[float],
        num_epochs: int = 100,
        batch_size: int = 16,
        patience: int = 10,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """Full training loop with early stopping.

        Args:
            train_graphs:  Training graphs.
            train_labels:  Training labels.
            val_graphs:    Validation graphs.
            val_labels:    Validation labels.
            num_epochs:    Maximum number of epochs.
            batch_size:    Batch size.
            patience:      Early stopping patience (epochs without improvement).
            verbose:       Print progress.

        Returns:
            Training history dict with per-epoch metrics.
        """
        logger.info(
            "Starting training: %d train, %d val, %d epochs, batch=%d",
            len(train_graphs), len(val_graphs), num_epochs, batch_size,
        )

        best_state = None
        patience_counter = 0

        for epoch in range(num_epochs):
            t0 = time.time()

            # Train
            train_loss = self.train_epoch(train_graphs, train_labels, batch_size=batch_size)

            # Validate
            val_results = self.evaluate(val_graphs, val_labels, batch_size=batch_size)
            val_auroc = val_results["metrics"]["auroc"]
            val_loss = val_results["loss"]

            elapsed = time.time() - t0

            epoch_info = {
                "epoch": epoch + 1,
                "train_loss": train_loss,
                "val_loss": val_loss,
                "val_auroc": val_auroc,
                "val_f1": val_results["metrics"]["f1"],
                "val_auprc": val_results["metrics"]["auprc"],
                "elapsed_s": elapsed,
            }
            self.history.append(epoch_info)

            if verbose and (epoch + 1) % max(1, num_epochs // 10) == 0:
                logger.info(
                    "Epoch %d/%d: train_loss=%.4f, val_loss=%.4f, val_auroc=%.4f, "
                    "val_f1=%.4f [%.1fs]",
                    epoch + 1, num_epochs, train_loss, val_loss,
                    val_auroc, val_results["metrics"]["f1"], elapsed,
                )

            # Early stopping on validation AUROC
            if val_auroc > self.best_val_auroc:
                self.best_val_auroc = val_auroc
                self.best_epoch = epoch
                patience_counter = 0
                best_state = {
                    "model_state_dict": self.model.state_dict(),
                    "optimizer_state_dict": self.optimizer.state_dict(),
                    "epoch": epoch,
                    "val_auroc": val_auroc,
                    "val_loss": val_loss,
                }

                if self.checkpoint_dir:
                    path = self.checkpoint_dir / "best_model.pt"
                    torch.save(best_state, path)
            else:
                patience_counter += 1
                if patience_counter >= patience:
                    logger.info(
                        "Early stopping at epoch %d (best val_auroc=%.4f at epoch %d)",
                        epoch + 1, self.best_val_auroc, self.best_epoch + 1,
                    )
                    break

        # Restore best model
        if best_state is not None:
            self.model.load_state_dict(best_state["model_state_dict"])
            logger.info(
                "Restored best model from epoch %d (val_auroc=%.4f)",
                self.best_epoch + 1, self.best_val_auroc,
            )

        return {
            "history": self.history,
            "best_val_auroc": self.best_val_auroc,
            "best_epoch": self.best_epoch,
            "total_epochs": len(self.history),
        }

    def save_checkpoint(self, path: Optional[Path] = None) -> None:
        """Save the current model state."""
        if path is None:
            path = self.checkpoint_dir / "checkpoint.pt" if self.checkpoint_dir else None
        if path is None:
            return
        torch.save({
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "best_val_auroc": self.best_val_auroc,
            "best_epoch": self.best_epoch,
            "history": self.history,
        }, path)
        logger.info("Checkpoint saved to %s", path)

    def load_checkpoint(self, path: Path) -> None:
        """Load a checkpoint."""
        ckpt = torch.load(path, map_location=self.device)
        self.model.load_state_dict(ckpt["model_state_dict"])
        self.optimizer.load_state_dict(ckpt["optimizer_state_dict"])
        self.best_val_auroc = ckpt.get("best_val_auroc", 0.0)
        self.best_epoch = ckpt.get("best_epoch", 0)
        self.history = ckpt.get("history", [])
        logger.info("Checkpoint loaded from %s (best_auroc=%.4f)", path, self.best_val_auroc)
