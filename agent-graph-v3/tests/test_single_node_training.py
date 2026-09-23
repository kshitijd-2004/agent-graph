"""Static training retains singleton snapshots without invalid BatchNorm stats."""
import pytest
import torch
from torch_geometric.data import Data

from detectors.static_gnn import StaticGNN
from generation.feature_schema import OBSERVABLE_NODE_FEATURE_DIM
from training.trainer import DetectorTrainer


@pytest.mark.parametrize("node_counts,batch_size,normal_batches", [
    ([1], 1, 0),
    ([1, 1, 1], 2, 1),  # Full batch followed by a singleton remainder.
    ([1, 1], 1, 0),
    ([2], 1, 1),  # One graph with multiple nodes uses training statistics.
])
def test_singleton_snapshot_training(node_counts, batch_size, normal_batches):
    torch.manual_seed(7)
    model = StaticGNN(node_feature_dim=OBSERVABLE_NODE_FEATURE_DIM,
                      hidden_dim=8, num_layers=2, dropout=0.)
    trainer = DetectorTrainer(model, model_type="static", device="cpu")
    graphs = [Data(x=torch.randn(count, OBSERVABLE_NODE_FEATURE_DIM),
                   edge_index=torch.empty((2, 0), dtype=torch.long),
                   y=torch.tensor([1.])) for count in node_counts]
    before = {name: value.detach().clone() for name, value in model.named_parameters()}
    seen = []
    hook = model.register_forward_hook(lambda module, args, output: seen.append(args[0].size(0)))
    try:
        loss = trainer.train_epoch(graphs, [1.] * len(graphs), batch_size=batch_size)
    finally:
        hook.remove()
    assert torch.isfinite(torch.tensor(loss))
    assert sum(seen) == sum(node_counts)  # No dropped or duplicated samples.
    assert model.training and all(bn.training for bn in model.bns)
    assert all(bn.num_batches_tracked.item() == normal_batches for bn in model.bns)
    assert all(torch.isfinite(p).all() for p in model.parameters())
    assert any(not torch.equal(before[name], value) for name, value in model.named_parameters())
    if normal_batches == 0:
        assert all(torch.equal(bn.running_mean, torch.zeros_like(bn.running_mean))
                   and torch.equal(bn.running_var, torch.ones_like(bn.running_var))
                   for bn in model.bns)


def test_singleton_restores_batchnorm_mode_after_forward_error(monkeypatch):
    model = StaticGNN(node_feature_dim=OBSERVABLE_NODE_FEATURE_DIM,
                      hidden_dim=8, num_layers=2)
    trainer = DetectorTrainer(model, model_type="static", device="cpu")
    graph = Data(x=torch.zeros(1, OBSERVABLE_NODE_FEATURE_DIM),
                 edge_index=torch.empty((2, 0), dtype=torch.long), y=torch.tensor([0.]))
    def fail(*args):
        assert all(not bn.training for bn in model.bns)
        raise RuntimeError("forward failed")
    monkeypatch.setattr(model, "forward", fail)
    with pytest.raises(RuntimeError, match="forward failed"):
        trainer.train_epoch([graph], [0.], batch_size=1)
    assert all(bn.training for bn in model.bns)
