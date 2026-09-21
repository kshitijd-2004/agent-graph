"""Training package for LEP detection models.

Provides:
- DetectorTrainer: Training loop for all detector types.
- DetectorDataset: Train/val/test split with DataLoader creation.
- DetectionEvaluator: Standard ML metrics and early-warning evaluation.
- BaselineComparator: Cross-detector comparison.
- DetectorPipeline: End-to-end training and evaluation.
"""

from training.dataset import DetectorDataset, create_dataloaders
from training.trainer import DetectorTrainer
from training.evaluator import DetectionEvaluator, BaselineComparator
from training.metrics import (
    aggregate_metrics,
    compute_aupr,
    compute_auroc,
    compute_classification_metrics,
    compute_lead_time,
    compute_lead_time_at_multiple_fpr,
)
from training.pipeline import DetectorPipeline, PipelineResult

__all__ = [
    "DetectorTrainer",
    "DetectorDataset",
    "create_dataloaders",
    "DetectionEvaluator",
    "BaselineComparator",
    "DetectorPipeline",
    "PipelineResult",
    "aggregate_metrics",
    "compute_aupr",
    "compute_auroc",
    "compute_classification_metrics",
    "compute_lead_time",
    "compute_lead_time_at_multiple_fpr",
]
