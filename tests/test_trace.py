"""Tests for the agentgraph.trace module."""

import json
import unittest
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from agentgraph.trace import (
    Trace,
    TraceEvent,
    TraceEventType,
    TraceVariant,
)


class TestTraceEventType(unittest.TestCase):
    """Tests for TraceEventType enum."""

    def test_from_string_valid(self):
        self.assertEqual(TraceEventType.from_string("user_input"), TraceEventType.USER_INPUT)
        self.assertEqual(TraceEventType.from_string("SYSTEM_INIT"), TraceEventType.SYSTEM_INIT)
        self.assertEqual(TraceEventType.from_string("tool_call"), TraceEventType.TOOL_CALL)

    def test_from_string_invalid(self):
        with self.assertRaises(ValueError):
            TraceEventType.from_string("invalid_event")


class TestTraceEvent(unittest.TestCase):
    """Tests for TraceEvent dataclass."""

    def test_create_event(self):
        event = TraceEvent(
            trace_id="abc123a",
            execution_id="abc123",
            event_id=1,
            timestamp="2026-01-01T00:00:00+00:00",
            event_type=TraceEventType.USER_INPUT,
            source="user",
            target="system",
        )
        self.assertEqual(event.trace_id, "abc123a")
        self.assertEqual(event.execution_id, "abc123")
        self.assertEqual(event.event_id, 1)
        self.assertFalse(event.lep_injected)
        self.assertFalse(event.downstream_failure)

    def test_string_event_type(self):
        event = TraceEvent(
            trace_id="abc123a",
            execution_id="abc123",
            event_id=1,
            timestamp="2026-01-01T00:00:00+00:00",
            event_type="tool_call",  # String instead of enum
            source="agent_001",
            target="mcp_list_directory",
        )
        self.assertEqual(event.event_type, TraceEventType.TOOL_CALL)

    def test_is_perturbation(self):
        event = TraceEvent(
            trace_id="abc123b",
            execution_id="abc123",
            event_id=1,
            timestamp="2026-01-01T00:00:00+00:00",
            event_type=TraceEventType.TOOL_CALL,
            source="agent_001",
            target="mcp_list_directory",
            lep_injected=True,
        )
        self.assertTrue(event.is_perturbation)

    def test_is_tool_event(self):
        tc = TraceEvent(
            trace_id="abc123a",
            execution_id="abc123",
            event_id=1,
            timestamp="2026-01-01T00:00:00+00:00",
            event_type=TraceEventType.TOOL_CALL,
            source="agent_001",
            target="mcp_list_directory",
        )
        self.assertTrue(tc.is_tool_event)

        tr = TraceEvent(
            trace_id="abc123a",
            execution_id="abc123",
            event_id=2,
            timestamp="2026-01-01T00:00:00+00:00",
            event_type=TraceEventType.TOOL_RESULT,
            source="mcp_list_directory",
            target="agent_001",
        )
        self.assertTrue(tr.is_tool_event)

    def test_to_dict(self):
        event = TraceEvent(
            trace_id="abc123a",
            execution_id="abc123",
            event_id=1,
            timestamp="2026-01-01T00:00:00+00:00",
            event_type=TraceEventType.REASONING,
            source="agent_001",
            target="internal",
            input_summary="test input",
            lep_injected=True,
            lep_type="FC2.2 Fail to Ask",
        )
        d = event.to_dict()
        self.assertEqual(d["event_type"], "reasoning")
        self.assertEqual(d["trace_id"], "abc123a")
        self.assertTrue(d["lep_injected"])

    def test_from_dict(self):
        d = {
            "trace_id": "abc123a",
            "execution_id": "abc123",
            "event_id": 1,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "event_type": "tool_call",
            "source": "agent_001",
            "target": "mcp_list_directory",
            "input_summary": "",
            "output_summary": "",
            "agent_id": None,
            "agent_name": None,
            "agent_role": None,
            "tool_id": None,
            "tool_name": None,
            "expected_behavior": "",
            "observed_behavior": "",
            "lep_injected": False,
            "lep_type": None,
            "lep_category": None,
            "lep_location": None,
            "lep_severity": None,
            "risk_tags": [],
            "caused_by_event": None,
            "depends_on": [],
            "propagates_to": [],
            "agent_id_from": None,
            "agent_name_from": None,
            "agent_id_to": None,
            "agent_name_to": None,
            "downstream_failure": False,
            "failure_type": None,
            "failure_event": None,
        }
        event = TraceEvent.from_dict(d)
        self.assertEqual(event.event_type, TraceEventType.TOOL_CALL)
        self.assertEqual(event.trace_id, "abc123a")


class TestTrace(unittest.TestCase):
    """Tests for Trace dataclass."""

    def _make_trace(self, variant: str = "a") -> Trace:
        events = [
            TraceEvent(
                trace_id=f"abc123{variant}",
                execution_id="abc123",
                event_id=i,
                timestamp="2026-01-01T00:00:00+00:00",
                event_type=TraceEventType.USER_INPUT if i == 1 else TraceEventType.TOOL_CALL,
                source="user" if i == 1 else "agent_001",
                target="system" if i == 1 else "mcp_list_directory",
                agent_name="researcher" if i > 1 else None,
            )
            for i in range(1, 4)
        ]
        return Trace(
            trace_id=f"abc123{variant}",
            execution_id="abc123",
            variant=TraceVariant.BENIGN if variant == "a" else TraceVariant.MALIGNANT,
            events=events,
        )

    def test_num_events(self):
        trace = self._make_trace()
        self.assertEqual(trace.num_events, 3)

    def test_is_benign(self):
        trace = self._make_trace("a")
        self.assertTrue(trace.is_benign)
        self.assertFalse(trace.is_malignant)

    def test_is_malignant(self):
        trace = self._make_trace("b")
        self.assertTrue(trace.is_malignant)
        self.assertFalse(trace.is_benign)

    def test_perturbation_events(self):
        events = [
            TraceEvent(
                trace_id="abc123b",
                execution_id="abc123",
                event_id=1,
                timestamp="2026-01-01T00:00:00+00:00",
                event_type=TraceEventType.TOOL_CALL,
                source="agent_001",
                target="mcp_list_directory",
                lep_injected=True,
            ),
            TraceEvent(
                trace_id="abc123b",
                execution_id="abc123",
                event_id=2,
                timestamp="2026-01-01T00:00:00+00:00",
                event_type=TraceEventType.TOOL_CALL,
                source="agent_001",
                target="mcp_read_file",
                lep_injected=False,
            ),
        ]
        trace = Trace(
            trace_id="abc123b",
            execution_id="abc123",
            variant=TraceVariant.MALIGNANT,
            events=events,
        )
        self.assertEqual(len(trace.perturbation_events), 1)

    def test_agent_ids(self):
        events = [
            TraceEvent(
                trace_id="abc123a",
                execution_id="abc123",
                event_id=1,
                timestamp="2026-01-01T00:00:00+00:00",
                event_type=TraceEventType.REASONING,
                source="agent_001",
                target="internal",
                agent_id="agent_001",
                agent_name="researcher",
            ),
            TraceEvent(
                trace_id="abc123a",
                execution_id="abc123",
                event_id=2,
                timestamp="2026-01-01T00:00:00+00:00",
                event_type=TraceEventType.REASONING,
                source="agent_002",
                target="internal",
                agent_id="agent_002",
                agent_name="analyst",
            ),
        ]
        trace = Trace(
            trace_id="abc123a",
            execution_id="abc123",
            variant=TraceVariant.BENIGN,
            events=events,
        )
        self.assertEqual(trace.agent_ids, ["agent_001", "agent_002"])

    def test_get_events_by_type(self):
        events = [
            TraceEvent(
                trace_id="abc123a",
                execution_id="abc123",
                event_id=1,
                timestamp="2026-01-01T00:00:00+00:00",
                event_type=TraceEventType.TOOL_CALL,
                source="agent_001",
                target="mcp_list_directory",
            ),
            TraceEvent(
                trace_id="abc123a",
                execution_id="abc123",
                event_id=2,
                timestamp="2026-01-01T00:00:00+00:00",
                event_type=TraceEventType.TOOL_RESULT,
                source="mcp_list_directory",
                target="agent_001",
            ),
        ]
        trace = Trace(
            trace_id="abc123a",
            execution_id="abc123",
            variant=TraceVariant.BENIGN,
            events=events,
        )
        tool_calls = trace.get_events_by_type(TraceEventType.TOOL_CALL)
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].event_type, TraceEventType.TOOL_CALL)

    def test_to_dict_and_back(self):
        trace = self._make_trace("a")
        d = trace.to_dict()
        restored = Trace.from_dict(d)
        self.assertEqual(restored.trace_id, trace.trace_id)
        self.assertEqual(restored.execution_id, trace.execution_id)
        self.assertEqual(restored.num_events, trace.num_events)
        self.assertEqual(restored.variant, trace.variant)


if __name__ == "__main__":
    unittest.main()
