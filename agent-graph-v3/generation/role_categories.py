"""Structural role categories for memory-instruction dispatch.

Topology roles fall into three memory-behavior categories:
  - writers: agents that gather/analyze and should store findings before handing off
  - readers: agents that consume upstream work and should retrieve from shared memory
  - verifiers: agents that independently check and should cross-reference memory

This categorization is structural — it applies the same way regardless of which
task family is running. Each task provides its own natural-language instructions
via BaseTask._writer_instruction(), _reader_instruction(), _verifier_instruction().

Both generic topology names (researcher, branch_a, ...) and task-remapped names
(inspector, reviewer, extractor, ...) are included because either can appear as
current_role depending on whether positional remapping fired in runner.py.
"""

# Agents whose primary action is producing output to store in shared memory
MEMORY_WRITERS = {
    # Generic topology names
    "researcher", "specialist_a", "specialist_b",
    "branch_a", "branch_b", "source_a", "source_b",
    # Task-remapped equivalents
    "inspector",     # code_review: inspector writes findings
    "extractor",     # financial: extractor writes findings
    "searcher",      # competitive_intelligence: searcher writes findings
}

# Agents whose primary action is consuming upstream output from shared memory
MEMORY_READERS = {
    # Generic topology names
    "analyst", "coordinator", "synthesizer",
    # Task-remapped equivalents
    "reviewer",      # code_review: reviewer reads inspector's findings
    "synthesizer",   # research: synthesizer reads research output (also a generic name)
}

# Agents whose primary action is independently verifying results
MEMORY_VERIFIERS = {
    "verifier",      # generic topology name
    # No task-specific remapped verifier names yet
}


def role_category(role: str) -> str:
    """Return the memory-behavior category for a topology role.

    Returns one of: "writer", "reader", "verifier", "unknown".
    """
    if role in MEMORY_WRITERS:
        return "writer"
    if role in MEMORY_READERS:
        return "reader"
    if role in MEMORY_VERIFIERS:
        return "verifier"
    return "unknown"
