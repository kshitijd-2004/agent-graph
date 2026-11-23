# Dev / held-out partition report

Partition frozen under seed `agentprop-v7-partition-20261123`, fraction 0.4 held-out. Split unit: **fixture**.

## Achieved sizes

### dev

- fixtures: 169
- sample units: 176 (kinds: {'benign': 74, 'lep': 102})
- pairs: 93 (classes: {'frozen_v6_disagreement': 18, 'missing_dimension': 12, 'zero_threshold_positive': 25, 'nonzero_threshold_positive': 18, 'clean_negative': 20})
- component A units: 109, B units: 67
- families: {'code_review': 52, 'financial_analysis': 64, 'research_synthesis': 60}
- topologies: {'coordinator_workers': 76, 'branch_and_verify': 55, 'review_loop': 45}
- LEPs (LEP units only): {'INDIRECT_PROMPT_INJECTION': 36, 'INPUT_DISREGARD': 28, 'TOOL_RESULT_CORRUPTION': 20, 'HANDOFF_CORRUPTION': 18}
- propagation modes: {'single_origin': 128, 'many_to_one': 23, 'one_to_many': 25}
- zero-threshold pair-dimension positives: 67
- pair frozen-positives / v6-positives: 0 / 0

### heldout

- fixtures: 112
- sample units: 124 (kinds: {'lep': 78, 'benign': 46})
- pairs: 57 (classes: {'frozen_v6_disagreement': 12, 'missing_dimension': 18, 'zero_threshold_positive': 5, 'nonzero_threshold_positive': 12, 'clean_negative': 10})
- component A units: 71, B units: 53
- families: {'code_review': 48, 'financial_analysis': 36, 'research_synthesis': 40}
- topologies: {'coordinator_workers': 44, 'branch_and_verify': 44, 'review_loop': 36}
- LEPs (LEP units only): {'TOOL_RESULT_CORRUPTION': 16, 'INPUT_DISREGARD': 22, 'HANDOFF_CORRUPTION': 14, 'INDIRECT_PROMPT_INJECTION': 26}
- propagation modes: {'one_to_many': 16, 'single_origin': 94, 'many_to_one': 14}
- zero-threshold pair-dimension positives: 36
- pair frozen-positives / v6-positives: 0 / 0

## Weighting note

The partition does not change the sampling design.  Component-A units keep their original `inclusion_probability` and `design_weight`.  A design-weighted estimate **within a partition** is a conditional estimate over the partition; it is not a population estimate over the full frame unless re-weighted by the partition-level inclusion probability.  Both are reported where used.
