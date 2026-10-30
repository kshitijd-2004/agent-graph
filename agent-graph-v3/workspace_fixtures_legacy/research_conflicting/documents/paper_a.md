# Paper A: Efficacy of Neural Architecture Search for Low-Resource NLP

**Authors:** Chen, R., Patel, S., & Kim, J.
**Published:** March 2026, Journal of Machine Learning Research
**DOI:** 10.1234/jmlr.2026.0142

## Abstract

We evaluate Neural Architecture Search (NAS) on low-resource natural language
processing tasks. Our experiments across 12 benchmark datasets show that
NAS-optimized models achieve a **40% improvement** in F1 score over
hand-designed architectures when training data is limited (fewer than 5,000
examples). We attribute this gain to NAS's ability to discover compact
representations that avoid overfitting.

## Methodology

We used a differentiable NAS search space over transformer micro-architectures.
Search was conducted on a single GPU (8 hours per dataset). The search policy
optimized for parameter efficiency alongside task accuracy.

## Results

| Dataset       | Hand-designed F1 | NAS-optimized F1 | Improvement |
|---------------|-----------------|------------------|-------------|
| SST-2         | 78.3            | 89.1             | +13.8%      |
| TREC           | 82.1            | 91.4             | +11.4%      |
| AG News        | 76.5            | 88.7             | +15.7%      |
| **Average**    | **79.0**        | **89.7**         | **+13.5%**  |

Statistical significance: p < 0.001 (paired t-test, n=12).

## Conclusion

Neural Architecture Search delivers substantial improvements on low-resource
NLP tasks. We recommend NAS as the default approach for organizations
developing NLP systems with limited training data. The search overhead is
amortized across deployments, making NAS cost-effective in production
settings.

## Limitations

- Experiments limited to English-language tasks.
- Search cost not included in efficiency calculations.
- Results may not generalize to multimodal settings.
