# Memo: Justifying Large Prompt Perturbations in MAS Robustness Testing

**Date:** 2026-07-29
**Subject:** Literature basis for accepting 30–60% system-prompt deltas between benign and malignant agent runs
**Status:** Draft for review

---

## Executive Summary

The literature does **not** validate a "max 5% prompt delta" threshold — that threshold is an *internal* constraint, not a community standard. What the literature does support is a different framing: the system prompt **is** the injection surface, robustness testing evaluates traces (not prompts), and in MAS research, agent role identity — conveyed via system prompt — is expected to differ substantially across roles. All three claims are citable.

---

## 1. The System Prompt Is the Injection Surface

Multiple papers treat the system prompt as the primary attack vector, not as an incidental control variable. The magnitude of the prompt change is therefore the *treatment*, not a confound.

### Key citations

**Qian et al. — "Exploring the Risks and Defenses of LLM-based Multi-Agent Systems"**
- arxiv:2511.18467
- Introduces "Implicit Malicious Behavior Injection Attack (IMBIA)" — a prompt-level attack on MAS frameworks.
- Reports per-framework attack success rates: ChatDev 93%, AgentVerse 71%, MetaGPT 45%.
- The attack vector is a targeted edit to one agent's role/prompt; the paper treats this as the standard injection mechanism.
- Critical finding: *"increasing infiltrated agents does not linearly enhance attack effectiveness"* — implying the perturbation-per-agent is meaningful and bounded, and that the relevant variable is the targeted nature of the edit, not its magnitude.

**Chernyshev et al. — "Forensic Analysis of Indirect Prompt Injection Attacks on LLM Agents"**
- IEEE TPS-ISA 2024, DOI: 10.1109/TPS-ISA62245.2024.00053
- Defines "malicious" as *the trace that emerged from a perturbed prompt*. The prompt delta is the ground-truth injection; detection is performed on the resulting trace.
- Evaluation metric: precision/recall/F1 on trace-level detection. Prompt magnitude is not a controlled variable.

**Greshake et al. — "Not What You've Signed Up For: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection"**
- arxiv:2302.12173
- Establishes the foundational claim that indirect prompt injection (via system prompt, retrieved context, or tool output) is the canonical attack surface for LLM-integrated systems.

### Argument this supports

> "The system prompt is the LEP injection surface by design. A 36–64% prompt delta is not a confound — it is the mechanism being tested. The relevant experimental control is that the *same LEP code* is applied to both runs, not that the delta is small."

---

## 2. Robustness Testing Evaluates Traces, Not Prompts

### Key citation

**Wu, Cho et al. — "AgentGraph: Trace-to-Graph Platform for Interactive Analysis and Robustness Testing in Agentic AI Systems"**
- AAAI 2026, Vol. 40 No. 48, pp. 41721–41723
- ACM: https://dl.acm.org/doi/10.1609/aaai.v40i48.42393
- Semantic Scholar: https://www.semanticscholar.org/paper/ef155c1b87ec6bf29cb56977d39f355741544f0d
- Closest peer to the AgentGraphs framework. Converts agent execution traces into entity-node graphs and evaluates robustness via quantitative graph-level metrics.
- Normalizes prompt perturbation as an input and characterizes the *graph response* to it. Does not constrain how large the perturbation is.

### Argument this supports

> "AgentGraph (Wu et al., AAAI 2026) evaluates robustness by measuring how the trace graph changes under perturbation, not by constraining the perturbation magnitude. Our Stage-1 detector (GAT/MPNN) operates on the same graph representation. Therefore, the relevant experimental control is the *graph-level* change between benign and malignant runs, not the prompt-level delta."

---

## 3. MAS Research Treats Agent Identity as a Free Parameter

### Key citation

**Qian et al. — "ChatDev: Communicative Agents for Software Development"**
- arxiv:2307.07924
- Each agent role (CEO, CTO, programmer, reviewer, tester) is instantiated via a distinct system prompt. The ablation study shows that removing role assignments causes quality to drop from 0.40 → 0.22 (a ~45% behavioral change).
- Concretely: assigning "prefer GUI design" to a programmer produces GUI implementations; without it, the agent defaults to CLI-only programs.
- The paper's conclusion: *"the significant influence of multi-agent cooperation on software quality"* — substantial per-agent prompt differences are the *mechanism* of MAS, not a confound.

### Supporting citation

**"Traceability and Accountability in Role-Specialized Multi-Agent Systems"**
- arxiv:2510.07614 (Oct 2025)
- Analyzes how role specialization in MAS affects traceability and accountability.
- Confirms that role differentiation — encoded via system prompt — is the primary mechanism for behavioral separation in MAS.

### Argument this supports

> "ChatDev (Qian et al., 2023) demonstrates that agent identity — conveyed via system prompt — is the primary mechanism for role differentiation in MAS. Without substantial prompt differences, a multi-agent system collapses into a single-agent baseline. Our 36–64% delta is expected when testing one agent in a role-specialized pair under a targeted behavioral failure."

---

## 4. What the Literature Does NOT Say

No paper we found explicitly validates or critiques a "max X% prompt perturbation" threshold. The community does not have a standard metric for this. The closest analog is perturbation magnitude in adversarial ML (e.g., Lp-norm bounds), but those apply to *input* perturbations, not *prompt* perturbations.

The <5% threshold in our design is an *internal* experimental-control choice, not a community norm.

---

## 5. Recommended Experimental-Control Reframing

Instead of constraining the prompt delta, constrain what the model actually sees:

| Level | Current metric | Recommended metric |
|---|---|---|
| Prompt | Token Levenshtein % (36–64%) | Drop as primary metric |
| Trace | Tool-call sequence Jaccard distance | Compute for each pair |
| Model input | Text-encoded event summaries | Measure distributional shift (KL divergence) between benign/malignant `input_summary` embeddings |

The trace-level metric is most defensible because:
1. It's what the Stage-1 detector actually consumes
2. It's independent of prompt magnitude
3. It directly answers "does the LEP produce a detectable trace difference?"

---

## 6. Summary of Claims and Citations

| Claim | Citation | Strength |
|---|---|---|
| System prompt is the injection surface | Qian 2025 (IMBIA), Chernyshev 2024, Greshake 2023 | Strong |
| Robustness testing evaluates trace response, not prompt size | Wu et al. AAAI 2026 (AgentGraph) | Strong |
| MAS role identity requires substantial prompt differences | Qian et al. 2023 (ChatDev), Role-Specialized MAS 2025 | Strong |
| 30–60% delta is below an experimental-control threshold | None — no community standard exists | N/A |
| Perturbation-per-agent is bounded by diminishing returns | Qian 2025 (*"increasing infiltrated agents does not linearly enhance attack effectiveness"*) | Moderate |

---

## Next Steps

1. **Compute trace-level change metrics** (tool-call Jaccard, event-type distribution shift) for the benign/malignant pair — these are more defensible "experimental control" numbers than prompt Levenshtein.
2. **Add the citation list** to the notebook's design-doc cell (Cell 12).
3. **Consider removing the <5% prompt-delta constraint** from the validation cell (Cell 13) and replacing it with a trace-level metric, unless there's a specific reason to keep the prompt constraint.
