"""Final execution splits reject undefined detector evaluation deterministically."""
import numpy as np
import pytest
from training.dataset import split_at_execution_level
from training.metrics import compute_auroc


def paired_splits(labels=None):
    ids = [f"{i}-{j}" for i in range(10) for j in range(2)]
    groups = [str(i) for i in range(10) for _ in range(2)]
    labels = labels or [0, 1] * 10
    return ids, groups, labels, split_at_execution_level(ids, labels, group_ids=groups)


def test_execution_splits_both_classes_and_no_leakage():
    ids, groups, labels, splits = paired_splits()
    for split in splits:
        assert {labels[ids.index(eid)] for eid in split} == {0, 1}
    assert set.union(*splits) == set(ids)
    for i in range(3):
        for j in range(i):
            assert splits[i].isdisjoint(splits[j])
            assert {groups[ids.index(e)] for e in splits[i]}.isdisjoint(
                {groups[ids.index(e)] for e in splits[j]})


@pytest.mark.parametrize("index,name,label", [(0, "train", 0), (1, "val", 0), (2, "test", 1)])
def test_one_class_final_split_rejected(index, name, label):
    ids, groups, labels, splits = paired_splits()
    labels = [label if eid in splits[index] else value for eid, value in zip(ids, labels)]
    n = len(splits[index])
    with pytest.raises(ValueError, match=rf"Invalid {name} split: executions={n} positive={n * label} negative={n * (1-label)} groups="):
        split_at_execution_level(ids, labels, group_ids=groups)


@pytest.mark.parametrize("labels", [[], [0, 0], [1, 1]])
def test_undefined_auroc_is_nan(labels):
    assert np.isnan(compute_auroc(labels, [0.5] * len(labels)))
