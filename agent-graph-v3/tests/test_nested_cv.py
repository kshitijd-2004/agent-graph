import json
import numpy as np
import pytest
from sklearn.metrics import f1_score, precision_score, recall_score
from training.cross_validation import NestedGroupCV, grouped_folds


def test_nested_selection_isolation_weights_and_pooled_decisions(tmp_path):
    # IDs intentionally bear no relationship to task-group names.
    ids = [f'execution-{i}' for i in range(45)]
    groups = [f'task-{i//3}' for i in range(45)]
    labels = [int(i % 3 == 0) for i in range(45)]
    lookup = dict(zip(ids, labels))
    group = dict(zip(ids, groups))
    fits, predictions = [], []

    def fit(train, y, weight, config):
        assert y == [lookup[e] for e in train]
        assert weight == y.count(0) / y.count(1)
        fits.append((set(train), config))
        return set(train), config

    def predict(model, held_out):
        train, config = model
        assert not train.intersection(held_out)
        assert not {group[e] for e in train}.intersection(group[e] for e in held_out)
        predictions.append(set(held_out))
        # Different fold score scales expose erroneous mean-threshold pooling.
        offset = (int(held_out[0].split('-')[1]) % 7) / 10
        return [offset + (0.02 if lookup[e] == config['direction'] else 0.01) for e in held_out]

    result = NestedGroupCV(inner_folds=4).run(ids, labels, groups, fit, predict,
                                             [{'direction': 0}, {'direction': 1}])
    assert len(result.fold_results) == 5
    assert len(fits) == 5 * (4 * 2 + 1)
    assert sorted(r['execution_id'] for r in result.outer_test_predictions) == sorted(ids)
    for i, fold in enumerate(result.fold_results):
        assert fold['selected_configuration'] == {'direction': 1}
        pool, test = set(fold['train_eids']), set(fold['test_eids'])
        assert {r['execution_id'] for r in fold['inner_oof_predictions']} == pool
        block = fits[i*9:(i+1)*9]
        assert block[-1][0] == pool
        assert all(not train.intersection(test) for train, _ in block)
    rows = result.outer_test_predictions
    y, decisions = [r['label'] for r in rows], [r['prediction'] for r in rows]
    for key, fn in [('f1', f1_score), ('precision', precision_score), ('recall', recall_score)]:
        assert result.pooled_metrics[key] == pytest.approx(fn(y, decisions))
    path = tmp_path / 'cv.json'
    result.save(path)
    assert len(json.loads(path.read_text())['outer_test_predictions']) == len(ids)


def test_reduce_and_refuse_infeasible(caplog):
    y = [1, 0] * 5
    groups = [f'g{i//2}' for i in range(10)]
    splits = grouped_folds(y, groups, 6, 42, reduce=True)
    assert len(splits) == 5
    assert 'Reducing inner folds from 6 to 5' in caplog.text
    for train, val in splits:
        assert set(np.array(y)[train]) == set(np.array(y)[val]) == {0, 1}
    with pytest.raises(ValueError):
        grouped_folds([1, 0], ['same', 'same'], 4, 42, reduce=True)
    with pytest.raises(ValueError):
        grouped_folds(y, groups, 6, 42)


def test_actual_inner_reduction_and_no_silent_missing_predictions(caplog):
    ids = list(map(str, range(10)))
    groups = [str(i//2) for i in range(10)]
    y = [1, 0] * 5
    result = NestedGroupCV(inner_folds=5).run(
        ids, y, groups, lambda *args: None, lambda _, ids: [0.4]*len(ids), [{}])
    assert all(f['inner_folds_used'] == 4 for f in result.fold_results)
    assert 'Reducing inner folds from 5 to 4' in caplog.text
    with pytest.raises(ValueError, match='one finite score'):
        NestedGroupCV().run(ids, y, groups, lambda *args: None, lambda *args: [], [{}])
    with pytest.raises(ValueError, match='one entry per execution'):
        NestedGroupCV().run(ids*2, y*2, groups*2, None, None, [{}])


def test_static_pipeline_fits_real_models_and_saves_oof(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import torch
    from torch_geometric.data import Data
    from training.pipeline import DetectorPipeline
    from generation.feature_schema import OBSERVABLE_NODE_FEATURE_DIM
    from generation.event_graph_snapshot import TemporalSnapshotBuilder
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    pipeline = DetectorPipeline(tmp_path, device='cpu')
    traces = [SimpleNamespace(execution_id=str(i), num_nodes=2,
                              metadata={'fixture_id': str(i//2), 'topology': 'test'})
              for i in range(10)]
    monkeypatch.setattr(pipeline, 'load_traces', lambda: (traces, []))
    monkeypatch.setattr(pipeline, 'build_anomaly_labels', lambda _: [0., 1.]*5)
    monkeypatch.setattr(pipeline.graph_builder, 'build', lambda trace, **kwargs: trace)
    monkeypatch.setattr(TemporalSnapshotBuilder, 'build_from_event_graph', lambda self, graph: [graph])

    def encode(graphs, labels):
        return ([Data(x=torch.ones(2, OBSERVABLE_NODE_FEATURE_DIM),
                      edge_index=torch.tensor([[0, 1], [1, 0]]), y=torch.tensor([label]))
                 for g, label in zip(graphs, labels)], [None]*len(graphs))

    monkeypatch.setattr(pipeline, 'encode_graphs', encode)
    try:
        result = pipeline.run_cross_validation(detector_types=['static_gnn'], num_epochs=1,
                                               use_heuristic_baselines=False)
    finally:
        torch.set_num_threads(previous_threads)
    assert len(result['static_gnn'].outer_test_predictions) == 10
    saved = json.loads((tmp_path/'detector_checkpoints/static_gnn_nested_cv.json').read_text())
    assert saved['outer_folds'] == 5


def test_threshold_is_exact_f1_maximum():
    from training.metrics import find_best_f1_threshold, compute_classification_metrics
    y = np.array([0, 1, 1, 0, 1])
    scores = np.array([.1, .11, .14, .14, .19])
    threshold = find_best_f1_threshold(y, scores)
    actual = compute_classification_metrics(y, scores, threshold)['f1']
    assert actual == pytest.approx(max(compute_classification_metrics(y, scores, t)['f1']
                                      for t in scores))
