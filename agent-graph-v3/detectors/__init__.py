"""Detectors package — learned and heuristic LEP detection models.

Provides:
- StaticGNN: PyG-based graph neural network for snapshot-level detection
- TemporalGNN: DyGLib-compatible temporal GNN for event-stream detection
- HybridDetector: Combines TGNN + interpretable heuristic signals
- HeuristicDetector: Rule-based baseline (degree-based, temporal, random)

All learned detectors operate on detector-visible features only (24-dim
node features with perturbation flags stripped).
"""

from detectors.static_gnn import DetectionOutput, StaticGNN
from detectors.tgnn import TemporalDetectionOutput, TemporalGNN
from detectors.hybrid import HybridDetectionOutput, HeuristicSignalExtractor, HybridDetector
from generation.heuristic_detector import DetectionResult, HeuristicDetector

__all__ = [
    # Learned detectors
    "StaticGNN",
    "TemporalGNN",
    "HybridDetector",
    # Heuristic baseline
    "HeuristicDetector",
    # Output types
    "DetectionOutput",
    "TemporalDetectionOutput",
    "HybridDetectionOutput",
    "DetectionResult",
    # Heuristic components
    "HeuristicSignalExtractor",
]
