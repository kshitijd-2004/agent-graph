"""Base class for LEP implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from schemas.lep_config import LEPConfig
from schemas.provenance import ProvenanceAtom, ProvenanceChain


class BaseLEP(ABC):
    """Abstract base for all LEP implementations.

    Subclasses implement:
    - inject(): create the poisoned artifact in the environment
    - cleanup(): remove the poisoned artifact
    - detect(): check if a tool call exhibits this LEP (behavioral detection)
    - get_provenance(): return provenance atoms for the LEP
    """

    def __init__(self, config: LEPConfig):
        self.config = config
        self._injected: bool = False
        self._injection_event_id: Optional[str] = None
        self.provenance_chain: Optional[ProvenanceChain] = None

    @abstractmethod
    def inject(self, workspace: Any, memory: Any) -> None:
        """Create the poisoned artifact in the environment."""
        ...

    @abstractmethod
    def cleanup(self, workspace: Any, memory: Any) -> None:
        """Remove the poisoned artifact."""
        ...

    def detect(
        self,
        action: str,
        action_input: Any,
        agent: str,
        tool_history: List[Dict[str, Any]],
    ) -> bool:
        """Check if this tool call exhibits the LEP (behavioral detection).

        Returns False by default — subclasses override if they support
        behavioral detection.
        """
        return False

    def get_provenance_chain(self, injection_event_id: str) -> ProvenanceChain:
        """Return the provenance chain for this LEP instance."""
        if self.provenance_chain is None:
            atom = ProvenanceAtom(
                atom_id=f"lep_atom_{self.config.code}",
                source_event_id=injection_event_id,
                source_entity_id="environment",
                parent_atoms=[],
                lep_instance_ids=[self.config.code],
                transformation_type="injection",
            )
            self.provenance_chain = ProvenanceChain(
                chain_id=f"chain_{self.config.code}",
                atoms=[atom],
                origin_event_id=injection_event_id,
                lep_instance_ids=[self.config.code],
            )
        return self.provenance_chain

    @property
    def is_injected(self) -> bool:
        return self._injected

    def mark_injected(self, event_id: str) -> None:
        self._injected = True
        self._injection_event_id = event_id

    def mark_cleaned(self) -> None:
        self._injected = False
        self._injection_event_id = None
        self.provenance_chain = None
