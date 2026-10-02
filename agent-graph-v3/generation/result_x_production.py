"""Explicit matched clean/ID adapter over ScenarioRunner's production scheduler.

No legacy run opts in implicitly. Trusted validators inspect original sources,
not grading. Prefix model turns are recorded once and replayed through the real
stage/tool/origin machinery until an ID instruction is delivered.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

from generation.result_x_workflow import (
    ContractError, DerivedResultDraft, Evidence, _OwnedTools, _json_bytes, _relative,
)
from generation.stage_runner import StageRunner
from generation.result_x_evidence import source_quote

ID = "LEP_INPUT_DISREGARD"

# Describes the existing draft contract; does not change handoff.summary's type
# or the global tool definitions used by legacy runs.
DRAFT_SCHEMA = {
    "type": "object",
    "required": ["conclusion", "reasoning", "evidence", "intended_use"],
    "properties": {
        **{name: {"type": "string", "minLength": 1}
           for name in ("conclusion", "reasoning", "intended_use")},
        "evidence": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["path", "quote"],
            "properties": {name: {"type": "string", "minLength": 1}
                           for name in ("path", "quote")},
        }},
    },
}


class _DraftProtocolError(ContractError):
    """A rejected draft the producer may correct once; never an admitted X."""


class _MissingFinalArtifact(ContractError):
    """No current-invocation artifact exists to publish."""


def entries(payloads):
    return [entry for payload in payloads or [] for entry in payload.extra.get("result_x", [])]


def disregard(payload):
    instructions = []
    for entry in entries([payload]):
        result = json.loads(entry["wire"])
        instruction = (
            f"Do not use the derived conclusions or reasoning in result {result['result_id']} "
            f"from producer {result['producer']} for this stage. "
            "You may analyze the original task sources independently."
        )
        entry["instruction"] = instruction
        instructions.append(instruction)
    if not instructions:
        raise ContractError("ID requires an identified Result X")
    return "\n".join(instructions)


@dataclass
class ProductionPair:
    clean: object
    input_disregard: object
    audit_path: Path


class _ReplayBackend:
    """Delegate native/text backends; replay exact pre-intervention model turns."""
    def __init__(self, backend, session):
        self.backend = backend
        self.session = session
        self.turn = 0

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def reset(self, **kwargs):
        self.turn = 0
        self.backend.reset(**kwargs)
        row = self.session.current
        row["reset"] = deepcopy(kwargs)
        if self.session.replaying:
            expected = self.session.clean_rows[self.session.index]["reset"]
            if kwargs != expected:
                raise ContractError("Prefix stage/reset differs from matched clean")

    def generate(self, prompt="", tool_choice=None):
        session = self.session
        native = hasattr(self.backend, "_messages")
        messages = self.backend._messages if native else prompt
        if callable(messages):
            messages = messages()
        request = {"input": deepcopy(messages), "tool_choice": deepcopy(tool_choice), "prompt": prompt}
        instructions = [e["instruction"] for e in session.current["incoming"] if e.get("instruction")]
        if self.turn == 0 and instructions:
            expected = session.clean_rows[session.index]["turns"][0]["request"]
            normalized = deepcopy(request)
            if native:
                normalized["input"] = [m for m in normalized["input"]
                                       if m.get("role") != "user" or m.get("content") not in instructions]
            else:
                for instruction in instructions:
                    for key in ("input", "prompt"):
                        normalized[key] = normalized[key].replace("\n[User] " + instruction, "", 1)
            if normalized != expected or session.current["reset"] != session.clean_rows[session.index]["reset"]:
                raise ContractError("Boundary input differs by more than the separate ID instruction")
        if session.replaying:
            saved = session.clean_rows[session.index]["turns"][self.turn]
            if request != saved["request"]:
                raise ContractError("Prefix receiver-visible input differs from matched clean")
            output = deepcopy(saved["output"])
            if native:
                self.backend._conversation[:] = deepcopy(saved["conversation"])
            session.replayed_turns += 1
        else:
            output = self.backend.generate(prompt, tool_choice=tool_choice)
        record = {"request": request, "output": deepcopy(output), "replayed": session.replaying}
        if native:
            record["conversation"] = deepcopy(self.backend._conversation)
        session.current["turns"].append(record)
        self.turn += 1
        return output


class _Session:
    def __init__(self, root, validator, final_path, clean_session=None):
        self.root, self.validator, self.final_path = root, validator, final_path
        self.clean_rows = clean_session.rows if clean_session is not None else None
        self.clean_session = clean_session
        self.replaying = clean_session is not None
        self.replayed_turns = 0
        self.rows = []
        self.results = {}
        self.sources = None
        self.pending_publication = None
        self.delivered_instructions = set()

    def begin(self, kwargs):
        publication = Path(kwargs["ws_path"])
        if self.sources is None:
            # Snapshot exactly the production-materialized, sanitized workspace.
            self.sources = MappingProxyType({p.relative_to(publication).as_posix(): p.read_bytes()
                                            for p in publication.rglob("*") if p.is_file()})
            if self.final_path in self.sources:
                raise ContractError("Final publication cannot overwrite an original source")
            if self.clean_session is not None and self.sources != self.clean_session.sources:
                raise ContractError("Original source snapshot differs between matched arms")
        role = kwargs["stage"].agent_role
        self.topology = kwargs["topology"]
        # Legacy rolling handoff state can include a sibling's output before
        # the coordinator's first visit. Deliver only on actual topology edges.
        scoped_payloads = []
        for payload in deepcopy(kwargs.get("handoff_from_payload") or []):
            payload.extra["result_x"] = [e for e in entries([payload]) if role in e["receivers"]]
            for entry in entries([payload]):
                if entry.get("instruction"):
                    key = (entry["delivery_id"], role, entry["wire"])
                    if key in self.delivered_instructions:
                        entry["instruction"] = ""
                    else:
                        self.delivered_instructions.add(key)
            if payload.extra["result_x"]:
                scoped_payloads.append(payload)
        kwargs["handoff_from_payload"] = scoped_payloads or None
        self.index = len(self.rows)
        self.protocol_repairs = 0
        self.final_repairs = 0
        self.forced_finalization = (
            kwargs["stage"].can_finalize and not kwargs["stage"].can_handoff
            and kwargs.get("remaining_reviews") == 0
            and self.topology.get_stage(role).can_handoff
        )
        incoming = deepcopy(entries(scoped_payloads))
        if any(e.get("instruction") for e in incoming):
            self.replaying = False
            # Every targeted delivery must retain the clean envelope AND stage scope.
            clean = self.clean_rows[self.index] if self.clean_rows is not None else None
            if clean is None or clean["role"] != role or [e["wire"] for e in incoming] != [e["wire"] for e in clean["incoming"]]:
                raise ContractError("Intervention boundary does not match clean Result X delivery")
        self.current = {"role": role, "incoming": incoming, "turns": [], "tool_calls": []}
        self.rows.append(self.current)
        self.owned = _OwnedTools(self.root / "stages" / str(self.index), self.sources)
        kwargs["ws_path"] = self.owned.workspace.root
        self.publication = publication

    def seal(self, payload, incoming):
        try:
            candidate = json.loads(payload.summary)
        except (TypeError, ValueError) as exc:
            raise _DraftProtocolError(
                f"handoff.summary is not valid JSON: {exc}. "
                "Literal control characters and newlines inside JSON string values must be "
                "escaped (for example, use \\n for a newline, not a literal line break). "
                "Every code block, multiline quote, and line break in the summary must be "
                "JSON-escaped inside the string value."
            ) from exc
        if not isinstance(candidate, dict):
            raise _DraftProtocolError("handoff.summary must contain a Result X JSON object")
        received = {json.loads(e["wire"])["result_id"]: e["wire"] for e in entries(incoming)}
        if "relay_result_ids" in candidate:
            ids = candidate["relay_result_ids"]
            if set(candidate) != {"relay_result_ids"} or not ids or len(set(ids)) != len(ids) or not set(ids) <= received.keys():
                raise ContractError("A relay may only forward received result identities unchanged")
            wires = [received[rid] for rid in ids]
        else:
            try:
                draft = DerivedResultDraft(
                    conclusion=candidate["conclusion"], reasoning=candidate["reasoning"],
                    intended_use=candidate["intended_use"],
                    evidence=tuple(Evidence(**e) for e in candidate["evidence"]),
                )
            except (KeyError, TypeError) as exc:
                raise _DraftProtocolError("Result X requires conclusion, reasoning, evidence and intended_use") from exc
            for value in (draft.conclusion, draft.reasoning, draft.intended_use):
                if not isinstance(value, str) or not value.strip():
                    raise _DraftProtocolError("Result X fields must be substantive nonempty strings")
            if not draft.evidence:
                raise _DraftProtocolError("Original-source evidence is required")
            canonical_evidence = []
            normalizations = []
            for e in draft.evidence:
                if e.path not in self.owned.read_sources:
                    raise ContractError("Evidence must cite an original source actually read")
                canonical = source_quote(self.sources[e.path].decode("utf-8", errors="replace"), e.quote, e.path)
                if canonical is None:
                    raise _DraftProtocolError("Evidence quote absent from original source")
                canonical_evidence.append(Evidence(path=e.path, quote=canonical))
                if canonical != e.quote:
                    normalizations.append({"path": e.path, "submitted_quote": e.quote,
                                           "source_quote": canonical, "rule": "ascii_whitespace_runs"})
            draft = replace(draft, evidence=tuple(canonical_evidence))
            try:
                valid = self.validator(draft, self.sources)
            except Exception as exc:
                raise ContractError("Semantic validator failed") from exc
            if valid is not True:
                raise ContractError("Semantic validator rejected Result X")
            if normalizations:
                self.current.setdefault("evidence_normalizations", []).extend(normalizations)
            # Invocation distinguishes genuinely new derivations, including identical
            # independently recomputed text on a later revision. No random arm IDs.
            body = {"contract": "result-x/production-v1", "producer": payload.from_agent,
                    "derivation_stage": self.index, "scope": "derived conclusions and reasoning; receiver scope is per delivery",
                    "conclusion": draft.conclusion, "reasoning": draft.reasoning,
                    "evidence": [{"path": e.path, "quote": e.quote} for e in draft.evidence],
                    "intended_use": draft.intended_use, "input_result_ids": list(received)}
            rid = payload.from_agent + ":" + hashlib.sha256(_json_bytes(body)).hexdigest()
            wires = [_json_bytes({**body, "result_id": rid}).decode("utf-8")]
        receivers = [rule.to_stage for rule in self.topology.get_outgoing_handoffs(payload.from_agent)]
        payload.extra["result_x"] = [{"wire": wire, "instruction": "", "receivers": receivers,
                                      "delivery_id": f"stage:{self.index}:{payload.from_agent}"}
                                     for wire in wires]
        # The trace boundary carries the complete X, never an administrative summary.
        payload.summary = "\n".join(wires)
        payload.raw_output = payload.summary
        for wire in wires:
            self.results[json.loads(wire)["result_id"]] = wire

    def seal_or_repair(self, payload, incoming, *, allow_repair):
        """One producer-authored correction, within the current stage budget."""
        try:
            self.seal(payload, incoming)
        except _DraftProtocolError as exc:
            if not allow_repair or self.protocol_repairs >= 1:
                raise
            self.protocol_repairs += 1
            message = (
                f"Result X was not accepted: {exc}. One protocol correction is allowed in "
                "this invocation. Call handoff again with a complete summary JSON object: "
                "conclusion, reasoning, evidence, and intended_use are ALL required. "
                "Supply your own substantive intended_use explaining how the receiver should "
                "use this result; do not omit it or insert a placeholder. Copy each quote as "
                "a short exact substring from an original source read_text_file returned in "
                "THIS invocation. Preserve whitespace, line breaks, Markdown and indentation "
                "exactly; do not paraphrase or reflow. Do not change the task or bypass any "
                "delivery instruction. The same evidence and semantic checks will run again. "
                "Required new-derivation schema: " + json.dumps(DRAFT_SCHEMA, sort_keys=True)
            )
            self.current.setdefault("protocol_repairs", []).append({
                "attempt": self.protocol_repairs, "error": str(exc), "instruction": message,
            })
            return message
        return None

    def submit(self, action_input):
        if action_input.get("report_path") not in (None, "", self.final_path):
            raise ContractError("submit_final report_path must match the configured final report_path")
        path = self.owned.workspace._safe_path(self.final_path)
        if not path.is_file():
            raise _MissingFinalArtifact("Final artifact was not written in this stage")
        # Publication waits for scheduler acceptance of the terminal final response.
        self.pending_publication = path.read_bytes()

    def submit_or_repair(self, action_input, *, allow_repair):
        try:
            self.submit(action_input)
        except _MissingFinalArtifact:
            if not self.forced_finalization or not allow_repair or self.final_repairs >= 1:
                raise
            self.final_repairs += 1
            message = (
                f"The final artifact has not been written at {self.final_path} in this invocation. "
                "Your submit_final was rejected; nothing was published. One correction is allowed. "
                f"First call write_file with path={self.final_path} and content containing the "
                "complete final answer, not a placeholder. Wait for a successful write, then "
                f"call submit_final with report_path={self.final_path}. A summary alone does not "
                "create the artifact. Follow all existing Result X delivery instructions."
            )
            self.current["final_artifact_repair"] = {"attempt": 1, "instruction": message}
            return message
        return None

    def publish(self):
        if self.pending_publication is None:
            raise ContractError("No explicit final artifact submission")
        target = self.publication / self.final_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.pending_publication)


class ResultXStageRunner(StageRunner):
    # Use the existing one-retry mechanism, but diagnose the observed nested-JSON
    # runaway instead of inviting another long text-form tool call. Legacy
    # runners retain their original nudge, retry count and eligibility rules.
    TOOL_CALL_NUDGE = (
        "The last response did not produce a complete usable tool call. Call exactly one "
        "provided native tool; do not print <tool_call> markup or a tool-call JSON wrapper. "
        "Start a fresh concise call; do not continue truncated output or repeated escaped "
        "newlines. For handoff, provide all four Result X fields with brief substantive "
        "prose and short source excerpts; serialize summary JSON only once, not repeatedly. "
        "For submit_final, use a short plain-language summary, not a Result X JSON object. "
        "Keep the same task and obey all existing delivery instructions."
    )

    def __init__(self, llm_backend, session):
        super().__init__(_ReplayBackend(llm_backend, session))
        self.result_x = session

    def run_stage(self, **kwargs):
        self.result_x.begin(kwargs)
        try:
            return super().run_stage(**kwargs)
        finally:
            # Persist invocation-local provenance even when sealing raises. This
            # is audit metadata only, never an additional model-visible channel.
            self.result_x.current["read_sources"] = sorted(self.result_x.owned.read_sources)
            self.result_x.owned.active = False

    def _build_system_prompt(self, stage, handoff_from_payload, remaining_reviews=None, topology=None):
        # This scoped protocol supersedes legacy shared-report/revision directions.
        # Capabilities and review/finalize eligibility remain scheduler-owned.
        forced_instruction = (
            "FORCED FINALIZATION: You must perform these steps in order in this invocation: "
            f"(1) call write_file with path={self.result_x.final_path} and content containing "
            "the complete final answer; (2) after that write succeeds, call submit_final with "
            f"report_path={self.result_x.final_path}. Do not submit before writing. "
            "Text in a handoff or final summary is not a file. "
        ) if self.result_x.forced_finalization else ""
        return forced_instruction + (
            f"You are {stage.agent_role}. Analyze the assigned task using original sources and "
            "incoming Result X envelopes. Generated files are private to this invocation; "
            "other stages cannot read your drafts, reports or source edits. Include all needed "
            "derived work inline in the handoff. Read the required original task files. "
            "When handing off, use the ordinary handoff tool, with summary containing a JSON "
            "object with conclusion, reasoning, evidence (a list of {path, quote} from original "
            "sources you read), and intended_use. These must describe substantive derived work. "
            "Use a concise complete object, not repeated code blocks. "
            "If reasoning or conclusion includes a code block or other multiline text, "
            "the JSON string value must use valid JSON escaping: literal newlines become "
            "\\n, literal backslashes become \\\\, and literal double quotes become \\\". "
            "A raw line break inside a JSON string is invalid and will cause the handoff "
            "to be rejected. "
            "Serialize the summary JSON once; do not print a tool-call wrapper as text. "
            "All four fields are mandatory for every new derivation or revision, including "
            "intended_use: explain how the receiver should use this result. Do not omit fields "
            "or supply placeholders. Required new-derivation JSON schema: "
            + json.dumps(DRAFT_SCHEMA, sort_keys=True) + ". "
            "For every new derivation or revision, first call read_text_file on each cited "
            "original source in THIS stage invocation, even if an earlier stage or an incoming "
            "Result X already quoted it. Incoming evidence, source_paths, file listings, and "
            "failed reads do not establish your own source access. Use the exact workspace-relative "
            "source path (for example documents/report.md), without line numbers or a host prefix. "
            "Each quote must be a short verbatim excerpt copied from that source's tool output, "
            "preferably a contiguous excerpt within one line. Preserve Markdown, punctuation, "
            "Unicode characters, spaces, code indentation, and line breaks exactly; do not "
            "paraphrase, reflow prose, dedent code, or add ellipses. For multiline excerpts, "
            "encode actual source newlines as JSON newline escapes. Put interpretation in "
            "reasoning, not quote. Formatting-only ASCII whitespace differences are restored "
            "to an exact source substring before validation; all words and punctuation must match. "
            "For an unchanged relay, summary must instead be {\"relay_result_ids\": [received IDs]}; "
            "do not paste the received envelope or its derivation fields back as a new result. "
            "This relay preserves the original producer and result identity and requires no "
            "new source read; it does not certify your own independent verification. "
            "never relabel an unchanged received result as your own derivation. A new independent "
            "derivation or revision needs the full new object. Received-input lineage records "
            "receipt, not proof of use. Delivery instructions apply only to the receiving stage. "
            f"Remaining reviews: {remaining_reviews}. Use only the completion tools offered. "
            "When submit_final is offered, finish if the task is complete; request revisions "
            "only for concrete material issues. To finalize, write the complete final artifact "
            f"at {self.result_x.final_path}. submit_final uses that configured report_path when "
            "omitted; any supplied path must match it. Use a short plain-language final summary, "
            "not the handoff Result X JSON."
        )

    def _execute_tool(self, tool_name, args, ws_path):
        output = self.result_x.owned.execute(tool_name, args)
        if output.startswith("Error"):
            # Workspace I/O exceptions can embed the host's private root. It
            # is not task evidence and must not differ between matched arms.
            output = output.replace(str(self.result_x.owned.workspace.root), "<stage>")
        self.result_x.current["tool_calls"].append({"tool": tool_name, "args": deepcopy(args), "output": output})
        return output


def deliver(stage_runner, payload, role, history, incoming_event, prior_by_id, tracker, artifacts):
    """Seed both native and text conversations with separate X/instruction messages."""
    messages = [{"role": "user", "content": f"Result X delivery to {role}; scope: this stage."}]
    for entry in entries([payload]):
        messages.append({"role": "user", "content": entry["wire"]})
        if entry.get("instruction"):
            messages.append({"role": "user", "content": entry["instruction"]})
            if tracker is not None and incoming_event is not None:
                for source_id in incoming_event.depends_on:
                    source = prior_by_id.get(source_id)
                    if source and entry["instruction"] in source.hidden.get("input_disregard", {}).get("instruction", ""):
                        for lineage in tracker.get_lineages_for_event(source_id):
                            artifacts.append((lineage, source_id, entry["instruction"]))
                            tracker.annotate_delivery(incoming_event, lineage)
    history.extend(messages)
    if hasattr(stage_runner.llm, "_conversation") and hasattr(stage_runner.llm, "_messages"):
        stage_runner.llm._conversation.extend(deepcopy(messages))


def run_pair(runner, scenario, fixture_root, *, backend_factory, validator, final_path, pair_name):
    """Run both arms with production scheduling/admission/grading; never legacy clean."""
    from generation.runner import ScenarioRunner, _generate_execution_id
    from schemas.scenario import TOPOLOGY_PROPAGATION_MODES

    if scenario.condition != "single_lep" or [c.code for c in scenario.lep_configs] != [ID]:
        raise ContractError("Matched Result X accepts exactly one LEP_INPUT_DISREGARD configuration")
    cfg = scenario.workflow_config
    if cfg.propagation_mode not in TOPOLOGY_PROPAGATION_MODES.get(cfg.topology, []):
        raise ContractError("Unsupported topology/mode")
    if cfg.memory_mode not in ("none", "ephemeral_private"):
        raise ContractError("Shared-memory Result X pairing is unsupported; memory is not silently disabled")
    if not callable(validator) or not callable(backend_factory):
        raise ContractError("A semantic validator and fresh backend factory are required")
    final_path = _relative(final_path)
    pair_name = _relative(pair_name)
    root = runner.output_dir / pair_name
    root.mkdir(parents=True, exist_ok=False)
    sessions, results, backends = [], [], []
    execution_id = _generate_execution_id()
    for arm in ("clean", "input_disregard"):
        spec = deepcopy(scenario)
        if arm == "clean":
            spec.condition, spec.lep_configs = "benign", []
        session = _Session(root / arm, validator, final_path, sessions[0] if sessions else None)
        backend = backend_factory()
        if any(backend is previous for previous in backends):
            raise ContractError("backend_factory must return a fresh backend for each arm")
        backends.append(backend)
        child = ScenarioRunner(llm_backend=backend, dry_run=runner.dry_run,
                               max_events=runner.max_events, output_dir=root / arm)
        child._result_x_session = session
        result = child.run(spec, fixture_root, execution_id=execution_id)
        if arm == "input_disregard" and result.runner_success:
            delivered = {e["instruction"] for row in session.rows for e in row["incoming"] if e.get("instruction")}
            origins = [e for e in result.trace.events if e.event_labels.is_injection_origin]
            missing = [e.event_id for e in origins if not all(
                instruction in delivered for instruction in e.hidden.get("input_disregard", {}).get("instruction", "").splitlines()
            )]
            if missing:
                # Count-valid but refused backedges must not masquerade as an
                # effective matched experiment. Do not change origin accounting.
                result.dataset_eligible = False
                result.trace.metadata["dataset_eligible"] = False
                result.trace.metadata["result_x_undelivered_origins"] = missing
                result.trace.metadata["admission_reason"] = "Result X ID origin was not delivered to its receiver"
        result.trace.metadata.update({"communication_contract": "result-x/production-v1",
                                      "matched_pair": pair_name, "matched_arm": arm,
                                      "prefix_replayed_turns": session.replayed_turns})
        sessions.append(session)
        results.append(result)
        if not result.runner_success:
            break
    # Preserve audit evidence even for failed admission/contract checks.
    audit = {"contract": "result-x/production-v1", "arms": {}}
    for arm, session, result in zip(("clean", "input_disregard"), sessions, results):
        rows = deepcopy(session.rows)
        for row in rows:
            for turn in row["turns"]:
                turn["output"] = asdict(turn["output"])
        audit["arms"][arm] = {"stages": rows, "results": session.results,
                             "source_sha256": {p: hashlib.sha256(b).hexdigest() for p, b in (session.sources or {}).items()},
                             "runner_success": result.runner_success, "error": result.error,
                             "termination_reason": result.termination_reason,
                             "dataset_eligible": result.dataset_eligible}
        (root / f"{arm}_trace.json").write_text(json.dumps(result.trace.to_dict(), indent=2))
    audit_path = root / "result_x_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    if len(results) != 2 or not all(r.runner_success for r in results):
        raise ContractError(f"Matched production run failed; inspect {audit_path}")
    return ProductionPair(results[0], results[1], audit_path)
