"""Final execution splits reject undefined detector evaluation deterministically."""
import random
from itertools import product

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


def make_grouped_data(compositions):
    ids, groups, labels = [], [], []
    for gid, (positive, negative) in enumerate(compositions):
        for label in [1] * positive + [0] * negative:
            ids.append(str(len(ids)))
            groups.append(str(gid))
            labels.append(label)
    return ids, groups, labels


def assert_valid(ids, groups, labels, splits):
    assert isinstance(splits, tuple)
    assert all(isinstance(split, set) for split in splits)
    assert set.union(*splits) == set(ids)
    label_by_id = dict(zip(ids, labels))
    group_by_id = dict(zip(ids, groups))
    for i, split in enumerate(splits):
        assert {label_by_id[eid] for eid in split} == {0, 1}
        for other in splits[:i]:
            assert split.isdisjoint(other)
            assert {group_by_id[eid] for eid in split}.isdisjoint(
                {group_by_id[eid] for eid in other})


def test_deterministic_and_duplicate_executions_counted_once():
    ids, groups, labels = make_grouped_data([(3, 1), (0, 4), (2, 0)] * 8)
    first = split_at_execution_level(ids, labels, group_ids=groups, seed=17)
    assert first == split_at_execution_level(ids, labels, group_ids=groups, seed=17)
    assert first == split_at_execution_level(
        ids + ids[:8], labels + labels[:8], group_ids=groups + groups[:8], seed=17)
    assert_valid(ids, groups, labels, first)


def test_regression_naive_validation_is_all_negative():
    shuffled = list(range(75))
    random.Random(42).shuffle(shuffled)
    # Old slicing puts these 11 groups (32 executions) in validation.
    old_val = shuffled[52:63]
    compositions = [(2, 1)] * 75
    for gid in old_val:
        compositions[gid] = (0, 3)
    compositions[old_val[0]] = (0, 2)
    ids, groups, labels = make_grouped_data(compositions)
    naive_labels = [label for gid, label in zip(groups, labels) if int(gid) in old_val]
    assert len(naive_labels) == 32
    assert sum(naive_labels) == 0
    splits = split_at_execution_level(ids, labels, group_ids=groups)
    assert_valid(ids, groups, labels, splits)


def test_targets_both_execution_class_counts_with_unequal_group_sizes():
    ids, groups, labels = make_grouped_data([(9, 1)] * 20 + [(1, 4)] * 20)
    splits = split_at_execution_level(ids, labels, group_ids=groups)
    assert_valid(ids, groups, labels, splits)
    by_id = dict(zip(ids, labels))
    for split, fraction in zip(splits, [0.7, 0.15, 0.15]):
        for label, total, tolerance in [(1, 200, 9), (0, 100, 4)]:
            assert abs(sum(by_id[eid] == label for eid in split) - total * fraction) <= tolerance


@pytest.mark.parametrize("compositions", [[], [(1, 1)] * 2,
    [(10, 0)] * 2 + [(0, 10)] * 5, [(0, 10)] * 2 + [(10, 0)] * 5,
    [(1, 1), (1, 0), (0, 1)], [(0, 3)] * 6])
def test_impossible_grouped_split(compositions):
    ids, groups, labels = make_grouped_data(compositions)
    with pytest.raises(ValueError, match="grouped dataset cannot support the requested split"):
        split_at_execution_level(ids, labels, group_ids=groups)


def test_all_small_group_type_combinations_against_exhaustive_feasibility():
    # Independently enumerate partitions, including feasible cases that need
    # separate pure-positive and pure-negative groups in the same split.
    for types in product([(1, 0), (0, 1), (1, 1)], repeat=5):
        feasible = any(all(
            all(any(types[g][label] for g in range(5) if assignment[g] == split)
                for label in range(2)) for split in range(3))
            for assignment in product(range(3), repeat=5))
        ids, groups, labels = make_grouped_data(types)
        for seed in (0, 42):
            if feasible:
                assert_valid(ids, groups, labels, split_at_execution_level(
                    ids, labels, group_ids=groups, seed=seed))
            else:
                with pytest.raises(ValueError, match="grouped dataset cannot support"):
                    split_at_execution_level(ids, labels, group_ids=groups, seed=seed)


@pytest.mark.parametrize("ids,labels,groups,message", [
    (["a"], [], ["g"], "equal lengths"),
    (["a"], [0], [], "equal lengths"),
    (["a"], [2], ["g"], "non-binary"),
    (["a", "a"], [0, 1], ["g", "g"], "inconsistent"),
    (["a", "a"], [0, 0], ["g", "h"], "multiple task groups"),
])
def test_invalid_execution_metadata(ids, labels, groups, message):
    with pytest.raises(ValueError, match=message):
        split_at_execution_level(ids, labels, group_ids=groups)


@pytest.mark.parametrize("labels", [[], [0, 0], [1, 1]])
def test_undefined_auroc_is_nan(labels):
    assert np.isnan(compute_auroc(labels, [0.5] * len(labels)))
