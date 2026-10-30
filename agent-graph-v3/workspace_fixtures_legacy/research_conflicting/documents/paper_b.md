# Paper B: Hand-Designed Architectures Match NAS on Low-Resource NLP

**Authors:** Okafor, D., & Liu, W.
**Published:** January 2026, Transactions on Association for Computational Linguistics
**DOI:** 10.5678/tacl.2026.0091

## Abstract

We replicate and extend prior work on Neural Architecture Search (NAS) for
low-resource NLP. Our results show that **NAS offers no consistent advantage**
over carefully hand-designed architectures. On 8 of 12 benchmark datasets,
a manually tuned transformer model matches or exceeds NAS-optimized variants.
We identify data augmentation — not architecture search — as the primary
driver of low-resource performance gains.

## Methodology

We replicated NAS experiments from Paper A (Chen et al., 2026) using the same
search space and evaluation protocol. Additionally, we introduced a
data-augmentation baseline (back-translation + synonym replacement) to
disentangle architecture effects from data effects. All models trained from
random initialization to avoid pretraining bias.

## Results

| Dataset       | Hand-designed F1 | NAS-optimized F1 | Hand-designed + aug F1 |
|---------------|-----------------|------------------|------------------------|
| SST-2         | 83.7            | 84.2             | 88.1                   |
| TREC           | 88.3            | 87.9             | 90.4                   |
| AG News        | 81.2            | 82.0             | 86.5                   |
| **Average**    | **84.4**        | **84.7**         | **88.3**               |

Statistical significance: NAS vs. hand-designed, p = 0.71 (not significant).

## Conclusion

NAS does not provide a reliable advantage over hand-designed architectures
on low-resource NLP tasks. The modest gains reported by Chen et al.
disappear when data augmentation is controlled. We recommend practitioners
prioritize data quality and augmentation strategies over expensive
architecture search.

## Limitations

- Search space may differ from proprietary NAS systems.
- Only evaluated on text classification tasks.
- Augmentation benefits vary by domain.
