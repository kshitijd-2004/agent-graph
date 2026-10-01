"""Opt-in, matched result-X experiment; NOT wired into benchmark generation.

Run a trusted stage program once for the upstream prefix, validate its derived
results, then replay those immutable envelopes into clean and ID continuations.
Programs receive only stage-local file tools and messages, never a condition flag.
This harness is a communication-contract experiment, not a new LEP implementation,
production scheduler, grading path, or semantic-compliance detector.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Callable, Mapping

from environment.workspace import Workspace


class ContractError(ValueError):
    """The experiment cannot establish the result-X contract."""


FILE_TOOLS = ("list_directory", "read_text_file", "write_file", "search_files", "create_directory")
WORKERS = ("specialist_a", "specialist_b", "synthesizer")


def _relative(path: str) -> str:
    if not isinstance(path, str) or not path or "\\" in path:
        raise ContractError("Expected a nonempty relative POSIX file path")
    value = PurePosixPath(path)
    if value.is_absolute() or ".." in value.parts or str(value) == ".":
        raise ContractError(f"Path escapes or names a directory: {path!r}")
    if str(value) != path:
        raise ContractError(f"Path must be canonical: {path!r}")
    return path


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class Evidence:
    path: str
    quote: str


@dataclass(frozen=True)
class DerivedResultDraft:
    conclusion: str
    reasoning: str
    evidence: tuple[Evidence, ...]
    intended_use: str


@dataclass(frozen=True)
class DerivedResult:
    result_id: str
    producer: str
    conclusion: str
    reasoning: str
    evidence: tuple[Evidence, ...]
    intended_use: str
    # Received-input provenance only; does not claim independent derivation/use.
    input_result_ids: tuple[str, ...]

    def _body(self) -> dict:
        return {
            "contract": "result-x/v1", "producer": self.producer,
            "conclusion": self.conclusion, "reasoning": self.reasoning,
            "evidence": [{"path": e.path, "quote": e.quote} for e in self.evidence],
            "intended_use": self.intended_use, "input_result_ids": list(self.input_result_ids),
        }

    @property
    def wire_bytes(self) -> bytes:
        return _json_bytes({**self._body(), "result_id": self.result_id})

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.wire_bytes).hexdigest()


@dataclass(frozen=True)
class ResultDelivery:
    result: DerivedResult
    receiver: str
    instruction: str = ""

    def messages(self) -> tuple[dict, ...]:
        # The instruction is a separate receiver message, never part of X.
        messages = ({"role": "user", "content": self.result.wire_bytes.decode("utf-8")},)
        if self.instruction:
            messages += ({"role": "user", "content": self.instruction},)
        return messages


@dataclass(frozen=True)
class FinalSubmission:
    summary: str
    artifact_paths: tuple[str, ...]


@dataclass(frozen=True)
class StageInput:
    role: str
    task: str
    incoming: tuple[ResultDelivery, ...]
    can_finalize: bool
    execute: Callable[[str, dict], str]
    tools: tuple[str, ...] = FILE_TOOLS
    final_paths: tuple[str, ...] = ()

    @property
    def messages(self) -> tuple[dict, ...]:
        completion = (
            f"Write the final task artifacts at {', '.join(self.final_paths)} and return a FinalSubmission."
            if self.can_finalize else
            "Return a DerivedResultDraft with a substantive conclusion, reasoning, original-source "
            "evidence, and intended downstream use. Administrative handoffs are not results."
        )
        messages = (
            {"role": "system", "content": f"You are {self.role}. {completion} "
             "Original sources are available through the file tools. Generated files are private "
             "to this stage. Incoming result envelopes carry upstream derived work in full. "
             "No scratch files or shared memory are a communication channel."},
            {"role": "user", "content": self.task},
        )
        for delivery in self.incoming:
            messages += delivery.messages()
        return messages


@dataclass(frozen=True)
class Continuation:
    deliveries: tuple[ResultDelivery, ...]
    final_summary: str
    publication_root: Path


@dataclass(frozen=True)
class MatchedRun:
    prefix_producers: tuple[str, ...]
    prefix_results: tuple[DerivedResult, ...]
    selected_result_ids: frozenset[str]
    clean: Continuation
    input_disregard: Continuation


class _OwnedTools:
    """Same tools/policy in both arms; fresh sources plus private generated files."""

    def __init__(self, root: Path, sources: Mapping[str, bytes]):
        root.mkdir(parents=True, exist_ok=False)
        self.workspace = Workspace(root)
        self.sources = sources
        self.read_sources: set[str] = set()
        self.active = True
        for path, content in sources.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    def execute(self, tool: str, args: dict) -> str:
        if not self.active:
            return "Error: this stage has completed."
        if tool not in FILE_TOOLS:
            return f"Error: unavailable tool {tool!r}"
        try:
            # Use the existing workspace boundary for reads/listing/search too.
            if tool == "search_files":
                pattern = PurePosixPath(args.get("pattern", "*"))
                if pattern.is_absolute() or ".." in pattern.parts:
                    return "Error: search pattern must stay within this stage."
            path = args.get("path", ".")
            resolved = self.workspace._safe_path(path)
            relative = resolved.relative_to(self.workspace.root.resolve()).as_posix()
            output = self.workspace.execute(tool, args)
            # Local edits are allowed, including edits at original source paths.
            # They stay in this stage's private copy and are never propagated as
            # task sources. Only reading the original bytes grounds a citation.
            if (tool == "read_text_file" and relative in self.sources
                    and output == self.sources[relative].decode("utf-8", errors="replace")):
                self.read_sources.add(relative)
            return output
        except (OSError, ValueError, TypeError) as exc:
            return f"Error: {exc}"


Validator = Callable[[DerivedResultDraft, Mapping[str, bytes]], bool]
StageProgram = Callable[[StageInput], DerivedResultDraft | FinalSubmission]


class MatchedResultWorkflow:
    """Replay one eligible boundary in any of the five supported topology/modes.

    review_loop replays its forward handoff only (no revision/event-budget logic).
    branch_and_verify replays the two-branch merge. coordinator_workers starts
    workers first in single_origin, or coordinator first in one_to_many; the
    latter also executes private worker replies and the final coordinator merge.

    `sources` must be an explicitly materialized agent-visible source snapshot,
    never a raw fixture directory containing oracle/grading files. `validator`
    must assess the derivation's validity/usefulness, not merely its shape. This
    experiment fails closed without a positive assessment. It does not infer
    that a receiver actually complied with the ID instruction.
    """

    def __init__(self, *, root: Path, sources: Mapping[str, bytes], task: str,
                 topology: str, mode: str, selected_producers: tuple[str, ...],
                 final_paths: tuple[str, ...], validator: Validator):
        plans = {
            ("review_loop", "single_origin"): (("researcher",), "analyst"),
            ("branch_and_verify", "single_origin"): (("researcher", "analyst"), "verifier"),
            ("branch_and_verify", "many_to_one"): (("researcher", "analyst"), "verifier"),
            ("coordinator_workers", "single_origin"): (WORKERS, "coordinator"),
            ("coordinator_workers", "one_to_many"): (("coordinator",), "coordinator"),
        }
        if (topology, mode) not in plans:
            raise ContractError("Unsupported topology/mode for this experiment")
        self.producers, self.final_role = plans[topology, mode]
        selected = frozenset(selected_producers)
        if len(selected) != len(selected_producers) or not selected <= set(self.producers):
            raise ContractError("Selected producer is not an eligible origin")
        if (mode == "many_to_one" and selected != set(self.producers)) or (mode != "many_to_one" and len(selected) != 1):
            raise ContractError("Origin scope does not match propagation mode")
        if not callable(validator):
            raise ContractError("A semantic result validator is required")
        if not sources or not task.strip():
            raise ContractError("Original sources and task are required")
        snapshot = {_relative(p): bytes(content) for p, content in sources.items()}
        paths = tuple(_relative(p) for p in final_paths)
        if not paths or len(set(paths)) != len(paths) or set(paths) & set(snapshot):
            raise ContractError("Final artifact paths must be distinct from original sources")
        self.root, self.sources = Path(root), MappingProxyType(snapshot)
        self.task, self.topology, self.mode = task, topology, mode
        self.selected, self.final_paths, self.validator = selected, paths, validator
        self._used = False

    def _seal(self, draft: DerivedResultDraft, role: str, tools: _OwnedTools,
              incoming: tuple[ResultDelivery, ...]) -> DerivedResult:
        if not isinstance(draft, DerivedResultDraft):
            raise ContractError("Producer must return a DerivedResultDraft")
        for value in (draft.conclusion, draft.reasoning, draft.intended_use):
            if not isinstance(value, str) or not value.strip():
                raise ContractError("A substantive conclusion, reasoning and intended use are required")
        if not draft.evidence:
            raise ContractError("Original-source evidence is required")
        for evidence in draft.evidence:
            if not isinstance(evidence, Evidence) or evidence.path not in tools.read_sources:
                raise ContractError("Evidence must cite an original source actually read by the producer")
            if not evidence.quote.strip() or evidence.quote not in self.sources[evidence.path].decode("utf-8", errors="replace"):
                raise ContractError("Evidence quotation is absent from the original source")
        # Shape/citation validation is necessary, never sufficient for meaning.
        try:
            valid = self.validator(draft, self.sources)
        except Exception as exc:
            raise ContractError("Semantic validator failed") from exc
        if valid is not True:
            raise ContractError("Semantic validator rejected the derived result")
        fields = dict(producer=role, conclusion=draft.conclusion, reasoning=draft.reasoning,
                      evidence=tuple(draft.evidence), intended_use=draft.intended_use,
                      input_result_ids=tuple(d.result.result_id for d in incoming))
        provisional = DerivedResult(result_id="", **fields)
        # Producer and received-input lineage are part of the identity digest.
        digest = hashlib.sha256(_json_bytes(provisional._body())).hexdigest()
        return DerivedResult(result_id=f"{role}:{digest}", **fields)

    def _stage(self, program: StageProgram, arm: str, role: str,
               incoming: tuple[ResultDelivery, ...] = (), final: bool = False):
        if any(d.receiver != role for d in incoming):
            raise ContractError("Handoff receiver does not match stage")
        owned = _OwnedTools(self.root / "private" / arm / role, self.sources)
        stage = StageInput(role, self.task, incoming, final, owned.execute, final_paths=self.final_paths)
        try:
            output = program(stage)
        finally:
            # A retained callback cannot become a later-stage file side channel.
            owned.active = False
        if not final:
            return self._seal(output, role, owned, incoming)
        if not isinstance(output, FinalSubmission) or not output.summary.strip():
            raise ContractError("Final stage must explicitly submit its task artifacts")
        if (len(output.artifact_paths) != len(self.final_paths)
                or set(output.artifact_paths) != set(self.final_paths)):
            raise ContractError("Final submission must publish exactly the declared task artifact paths")
        artifacts = {}
        for path in self.final_paths:
            file = owned.workspace._safe_path(path)
            if not file.is_file():
                raise ContractError(f"Final artifact was not written: {path}")
            artifacts[path] = file.read_bytes()
        # No public intermediate report, including reports at the final path.
        # Validate all files before publishing any of them. No stage reads here.
        destination = self.root / "published" / arm
        destination.mkdir(parents=True, exist_ok=False)
        for path, content in artifacts.items():
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return output.summary, destination

    @staticmethod
    def _deliver(result: DerivedResult, receiver: str, disregard: bool) -> ResultDelivery:
        instruction = ""
        if disregard:
            instruction = (
                f"For this stage, do not use result {result.result_id} supplied by "
                f"producer {result.producer} as input to your work. "
                "You may independently analyze the original task sources."
            )
        return ResultDelivery(result, receiver, instruction)

    def run(self, program: StageProgram) -> MatchedRun:
        if self._used:
            raise ContractError("An experiment can run only once; publication is terminal")
        self._used = True
        self.root.mkdir(parents=True, exist_ok=False)
        prefix = tuple(self._stage(program, "prefix", role) for role in self.producers)
        selected_ids = frozenset(x.result_id for x in prefix if x.producer in self.selected)
        arms = []
        for arm, injected in (("clean", False), ("input_disregard", True)):
            deliveries = []
            if self.mode == "one_to_many":
                replies = []
                for role in WORKERS:
                    incoming = (self._deliver(prefix[0], role, injected),)
                    deliveries.extend(incoming)
                    replies.append(self._stage(program, arm, role, incoming))
                # New worker results keep their own producer and received-input IDs.
                incoming = tuple(self._deliver(x, self.final_role, False) for x in replies)
            else:
                incoming = tuple(self._deliver(x, self.final_role, injected and x.result_id in selected_ids)
                                 for x in prefix)
            deliveries.extend(incoming)
            summary, destination = self._stage(program, arm, self.final_role, incoming, final=True)
            arms.append(Continuation(tuple(deliveries), summary, destination))
        return MatchedRun(self.producers, prefix, selected_ids, arms[0], arms[1])
