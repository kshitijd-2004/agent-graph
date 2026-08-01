"""Export utilities for saving graphs in various formats.

Supports:
- DyGLib CSV format (for TGN, JODIE, TGAT training)
- PyTorch format (.pt) for serialization
- JSON format for inspection/debugging
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

import torch

from agentgraph.encoder import StaticGraphData, TemporalGraphData
from agentgraph.graph_builder import EntityGraph


class ExportManager:
    """Manage export of graphs to various formats.

    Handles saving to disk, batch exports, and format conversion.
    """

    def __init__(self, output_dir: Path) -> None:
        """Initialize export manager.

        Args:
            output_dir: Root directory for all exports
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_dyglib_csv(
        self,
        graphs: List[EntityGraph],
        filename: str = "data.csv",
    ) -> Path:
        """Export graphs to DyGLib-compatible CSV format.

        The DyGLib format expects columns:
            u,v,ts,label,idx

        Where:
            u,v    = source/target node IDs
            ts     = normalized timestamp
            label  = graph-level label (0=benign, 1=malignant)
            idx    = graph index (0 for single-graph export)

        For multi-graph datasets, use export_dyglib_dataset() instead.

        Args:
            graphs: List of EntityGraph objects
            filename: Output filename

        Returns:
            Path to the written CSV file
        """
        output_path = self.output_dir / filename
        rows = []

        for graph in graphs:
            label = 1 if graph.variant == "b" else 0
            for i in range(graph.num_edges):
                u = graph.edge_index[0, i].item()
                v = graph.edge_index[1, i].item()
                ts = graph.edge_timestamps[i].item() if graph.num_edges > 0 else 0.0
                rows.append({
                    "u": u,
                    "v": v,
                    "ts": round(ts, 6),
                    "label": label,
                    "idx": 0,  # Single graph, so all events share index 0
                })

        with open(output_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["u", "v", "ts", "label", "idx"],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)

        return output_path

    def export_dyglib_dataset(
        self,
        graphs: List[EntityGraph],
        base_filename: str = "dyglib_dataset",
    ) -> Path:
        """Export a batch of graphs to DyGLib-compatible format.

        For paired detection tasks, creates a single CSV where:
        - Events from graph i have idx=i
        - Benign traces get label=0, malignant get label=1

        Args:
            graphs: List of EntityGraph objects (can be mixed benign/malignant)
            base_filename: Base name for output files

        Returns:
            Path to the main CSV file
        """
        csv_path = self.output_dir / f"{base_filename}.csv"

        rows = []
        for graph_idx, graph in enumerate(graphs):
            label = 1 if graph.variant == "b" else 0
            for i in range(graph.num_edges):
                u = graph.edge_index[0, i].item()
                v = graph.edge_index[1, i].item()
                ts = graph.edge_timestamps[i].item() if graph.num_edges > 0 else 0.0
                rows.append({
                    "u": u,
                    "v": v,
                    "ts": round(ts, 6),
                    "label": label,
                    "idx": graph_idx,
                })

        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["u", "v", "ts", "label", "idx"],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)

        # Write edge features as a separate numpy file
        edge_features_dir = self.output_dir / f"{base_filename}_edge_features"
        edge_features_dir.mkdir(exist_ok=True)

        import numpy as np
        for graph_idx, graph in enumerate(graphs):
            if graph.num_edges > 0:
                features_np = graph.edge_attr.numpy()
                np.save(
                    edge_features_dir / f"edge_features_{graph_idx}.npy",
                    features_np,
                )
            else:
                # Empty features array
                import numpy as np
                np.save(
                    edge_features_dir / f"edge_features_{graph_idx}.npy",
                    np.zeros((0, graph.edge_attr.shape[1]), dtype=np.float32),
                )

        # Write node features
        node_features_dir = self.output_dir / f"{base_filename}_node_features"
        node_features_dir.mkdir(exist_ok=True)

        for graph_idx, graph in enumerate(graphs):
            features = self._compute_node_features(graph)
            import numpy as np
            np.save(
                node_features_dir / f"node_features_{graph_idx}.npy",
                features.numpy(),
            )

        # Write graph statistics
        stats_path = self.output_dir / f"{base_filename}_stats.json"
        stats = {
            "num_graphs": len(graphs),
            "total_edges": sum(g.num_edges for g in graphs),
            "total_nodes": sum(g.num_nodes for g in graphs),
            "edge_feature_dim": graphs[0].edge_attr.shape[1] if graphs and graphs[0].num_edges > 0 else 0,
            "graphs": [
                {
                    "idx": i,
                    "trace_id": g.trace_id,
                    "execution_id": g.execution_id,
                    "variant": g.variant,
                    "num_nodes": g.num_nodes,
                    "num_edges": g.num_edges,
                    "label": 1 if g.variant == "b" else 0,
                }
                for i, g in enumerate(graphs)
            ],
        }
        with open(stats_path, "w", encoding="utf-8") as fh:
            json.dump(stats, fh, indent=2)

        return csv_path

    def save_torch(
        self,
        data_list: List[StaticGraphData],
        filename: str = "graphs.pt",
    ) -> Path:
        """Save static graph data as a PyTorch file.

        Args:
            data_list: List of StaticGraphData objects
            filename: Output filename

        Returns:
            Path to the saved file
        """
        output_path = self.output_dir / filename
        torch.save(data_list, output_path)
        return output_path

    def load_torch(self, filename: str = "graphs.pt") -> List[StaticGraphData]:
        """Load static graph data from a PyTorch file.

        Args:
            filename: Input filename

        Returns:
            List of StaticGraphData objects
        """
        output_path = self.output_dir / filename
        if not output_path.exists():
            raise FileNotFoundError(f"File not found: {output_path}")
        return torch.load(output_path, weights_only=False)

    def save_json(
        self,
        graphs: List[EntityGraph],
        filename: str = "graphs.json",
    ) -> Path:
        """Save graph metadata as JSON (for inspection/debugging).

        Does NOT include tensors — only structural metadata.

        Args:
            graphs: List of EntityGraph objects
            filename: Output filename

        Returns:
            Path to the saved file
        """
        output_path = self.output_dir / filename
        data = []
        for g in graphs:
            data.append({
                "trace_id": g.trace_id,
                "execution_id": g.execution_id,
                "variant": g.variant,
                "num_nodes": g.num_nodes,
                "num_edges": g.num_edges,
                "nodes": [n.to_dict() for n in g.nodes],
                "edge_index_shape": list(g.edge_index.shape),
                "edge_attr_shape": list(g.edge_attr.shape),
                "edge_timestamps_shape": list(g.edge_timestamps.shape),
            })
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        return output_path

    def _compute_node_features(self, graph: EntityGraph) -> torch.Tensor:
        """Compute node features (same logic as GraphEncoder)."""
        num_nodes = len(graph.nodes)
        features = []

        in_degrees = torch.zeros(num_nodes, dtype=torch.float)
        if graph.num_edges > 0:
            for target_idx in graph.edge_index[1]:
                in_degrees[target_idx] += 1.0
        max_degree = max(in_degrees.max().item(), 1.0)
        in_degrees = in_degrees / max_degree

        for i, node in enumerate(graph.nodes):
            type_vec = node.to_feature_vector(5)
            degree = [in_degrees[i].item()]
            features.append(type_vec + degree)

        return torch.tensor(features, dtype=torch.float)
