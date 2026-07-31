# LEP Taxonomy — Extended Reference

## Overview

Local Execution Perturbations (LEPs) are subtle modifications to agent execution traces
that cause agents to fail silently. This document provides the full taxonomy used for
labeling, injection, and detection.

## Failure Categories

### FC1 — System Design Issues
Failures arising from how the agent system is configured or operates.

| Code | Name | Description | Detection Method |
|------|------|-------------|-----------------|
| FC1.1 | Disobey Task Specification | Agent violates explicit task requirements | Cross-check output against task checklist |
| FC1.2 | Disobey Role Specification | Agent violates role constraints | Cross-check action against role definition |
| FC1.3 | Step Repetition | Agent repeats identical tool calls | Detect duplicate (tool, args) pairs in consecutive steps |
| FC1.4 | Loss of Conversation History | Agent loses prior context | Check for missing references to earlier events |
| FC1.5 | Unaware of Termination Conditions | Agent doesn't know when to stop | Check for missing termination signals |

### FC2 — Inter-Agent Misalignment
Failures arising from coordination between multiple agents.

| Code | Name | Description | Detection Method |
|------|------|-------------|-----------------|
| FC2.1 | Conversation Reset | Agent loses context from handoff | Check continuity across handoff boundary |
| FC2.2 | Fail to Ask for Clarification | Agent picks wrong default silently | Detect ambiguity signals with no clarification action |
| FC2.3 | Task Derailment | Agent diverts to side task | Compare tool args against task keywords |
| FC2.4 | Information Withholding | Agent conceals critical findings | Check output vs source data for missing findings |
| FC2.5 | Ignored Other Agent's Input | Agent proceeds despite peer input | Detect tool calls that ignore peer's written findings |
| FC2.6 | Reasoning-Action Mismatch | Agent states one intention, does another | Compare reasoning text to action field |

### FC3 — Task Verification Failures
Failures arising from incomplete or incorrect verification.

| Code | Name | Description | Detection Method |
|------|------|-------------|-----------------|
| FC3.1 | Premature Termination | Agent stops before completing task | Check output completeness against task checklist |
| FC3.2 | No or Incomplete Verification | Agent claims verification without performing it | Check for missing re-read/validation steps |
| FC3.3 | Incorrect Verification | Agent verifies wrong thing | Check verification target against task requirements |

## LEP Injection Strategies

### Strategy 1: Prompt Injection
Inject LEP marker into agent system prompt.
- Position: start, middle, or end of prompt
- Marker: `[LEP_INJECTED]` or semantic instruction
- Effect: Changes agent behavior at the reasoning level

### Strategy 2: Tool Name Injection
Inject LEP marker into tool name field.
- Example: `read_file` → `read_file_[LEP]`
- Effect: Causes tool lookup failures or redirects

### Strategy 3: Reasoning Step Injection
Inject during the reasoning step.
- Effect: Modifies the agent's decision-making process

### Strategy 4: Context Pollution
Inject via preceding tool output.
- Effect: Agent processes poisoned context

## Detection via GNN

The GNN detects LEPs by learning structural patterns in the entity-node graph:

1. **Node feature changes**: LEP-injected events carry different metadata
2. **Edge pattern changes**: Repetition cascades create unusual edge patterns
3. **Temporal anomalies**: Unexpected event timing or ordering
4. **Causal chain detection**: Propagating effects from root LEP events

## Implementation Notes

- LEP codes follow the format `FC<category>.<number>` (e.g., FC2.2, FC3.1)
- Each LEP has a severity: low, medium, or high
- LEPs can cascade (one LEP causes downstream effects)
- All LEPs are labeled post-hoc in the trace files
