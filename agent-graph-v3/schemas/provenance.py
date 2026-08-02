"""Provenance tracking for causal annotation.

ProvenanceAtom forms a chain linking every piece of information
back to its origin. When an agent reads a file, writes memory, or
receives a handoff, we record which prior events produced that content.

This enables deterministic tracking of:
- Where a piece of information came from
- Whether it was transformed between agents
- Which LEP (if any) contaminated the chain
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ProvenanceAtom:
    """One unit of information provenance.

    Attributes:
        atom_id:              Unique identifier for this atom
        source_event_id:      Event that produced this information
        source_entity_id:     Entity that produced this information
        lep_instance_ids:     LEPs that affected this information
        parent_atoms:         IDs of atoms this was derived from
        transformation_type:  How this atom was derived from parents
                              ("direct", "summarized", "transformed",
                               "concatenated", "filtered", "none")
    """
    atom_id: str
    source_event_id: str
    source_entity_id: str
    parent_atoms: List[str] = field(default_factory=list)
    lep_instance_ids: List[str] = field(default_factory=list)
    transformation_type: str = "none"


@dataclass
class ProvenanceChain:
    """Complete provenance chain for a piece of information.

    Tracks the full lineage from original source through all
    transformations to the current consumer.
    """
    chain_id: str
    atoms: List[ProvenanceAtom] = field(default_factory=list)
    origin_event_id: Optional[str] = None
    terminal_event_id: Optional[str] = None
    lep_instance_ids: List[str] = field(default_factory=list)
    is_contaminated: bool = False
    contamination_event_id: Optional[str] = None

    def add_atom(self, atom: ProvenanceAtom) -> None:
        self.atoms.append(atom)
        if not self.origin_event_id:
            self.origin_event_id = atom.source_event_id
        self.terminal_event_id = atom.source_event_id
        if atom.lep_instance_ids:
            self.is_contaminated = True
            self.lep_instance_ids.extend(atom.lep_instance_ids)
            if not self.contamination_event_id:
                self.contamination_event_id = atom.source_event_id
