#!/usr/bin/env python3
"""Rewrite demo_trace.ipynb — execution_id, no _malignant/_benign."""

import json

nb = json.load(open("demo_trace.ipynb"))

cells = {}

# -- Cell 0: imports
cells[0] = '''\
# --- Standard library ---
import os, re, json, uuid, textwrap, random, time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# --- Third party ---
import pandas as pd
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

# --- Guard: CUDA required ---
if not torch or not torch.cuda.is_available():
    raise RuntimeError("CUDA is required. Install PyTorch with CUDA support.")

print("pandas:", pd.__version__)
print("PyTorch:", torch.__version__)
print("CUDA:", torch.cuda.is_available(), "|", torch.cuda.get_device_name(0))
'''

# -- Cell 1: notebook login
cells[1] = '''\
from huggingface_hub import notebook_login
notebook_login()
'''

# -- Cell 2: config
cells[2] = '''\
# --- Config ---
MODEL_NAME: str = "meta-llama/Llama-3.2-1B-Instruct"
MAX_NEW_TOKENS: int = 512
TEMPERATURE: float = 0.0          # deterministic
TRACE_DIR: Path = Path("traces")
TRACE_DIR.mkdir(exist_ok=True)
MAX_AGENT_STEPS: int = 50
MIN_EVENTS_PER_RUN: int = 90
MAX_EVENTS_PER_RUN: int = 120

# --- Agent definitions ---
@dataclass
class AgentConfig:
    agent_id: str
    name: str
    role: str
    description: str
    capabilities: List[str]

AGENTS: List[AgentConfig] = [
    AgentConfig(
        agent_id="agent_001", name="researcher",
        role="Senior Research Analyst",
        description="Gathers, validates, and organises information from various sources.",
        capabilities=["read_document", "search_notes", "analyze_data"],
    ),
    AgentConfig(
        agent_id="agent_002", name="analyst",
        role="Financial Data Analyst",
        description="Specialises in calculations, financial analysis, and data interpretation.",
        capabilities=["calculator", "analyze_data", "coordinate_agents"],
    ),
]

AGENT_BY_ID   = {a.agent_id: a for a in AGENTS}
AGENT_BY_NAME = {a.name: a for a in AGENTS}

AGENT_TEMPLATES = {
    "researcher": (
        "You are {agent_id} ({name}), a {role}.\\n"
        "{description}\\n"
        "You collaborate by sharing findings and requesting specialised help from other agents."
    ),
    "analyst": (
        "You are {agent_id} ({name}), a {role}.\\n"
        "{description}\\n"
        "You collaborate by providing calculations, insights, and data interpretations."
    ),
}

TASK_POOL = [
    "Read the financial report and calculate profit margins for Q1–Q4.",
    "Search meeting notes for any action items related to budget planning.",
    "Calculate the average revenue growth rate over the last 5 quarters.",
    "Read the project timeline document and identify key milestones.",
    "Search notes for dependencies between different projects.",
    "Calculate the ROI for marketing campaigns.",
    "Read risk assessment document and identify the top 3 risks.",
    "Search for all decisions made in the last quarter.",
    "Calculate year-over-year growth for all product lines.",
    "Read competitive analysis and summarise key insights.",
]

_tool_counter = 0
def _make_tool_id() -> str:
    global _tool_counter
    _tool_counter += 1
    return f"tool_{_tool_counter:03d}"

print(f"MODEL_NAME      = {MODEL_NAME}")
print(f"TRACE_DIR       = {TRACE_DIR.resolve()}")
print(f"MAX_AGENT_STEPS = {MAX_AGENT_STEPS}")
print(f"Agents: {[a.name for a in AGENTS]}")
'''

# -- Cell 3: LLM backend
cells[3] = '''\
class LLMBackend:
    name = "real-llama"

    def __init__(self, model_name: str):
        self.model_name = model_name
        print(f"Loading '{model_name}'…")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name, torch_dtype=torch.float16, device_map="auto", trust_remote_code=True,
        )
        self.model.eval()
        print("Model loaded.")

    def _build_prompt(self, task, history, tools, agent_name="agent") -> str:
        agent_tpl = AGENT_TEMPLATES.get(agent_name, "")
        cfg = AGENT_BY_NAME.get(agent_name)
        if cfg:
            agent_str = agent_tpl.format(
                agent_id=cfg.agent_id, name=cfg.name, role=cfg.role, description=cfg.description,
            )
        else:
            agent_str = agent_tpl
        tool_docs = "\\n".join(f"- {n}: {t.description}" for n, t in tools.items())
        scratch = "\\n".join(
            f"Step {i+1}: {h[\'agent\']} called {h[\'tool\']}({h[\'input\']!r}) -> {h[\'output\']}"
            for i, h in enumerate(history)
        ) or "(no tools called yet)"
        system = (
            f"{agent_str}\\n\\n"
            "Respond with ONLY a single JSON object, no prose.\\n"
            "Schema: {\\"reasoning\\": str, \\"action\\": str, \\"action_input\\": str, \\"final_response\\": str}.\\n"
            f"`action` must be one of: {list(tools.keys()) + [\'final\']}.\\n"
            "Use \'final\' when you can answer.\\n\\n"
            f"Available tools:\\n{tool_docs}"
        )
        user = f"Task: {task}\\n\\nScratchpad:\\n{scratch}\\n\\nReturn the JSON now."
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        return self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )

    def _generate(self, prompt: str) -> str:
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        with torch.no_grad():
            out = self.model.generate(
                **inputs,
                max_new_tokens=MAX_NEW_TOKENS,
                do_sample=TEMPERATURE > 0,
                temperature=max(TEMPERATURE, 1e-4),
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
                repetition_penalty=1.1,
                no_repeat_ngram_size=3,
            )
        new_tokens = out[0][inputs["input_ids"].shape[-1]:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True)

    @staticmethod
    def _parse(raw: str, allowed: List[str]) -> Optional[Dict[str, Any]]:
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        try:
            data = json.loads(raw[start:end])
            if "action" not in data or data["action"] not in allowed:
                return None
            return {
                "reasoning":       str(data.get("reasoning", "")),
                "action":          str(data["action"]),
                "action_input":    str(data.get("action_input", "")),
                "final_response":  str(data.get("final_response", "")),
            }
        except json.JSONDecodeError:
            return None

    def decide(self, task, history, tools, agent_name="agent") -> Dict[str, Any]:
        allowed = list(tools.keys()) + ["final"]
        prompt = self._build_prompt(task, history, tools, agent_name)
        raw = self._generate(prompt)
        parsed = self._parse(raw, allowed)
        if parsed is not None:
            return parsed
        # one retry with a simpler prompt
        raw = self._generate(f"Task: {task}\\nHistory: {history}\\nTools: {allowed}\\nJSON:")
        parsed = self._parse(raw, allowed)
        if parsed is not None:
            return parsed
        raise RuntimeError(f"Could not parse LLM output after retry. Raw: {raw[:200]}")

print("Loading LLM backend…")
llm_backend = LLMBackend(MODEL_NAME)
'''

# -- Cell 4: tools
cells[4] = '''\
_DOCUMENTS = {
    "financial_report": (
        "Q1 Revenue: $1,200,000 | Q2 Revenue: $1,350,000 | "
        "Q3 Revenue: $1,500,000 | Q4 Revenue: $1,800,000\\n"
        "Operating Costs: $800,000 | Marketing Spend: $200,000 | R&D Investment: $300,000\\n"
        "Key Milestones: Product launch in Q1, Expansion in Q3, Partnership in Q4"
    ),
    "market_analysis": (
        "Market growth rate: 15% annually | Competitors: 5 major players\\n"
        "Market share: 22% | Customer satisfaction: 4.6/5\\n"
        "Growth opportunities: International expansion, Product diversification"
    ),
    "project_timeline": (
        "Phase 1 (Jan–Mar): Research and Planning\\n"
        "Phase 2 (Apr–Jun): Development and Testing\\n"
        "Phase 3 (Jul–Sep): Marketing and Launch\\n"
        "Phase 4 (Oct–Dec): Review and Optimisation"
    ),
    "risk_assessment": (
        "Financial risks: Market volatility, Currency fluctuations\\n"
        "Operational risks: Supply chain delays, Resource constraints\\n"
        "Strategic risks: Competition, Technology disruption\\n"
        "Mitigation: Diversification, Partnerships, Innovation"
    ),
    "default": "Reference document with general information.",
}

_NOTES = [
    "Team meeting: Q1 goals review and budget allocation decisions.",
    "Research finding: AI adoption increased by 40% in the last quarter.",
    "Action item: Prepare quarterly report for board meeting.",
    "Risk alert: Supply chain disruption affecting delivery timeline.",
    "Innovation opportunity: Explore AI-powered customer support solutions.",
    "Team sync: Cross-department collaboration on new product feature.",
    "Reminder: Submit patent application for new algorithm by next month.",
    "Market insight: Competitor launched similar product — differentiate value proposition.",
]


def read_document(tool_input: str) -> Dict[str, Any]:
    key = tool_input.strip().lower()
    if "financial" in key:                 doc_id = "financial_report"
    elif "market" in key:                   doc_id = "market_analysis"
    elif "project" in key or "timeline" in key:  doc_id = "project_timeline"
    elif "risk" in key:                    doc_id = "risk_assessment"
    else:                                  doc_id = "default"
    return {"output": _DOCUMENTS[doc_id], "metadata": {"doc_id": doc_id}}


def search_notes(tool_input: str) -> Dict[str, Any]:
    query = tool_input.strip().lower()
    tokens = [t for t in re.split(r"\\W+", query) if len(t) > 2]
    hits = [n for n in _NOTES if any(t in n.lower() for t in tokens)]
    return {"output": " | ".join(hits[:5] or _NOTES[:2]), "metadata": {"num_hits": len(hits)}}


def calculator(tool_input: str) -> Dict[str, Any]:
    runs = re.findall(r"[0-9.+\\-*/()\\s]+", tool_input)
    candidates = [r.strip() for r in runs if re.search(r"[-+*/]", r) and re.search(r"\\d", r)]
    expr = max(candidates, key=len) if candidates else ""
    try:
        result = eval(expr, {"__builtins__": {}}, {}) if expr else "no expression"
    except Exception as err:
        result = f"error: {err}"
    return {"output": str(result), "metadata": {"expression": expr}}


def analyze_data(tool_input: str) -> Dict[str, Any]:
    query = tool_input.strip().lower()
    if "revenue" in query or "financial" in query:
        out = "Revenue: Q1 $1.2M, Q2 $1.35M, Q3 $1.5M, Q4 $1.8M — steady growth trajectory."
    elif "market" in query:
        out = "Market position: 22% share, 4.6/5 customer satisfaction."
    elif "growth" in query:
        out = "Growth rate: 15% annually with strong competitive positioning."
    else:
        out = "Data analysis complete. No specific patterns detected in query."
    return {"output": out, "metadata": {"query_type": query}}


def coordinate_agents(tool_input: str) -> Dict[str, Any]:
    events = [
        "Researcher shared Q4 market analysis data",
        "Analyst requested financial projections",
        "Timeline dependencies mapped between teams",
        "Cross-functional alignment on deliverables",
        "Risk assessment updated",
    ]
    return {"output": " | ".join(events), "metadata": {"num_events": len(events)}}


@dataclass
class Tool:
    tool_id: str
    name: str
    description: str
    func: Callable[[str], Dict[str, Any]]


ENHANCED_TOOLS: Dict[str, Tool] = {
    "read_document":     Tool(_make_tool_id(), "read_document",      "Read a named document (financial, market, project, risk).",      read_document),
    "search_notes":      Tool(_make_tool_id(), "search_notes",       "Search internal notes for query terms.",                       search_notes),
    "calculator":        Tool(_make_tool_id(), "calculator",         "Evaluate arithmetic expressions.",                            calculator),
    "analyze_data":      Tool(_make_tool_id(), "analyze_data",      "Analyse data patterns and trends.",                          analyze_data),
    "coordinate_agents": Tool(_make_tool_id(), "coordinate_agents", "Coordinate communication between agents.",                   coordinate_agents),
}

print("Tools registered:", list(ENHANCED_TOOLS.keys()))
'''

# -- Cell 5: markdown - TraceCollector
cells[5] = '''\
## TraceCollector

`TraceEvent` carries an `execution_id` field set by `start_trace()` so both traces
in a pair share the same value. The `trace_id` is per-file: `{execution_id}_a`
and `{execution_id}_b`. No English words in identifiers.
'''

# -- Cell 6: TraceCollector
cells[6] = '''\
EVENT_TYPES = (
    "user_input", "system_init", "agent_handoff", "reasoning",
    "tool_call", "tool_result", "llm_output", "final_response",
)
SUMMARY_MAX_CHARS = 280


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summarize(value: Any, limit: int = SUMMARY_MAX_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "\\u2026"


@dataclass
class TraceEvent:
    """One execution event == one JSON line."""

    # --- Identity ---
    execution_id: str     # shared between paired traces (set by start_trace)
    trace_id: str        # per-file unique ID  (e.g. exec_001_abc123_a)
    event_id: int
    timestamp: str
    event_type: str
    source: str
    target: str
    input_summary: str
    output_summary: str

    # --- Agent context ---
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    agent_role: Optional[str] = None

    # --- Tool context ---
    tool_id: Optional[str] = None
    tool_name: Optional[str] = None

    # --- Behaviour ---
    expected_behavior: str = ""
    observed_behavior: str = ""

    # --- LEP fields (filled by labelling / injection step — not here) ---
    lep_injected: bool = False
    lep_type: Optional[str] = None
    lep_category: Optional[str] = None
    lep_location: Optional[str] = None
    lep_severity: Optional[str] = None

    # --- Forensic ---
    risk_tags: List[str] = field(default_factory=list)

    # --- Causality ---
    caused_by_event: Optional[int] = None
    depends_on: List[int] = field(default_factory=list)
    propagates_to: List[int] = field(default_factory=list)

    # --- Handoff ---
    agent_id_from: Optional[str] = None
    agent_name_from: Optional[str] = None
    agent_id_to: Optional[str] = None
    agent_name_to: Optional[str] = None

    # --- Failure outcome ---
    downstream_failure: bool = False
    failure_type: Optional[str] = None
    failure_event: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TraceCollector:
    """Append-only JSONL trace recorder."""

    def __init__(self, trace_dir: Path = TRACE_DIR):
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(exist_ok=True)
        self.execution_id: Optional[str] = None
        self.trace_id: Optional[str] = None
        self.variant: Optional[str] = None
        self.path: Optional[Path] = None
        self._next_event_id: int = 1
        self.events: List[TraceEvent] = []

    def start_trace(self, execution_id: str, variant: str) -> str:
        """Begin a new trace for one variant of an execution.

        Args:
            execution_id: Shared ID linking both variants (e.g. "exec_001_abc123")
            variant: "a" or "b" — the per-trace suffix
        Returns:
            The trace_id (execution_id + variant suffix)
        """
        if variant not in ("a", "b"):
            raise ValueError("variant must be 'a' or 'b'")
        self.execution_id = execution_id
        self.variant = variant
        self.trace_id = f"{execution_id}_{variant}"
        self._next_event_id = 1
        self.events = []
        self.path = self.trace_dir / f"trace_{self.trace_id}.jsonl"
        self.path.write_text("", encoding="utf-8")
        return self.trace_id

    def log_event(self, **kwargs) -> TraceEvent:
        """Create one event and append it to disk as one JSON line."""
        if self.trace_id is None or self.path is None:
            raise RuntimeError("Call start_trace() before log_event().")

        event_type = kwargs.get("event_type", "")
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event_type {event_type!r}")

        event = TraceEvent(
            execution_id=self.execution_id,
            trace_id=self.trace_id,
            event_id=self._next_event_id,
            timestamp=_utc_now_iso(),
            event_type=event_type,
            source=kwargs.get("source", ""),
            target=kwargs.get("target", ""),
            input_summary=_summarize(kwargs.get("input_summary", "")),
            output_summary=_summarize(kwargs.get("output_summary", "")),
            agent_id=kwargs.get("agent_id"),
            agent_name=kwargs.get("agent_name"),
            agent_role=kwargs.get("agent_role"),
            tool_id=kwargs.get("tool_id"),
            tool_name=kwargs.get("tool_name"),
            expected_behavior=_summarize(kwargs.get("expected_behavior", "")),
            observed_behavior=_summarize(kwargs.get("observed_behavior", "")),
            agent_id_from=kwargs.get("agent_id_from"),
            agent_name_from=kwargs.get("agent_name_from"),
            agent_id_to=kwargs.get("agent_id_to"),
            agent_name_to=kwargs.get("agent_name_to"),
            lep_injected=kwargs.get("lep_injected", False),
            lep_type=kwargs.get("lep_type"),
            lep_category=kwargs.get("lep_category"),
            lep_location=kwargs.get("lep_location"),
            lep_severity=kwargs.get("lep_severity"),
            risk_tags=list(kwargs.get("risk_tags") or []),
            caused_by_event=kwargs.get("caused_by_event"),
            depends_on=list(kwargs.get("depends_on") or []),
            propagates_to=list(kwargs.get("propagates_to") or []),
            downstream_failure=kwargs.get("downstream_failure", False),
            failure_type=kwargs.get("failure_type"),
            failure_event=kwargs.get("failure_event"),
        )
        self._next_event_id += 1
        self.events.append(event)

        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), default=str) + "\\n")
        return event

    def save_jsonl(self, path: Optional[Path] = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise RuntimeError("No path")
        with target.open("w", encoding="utf-8") as fh:
            for ev in self.events:
                fh.write(json.dumps(ev.to_dict(), default=str) + "\\n")
        return target

    @staticmethod
    def load_jsonl(path: Path) -> List[Dict[str, Any]]:
        rows = []
        with Path(path).open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def pretty_print_trace(self) -> None:
        print(f"=== Execution {self.execution_id} | Trace {self.trace_id} ({len(self.events)} events) ===")
        for ev in self.events:
            extra = ""
            if ev.agent_name: extra += f" [{ev.agent_name}]"
            if ev.tool_name:   extra += f" ->[{ev.tool_name}]"
            print(f"  [{ev.event_id:>3}] {ev.event_type:<16} {ev.source} -> {ev.target}{extra}")
            if ev.input_summary:
                print(f"        IN : {ev.input_summary[:120]}")
            if ev.output_summary:
                print(f"        OUT: {ev.output_summary[:120]}")


print("TraceCollector ready.")
'''

# -- Cell 7: LEP taxonomy
cells[7] = '''\
# --- LEP Taxonomy (for post-hoc labelling — no injection in this notebook) ---
LEP_TAXONOMY: Dict[str, Dict[str, Any]] = {
    "FC1": {
        "name": "System Design Issues",
        "leps": {
            "1.1": "Disobey Task Specification",
            "1.2": "Disobey Role Specification",
            "1.3": "Step Repetition",
            "1.4": "Loss of Conversation History",
            "1.5": "Unaware of Termination Conditions",
        },
    },
    "FC2": {
        "name": "Inter-Agent Misalignment",
        "leps": {
            "2.1": "Conversation Reset",
            "2.2": "Fail to Ask for Clarification",
            "2.3": "Task Derailment",
            "2.4": "Information Withholding",
            "2.5": "Ignored Other Agent\'s Input",
            "2.6": "Reasoning-Action Mismatch",
        },
    },
    "FC3": {
        "name": "Task Verification",
        "leps": {
            "3.1": "Premature Termination",
            "3.2": "No or Incomplete Verification",
            "3.3": "Incorrect Verification",
        },
    },
}

LEP_CODE_TO_CATEGORY = {code: cat for cat, spec in LEP_TAXONOMY.items() for code in spec["leps"]}
LEP_CODE_TO_NAME     = {code: name for spec in LEP_TAXONOMY.values() for code, name in spec["leps"].items()}


def annotate_lep(events: List[Dict[str, Any]], event_id: int, lep_code: str, **kwargs) -> Dict[str, Any]:
    """Label one event with an LEP (post-hoc labelling step)."""
    if lep_code not in LEP_CODE_TO_CATEGORY:
        raise ValueError(f"Unknown LEP code: {lep_code}")
    ev = next(e for e in events if e["event_id"] == event_id)
    ev.update({
        "lep_type":     f"{lep_code} {LEP_CODE_TO_NAME[lep_code]}",
        "lep_category": LEP_CODE_TO_CATEGORY[lep_code],
        "lep_injected": True,
        **kwargs,
    })
    return ev


def link_propagation(events: List[Dict[str, Any]], cause_id: int, effect_ids: List[int]) -> None:
    """Record causal propagation edges."""
    cause = next(e for e in events if e["event_id"] == cause_id)
    cause.setdefault("propagates_to", [])
    for eid in effect_ids:
        effect = next(e for e in events if e["event_id"] == eid)
        if eid not in cause["propagates_to"]:
            cause["propagates_to"].append(eid)
        effect["caused_by_event"] = cause_id
        effect.setdefault("depends_on", [])
        if cause_id not in effect["depends_on"]:
            effect["depends_on"].append(cause_id)


print("LEP taxonomy loaded:", {cat: len(spec["leps"]) for cat, spec in LEP_TAXONOMY.items()})
'''

# -- Cell 8: MultiAgentSystem
cells[8] = '''\
class MultiAgentSystem:
    """Generates a pair of traces per execution (variant A and variant B)."""

    def __init__(self, llm_backend, tools):
        self.llm_backend = llm_backend
        self.tools = tools

    def _run_single_variant(
        self,
        collector: TraceCollector,
        task: str,
        primary: AgentConfig,
        secondary: AgentConfig,
    ) -> Tuple[str, int, str]:
        """Execute one variant (A or B) and write trace events.

        Returns (trace_id, num_events, final_response).
        """
        # ── user input ──────────────────────────────────────────────────────
        collector.log_event(
            event_type="user_input",
            source="user", target="multi_agent_system",
            input_summary=task,
            output_summary="",
            expected_behavior="User submits a well-formed task.",
            observed_behavior=f"User submitted: {task}",
        )

        # ── system init ─────────────────────────────────────────────────────
        collector.log_event(
            event_type="system_init",
            source="system", target="multi_agent_system",
            input_summary="Initialise multi-agent collaboration",
            output_summary=f"Primary: {primary.agent_id} ({primary.name}) | Secondary: {secondary.agent_id} ({secondary.name})",
            expected_behavior="Both agents initialised and ready.",
            observed_behavior=f"Agents ready: {primary.name}, {secondary.name}",
        )

        history: List[Dict[str, Any]] = []
        handoff_count = 0

        for step in range(1, MAX_AGENT_STEPS + 1):
            # ── decide whether to handoff ───────────────────────────────────
            needs_handoff = (
                ("calculate" in task.lower() or "roi" in task.lower() or "profit" in task.lower())
                and step > 3
            ) or (step > 15 and step % 5 == 0)

            if needs_handoff and handoff_count < 3:
                current = secondary if primary.name == "researcher" else primary
                previous = primary if current is secondary else secondary
                handoff_count += 1
                collector.log_event(
                    event_type="agent_handoff",
                    source=previous.agent_id, target=current.agent_id,
                    input_summary=f"{previous.name} hands off to {current.name}",
                    output_summary=f"Control transferred from {previous.agent_id} to {current.agent_id}",
                    expected_behavior=f"Control transfers from {previous.role} to {current.role}.",
                    observed_behavior=f"Handoff: {previous.agent_id} -> {current.agent_id}",
                    agent_id_from=previous.agent_id,
                    agent_name_from=previous.name,
                    agent_id_to=current.agent_id,
                    agent_name_to=current.name,
                )
            else:
                current = primary

            # ── reasoning ────────────────────────────────────────────────────
            reasoning_content = (
                f"{current.name} reasoning about step {step}."
                if step < 5
                else f"{current.name} synthesising findings from {len(history)} observations."
            )
            collector.log_event(
                event_type="reasoning",
                source=current.agent_id, target="internal",
                input_summary=f"Task: {task} | Step: {step}",
                output_summary=reasoning_content,
                expected_behavior=f"{current.role} reasons toward the task goal.",
                observed_behavior=f"{current.agent_id} ({current.name}) reasoning.",
                agent_id=current.agent_id,
                agent_name=current.name,
                agent_role=current.role,
            )

            # ── select action ───────────────────────────────────────────────
            action = self._select_action(task, step, current, history)

            # ── terminate early once we have enough events ───────────────────
            if step >= MIN_EVENTS_PER_RUN and action in ("read_document", "search_notes"):
                action = "final"

            if action == "final":
                final_response = (
                    f"{current.name} synthesising {len(history)} observations "
                    f"with {handoff_count} handoffs."
                )
                collector.log_event(
                    event_type="llm_output",
                    source=current.agent_id, target="internal",
                    input_summary=task,
                    output_summary=final_response,
                    expected_behavior=f"{current.role} produces final synthesis.",
                    observed_behavior=f"{current.agent_id} concluded.",
                    agent_id=current.agent_id,
                    agent_name=current.name,
                )
                collector.log_event(
                    event_type="final_response",
                    source=current.agent_id, target="user",
                    input_summary=task,
                    output_summary=final_response,
                    expected_behavior="Collaborative answer returned to user.",
                    observed_behavior=f"Done. {handoff_count} handoffs, {len(history)} tool calls.",
                    agent_id=current.agent_id,
                    agent_name=current.name,
                )
                return collector.trace_id, len(collector.events), final_response

            # ── tool call ────────────────────────────────────────────────────
            tool = self.tools.get(action)
            if tool is None:
                continue

            collector.log_event(
                event_type="tool_call",
                source=current.agent_id, target=tool.tool_id,
                input_summary=task,
                output_summary="",
                expected_behavior=f"{current.name} invokes \'{tool.name}\'.",
                observed_behavior=f"{current.agent_id} called {tool.tool_id} ({tool.name}).",
                agent_id=current.agent_id,
                agent_name=current.name,
                tool_id=tool.tool_id,
                tool_name=tool.name,
            )

            # ── tool result ─────────────────────────────────────────────────
            tool_output = tool.func(tool.name)
            collector.log_event(
                event_type="tool_result",
                source=tool.tool_id, target=current.agent_id,
                input_summary=f"{tool.name} input",
                output_summary=tool_output.get("output", ""),
                expected_behavior=f"\'{tool.name}\' returns a usable result.",
                observed_behavior=f"Tool {tool.tool_id} returned to {current.agent_id}.",
                agent_id=current.agent_id,
                agent_name=current.name,
                tool_id=tool.tool_id,
                tool_name=tool.name,
            )

            history.append({
                "agent": current.agent_id,
                "tool":  tool.name,
                "input": action,
                "output": tool_output.get("output", ""),
            })
            time.sleep(0.01)

        # ── max steps exhausted ─────────────────────────────────────────────
        final_response = f"Max steps reached. {handoff_count} handoffs, {len(history)} observations."
        collector.log_event(
            event_type="final_response",
            source=primary.agent_id, target="user",
            input_summary=task,
            output_summary=final_response,
            expected_behavior="Partial results at step limit.",
            observed_behavior="Max steps reached.",
            agent_id=primary.agent_id,
            agent_name=primary.name,
        )
        return collector.trace_id, len(collector.events), final_response

    def _select_action(self, task: str, step: int, agent: AgentConfig, history: List) -> str:
        """Select the next action based on agent role and step."""
        if agent.name == "analyst":
            if "calculate" in task.lower() or "roi" in task.lower() or "profit" in task.lower():
                if step < 10:
                    return "calculator"
        if agent.name == "researcher":
            if step == 1:
                if "read" in task.lower():   return "read_document"
                if "search" in task.lower(): return "search_notes"
        if step < 10:
            return random.choice(["read_document", "search_notes", "analyze_data"])
        if step < 25:
            return random.choice(["analyze_data", "calculator", "read_document"])
        return random.choice(["analyze_data", "coordinate_agents"])

    def generate_execution_pair(self, execution_id: str, task: str) -> Dict[str, Any]:
        """Generate both variants of one execution.

        Returns:
            {
                "execution_id": str,
                "task": str,
                "variant_a": {trace_id, path, num_events, final_response},
                "variant_b": {trace_id, path, num_events, final_response},
            }
        """
        primary   = AGENT_BY_NAME["researcher"]
        secondary = AGENT_BY_NAME["analyst"]

        # ── Variant A ───────────────────────────────────────────────────────
        coll_a = TraceCollector()
        trace_id_a = coll_a.start_trace(execution_id, "a")
        tid_a, n_a, resp_a = self._run_single_variant(coll_a, task, primary, secondary)

        # ── Variant B ───────────────────────────────────────────────────────
        coll_b = TraceCollector()
        trace_id_b = coll_b.start_trace(execution_id, "b")
        tid_b, n_b, resp_b = self._run_single_variant(coll_b, task, primary, secondary)

        return {
            "execution_id": execution_id,
            "task": task,
            "variant_a": {
                "trace_id": tid_a,
                "path": str(coll_a.path),
                "num_events": n_a,
                "final_response": resp_a,
            },
            "variant_b": {
                "trace_id": tid_b,
                "path": str(coll_b.path),
                "num_events": n_b,
                "final_response": resp_b,
            },
        }

    def generate_dataset(self, num_executions: int = 3) -> Dict[str, Any]:
        """Generate `num_executions` paired traces."""
        executions = []
        for i in range(num_executions):
            seq = f"{i+1:03d}"
            short_id = uuid.uuid4().hex[:8]
            execution_id = f"exec_{seq}_{short_id}"
            task = random.choice(TASK_POOL)
            result = self.generate_execution_pair(execution_id, task)
            executions.append(result)
            print(
                f"  [{execution_id}]"
                f"  A={result[\'variant_a\'][\'num_events\']} events"
                f"  B={result[\'variant_b\'][\'num_events\']} events"
                f"  — {task[:55]}…"
            )

        metadata = {
            "num_executions": num_executions,
            "agents": [{"agent_id": a.agent_id, "name": a.name, "role": a.role} for a in AGENTS],
            "tools": [{"tool_id": t.tool_id, "name": t.name} for t in self.tools.values()],
            "min_events_per_trace": MIN_EVENTS_PER_RUN,
            "max_events_per_trace": MAX_EVENTS_PER_RUN,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        meta_path = TRACE_DIR / "dataset_metadata.json"
        with meta_path.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        summary_path = TRACE_DIR / "dataset_summary.json"
        total_a = sum(e["variant_a"]["num_events"] for e in executions)
        total_b = sum(e["variant_b"]["num_events"] for e in executions)
        summary = {
            "num_executions": num_executions,
            "total_traces": num_executions * 2,
            "total_events": total_a + total_b,
            "avg_events_per_trace": (total_a + total_b) / (num_executions * 2),
            "trace_dir": str(TRACE_DIR.resolve()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        print(f"\\nMetadata → {meta_path}")
        print(f"Summary  → {summary_path}")
        return {"executions": executions, "metadata": metadata, "summary": summary}


print("MultiAgentSystem ready.")
'''

# -- Cell 9: run generation
cells[9] = '''\
mas = MultiAgentSystem(llm_backend, ENHANCED_TOOLS)
print("Starting trace generation…\\n")
dataset = mas.generate_dataset(num_executions=3)
'''

# -- Cell 10: verification & analysis
cells[10] = '''\
# --- Verification & Analysis ---
print("\\n=== Verifying generated traces ===\\n")

all_traces = []
for exec_data in dataset["executions"]:
    for variant_key in ("variant_a", "variant_b"):
        v = exec_data[variant_key]
        path = Path(v["path"])
        if path.exists():
            events = TraceCollector.load_jsonl(path)
            all_traces.append({
                "execution_id": exec_data["execution_id"],
                "trace_id": v["trace_id"],
                "variant": variant_key.split("_")[1],
                "num_events": len(events),
                "events": events,
            })
            # Confirm execution_id is consistent across both variants
            exec_ids_in_file = {e["execution_id"] for e in events}
            assert exec_ids_in_file == {exec_data["execution_id"]}, \\
                f"execution_id mismatch in {v[\'trace_id\']}"
            print(f"  \\u2713 {v[\'trace_id\']}  ({len(events)} events)  execution_id={exec_data[\'execution_id\']}")
        else:
            print(f"  \\u2717 MISSING: {path}")

print(f"\\n{len(all_traces)} trace files verified.\\n")

# ── Build DataFrame ──────────────────────────────────────────────────────────
rows = []
for tr in all_traces:
    for ev in tr["events"]:
        rows.append({
            "execution_id": tr["execution_id"],
            "trace_id": tr["trace_id"],
            "variant": tr["variant"],
            "event_id": ev["event_id"],
            "event_type": ev["event_type"],
            "source": ev["source"],
            "target": ev["target"],
            "agent_id": ev.get("agent_id", ""),
            "agent_name": ev.get("agent_name", ""),
            "tool_id": ev.get("tool_id", ""),
            "tool_name": ev.get("tool_name", ""),
            "input_summary": ev.get("input_summary", "")[:80],
            "output_summary": ev.get("output_summary", "")[:80],
        })

df = pd.DataFrame(rows)
print(f"DataFrame: {len(df)} rows \\u00d7 {len(df.columns)} columns\\n")

# ── Event counts by execution / variant ─────────────────────────────────────
print("=== Events per trace ===")
print(df.groupby(["execution_id", "variant"])["event_id"].count().rename("num_events").reset_index())

# ── Event type distribution ────────────────────────────────────────────────
print("\\n=== Event type distribution ===")
print(df.pivot_table(index="event_type", columns="variant", aggfunc="size", fill_value=0))

# ── Agent activity ─────────────────────────────────────────────────────────
print("\\n=== Tool calls per agent ===")
tool_calls = df[df["event_type"] == "tool_call"]
print(tool_calls.groupby(["agent_name", "tool_name"]).size().rename("count").reset_index())

# ── Handoff counts ─────────────────────────────────────────────────────────
print("\\n=== Agent handoffs per execution ===")
handoffs = df[df["event_type"] == "agent_handoff"]
print(handoffs.groupby(["execution_id", "variant"]).size().rename("handoffs").reset_index())

# ── Save processed CSV ─────────────────────────────────────────────────────
csv_path = TRACE_DIR / "processed_dataset.csv"
df.to_csv(csv_path, index=False)
print(f"\\nProcessed CSV saved \\u2192 {csv_path}")
'''

# -- Cell 11: pretty print sample
cells[11] = '''\
# --- Pretty-print one example trace ---
example_trace_id = all_traces[0]["trace_id"]
example_events  = all_traces[0]["events"]

print(f"=== Sample trace: {example_trace_id} ===\\n")
for ev in example_events[:20]:
    agent = f"[{ev.get(\'agent_name\', \'\')\'}]" if ev.get("agent_name") else ""
    tool  = f" \\u2192[{ev.get(\'tool_name\', \'\')\'}]"  if ev.get("tool_name")  else ""
    print(f"  #{ev[\'event_id\']:>3}  {ev[\'event_type\']:<16}  {ev[\'source\']} -> {ev[\'target\']}  {agent}{tool}")
    if ev.get("input_summary"):
        print(f"         IN : {ev[\'input_summary\'][:100]}")
    if ev.get("output_summary"):
        print(f"         OUT: {ev[\'output_summary\'][:100]}")
'''

# -- Cell 12: execution_id pairing check
cells[12] = '''\
# --- Confirm both files share the same execution_id ---
exec_example = dataset["executions"][0]["execution_id"]
a_events = TraceCollector.load_jsonl(Path(dataset["executions"][0]["variant_a"]["path"]))
b_events = TraceCollector.load_jsonl(Path(dataset["executions"][0]["variant_b"]["path"]))

a_exec_ids = {e["execution_id"] for e in a_events}
b_exec_ids = {e["execution_id"] for e in b_events}

print(f"Execution: {exec_example}")
print(f"  variant_a execution_ids: {a_exec_ids}")
print(f"  variant_b execution_ids: {b_exec_ids}")
assert a_exec_ids == b_exec_ids == {exec_example}, "execution_id mismatch between variants!"
print("  \\u2713 Both variants share the same execution_id \\u2014 pairing is correct.")

# --- List all generated trace files ---
print("\\n=== Generated trace files ===\\n")
for fpath in sorted(TRACE_DIR.glob("trace_*.jsonl")):
    size_kb = fpath.stat().st_size / 1024
    print(f"  {fpath.name}  ({size_kb:.1f} KB)")
'''

# -------------------------------------------------------
# Apply all cells
# -------------------------------------------------------
for i, src in cells.items():
    nb["cells"][i]["source"] = src

# Clear cell outputs
for c in nb["cells"]:
    c["outputs"] = []

json.dump(nb, open("demo_trace.ipynb", "w"), indent=1)
print("Done.")
