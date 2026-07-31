"""Tests for the agentgraph.parser module."""

import json
import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from agentgraph.parser import JSONLTraceParser, ParseResult
from agentgraph.trace import TraceEvent, TraceEventType, TraceVariant


class TestJSONLTraceParser(unittest.TestCase):
    """Tests for JSONL trace parser."""

    def setUp(self):
        """Create a temporary directory with test trace files."""
        self.tmpdir = Path(tempfile.mkdtemp())
        self._create_test_traces()

    def _create_test_traces(self):
        """Create test JSONL trace files."""
        # Benign trace
        a_events = [
            {
                "trace_id": "abc123a",
                "execution_id": "abc123",
                "event_id": 1,
                "timestamp": "2026-01-01T00:00:00+00:00",
                "event_type": "user_input",
                "source": "user",
                "target": "multi_agent_system",
                "input_summary": "Test task",
                "output_summary": "",
            },
            {
                "trace_id": "abc123a",
                "execution_id": "abc123",
                "event_id": 2,
                "timestamp": "2026-01-01T00:00:01+00:00",
                "event_type": "tool_call",
                "source": "agent_001",
                "target": "mcp_list_directory",
                "input_summary": '{"path": "."}',
                "output_summary": "",
                "agent_name": "researcher",
                "tool_name": "list_directory",
            },
            {
                "trace_id": "abc123a",
                "execution_id": "abc123",
                "event_id": 3,
                "timestamp": "2026-01-01T00:00:02+00:00",
                "event_type": "tool_result",
                "source": "mcp_list_directory",
                "target": "agent_001",
                "input_summary": "",
                "output_summary": "[DIR] documents [DIR] notes",
                "agent_name": "researcher",
                "tool_name": "list_directory",
            },
        ]

        # Malignant trace
        b_events = [
            {
                "trace_id": "abc123b",
                "execution_id": "abc123",
                "event_id": 1,
                "timestamp": "2026-01-01T00:00:10+00:00",
                "event_type": "user_input",
                "source": "user",
                "target": "multi_agent_system",
                "input_summary": "Test task",
                "output_summary": "",
            },
            {
                "trace_id": "abc123b",
                "execution_id": "abc123",
                "event_id": 2,
                "timestamp": "2026-01-01T00:00:11+00:00",
                "event_type": "tool_call",
                "source": "agent_001",
                "target": "mcp_list_directory",
                "input_summary": '{"path": "."}',
                "output_summary": "",
                "agent_name": "researcher",
                "tool_name": "list_directory",
                "lep_injected": True,
                "lep_type": "FC1.3 Step Repetition",
            },
            {
                "trace_id": "abc123b",
                "execution_id": "abc123",
                "event_id": 3,
                "timestamp": "2026-01-01T00:00:12+00:00",
                "event_type": "tool_result",
                "source": "mcp_list_directory",
                "target": "agent_001",
                "input_summary": "",
                "output_summary": "[DIR] documents [DIR] notes",
                "agent_name": "researcher",
                "tool_name": "list_directory",
            },
        ]

        with open(self.tmpdir / "trace_abc123a.jsonl", "w") as f:
            for event in a_events:
                f.write(json.dumps(event) + "\n")

        with open(self.tmpdir / "trace_abc123b.jsonl", "w") as f:
            for event in b_events:
                f.write(json.dumps(event) + "\n")

    def test_parse_file(self):
        parser = JSONLTraceParser(self.tmpdir)
        trace = parser.parse_file(self.tmpdir / "trace_abc123a.jsonl")

        self.assertEqual(trace.trace_id, "trace_abc123a")
        self.assertEqual(trace.execution_id, "abc123")
        self.assertEqual(trace.variant, TraceVariant.BENIGN)
        self.assertEqual(trace.num_events, 3)

    def test_parse_malignant_file(self):
        parser = JSONLTraceParser(self.tmpdir)
        trace = parser.parse_file(self.tmpdir / "trace_abc123b.jsonl")

        self.assertEqual(trace.variant, TraceVariant.MALIGNANT)
        self.assertEqual(trace.num_events, 3)
        self.assertTrue(trace.events[1].lep_injected)

    def test_parse_directory(self):
        parser = JSONLTraceParser(self.tmpdir)
        traces = parser.parse_directory()

        self.assertEqual(len(traces), 2)
        trace_ids = {t.trace_id for t in traces}
        self.assertIn("trace_abc123a", trace_ids)
        self.assertIn("trace_abc123b", trace_ids)

    def test_get_pairs(self):
        parser = JSONLTraceParser(self.tmpdir)
        pairs = parser.get_pairs()

        self.assertEqual(len(pairs), 1)
        benign, malignant = pairs[0]
        self.assertEqual(benign.execution_id, "abc123")
        self.assertEqual(malignant.execution_id, "abc123")
        self.assertTrue(benign.is_benign)
        self.assertTrue(malignant.is_malignant)

    def test_parse_missing_file(self):
        parser = JSONLTraceParser(self.tmpdir)
        with self.assertRaises(FileNotFoundError):
            parser.parse_file(self.tmpdir / "nonexistent.jsonl")

    def test_parse_invalid_json(self):
        bad_file = self.tmpdir / "trace_bad.jsonl"
        bad_file.write_text("not valid json\n")

        parser = JSONLTraceParser(self.tmpdir)
        with self.assertRaises(ValueError):
            parser.parse_file(bad_file)


if __name__ == "__main__":
    unittest.main()
