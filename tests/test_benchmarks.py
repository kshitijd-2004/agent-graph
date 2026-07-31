"""Tests for the benchmarks module."""

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from benchmarks.benchmark import DocumentProvider, MockLLMBackend, TaskCategory, TraceConfig
from benchmarks.tasks.financial import FinancialTask
from benchmarks.tasks.code_review import CodeReviewTask
from benchmarks.tasks.research import ResearchTask
from benchmarks.tasks.competitive_intelligence import CompetitiveIntelligenceTask


class TestMockLLMBackend(unittest.TestCase):
    """Tests for the mock LLM backend."""

    def test_initialization(self):
        llm = MockLLMBackend("test-model")
        self.assertEqual(llm.model_name, "test-model")

    def test_reset(self):
        llm = MockLLMBackend()
        llm.reset(task="test task", agent_name="analyst", mcp_tools=["tool1", "tool2"])
        self.assertEqual(llm._task, "test task")
        self.assertEqual(llm._agent_name, "analyst")
        self.assertEqual(llm._mcp_tools, ["tool1", "tool2"])

    def test_generate_returns_json(self):
        llm = MockLLMBackend()
        llm.reset(task="test", agent_name="researcher")
        result = llm._generate("some prompt")
        import json
        parsed = json.loads(result)
        self.assertIn("reasoning", parsed)
        self.assertIn("action", parsed)

    def test_parse_valid(self):
        llm = MockLLMBackend()
        raw = '{"reasoning": "test", "action": "list_directory", "action_input": "{}", "final_response": ""}'
        result = llm._parse(raw, ["list_directory", "final"])
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "list_directory")

    def test_parse_invalid(self):
        llm = MockLLMBackend()
        result = llm._parse("not json", ["list_directory"])
        self.assertIsNone(result)


class TestDocumentProvider(unittest.TestCase):
    """Tests for DocumentProvider."""

    def test_add_and_get(self):
        provider = DocumentProvider()
        provider.add_document("test", "test content")
        self.assertEqual(provider.get("test"), "test content")

    def test_get_missing(self):
        provider = DocumentProvider()
        self.assertEqual(provider.get("missing"), "")

    def test_list_documents(self):
        provider = DocumentProvider()
        provider.add_document("doc1", "content1")
        provider.add_document("doc2", "content2")
        docs = provider.list_documents()
        self.assertEqual(len(docs), 2)
        self.assertIn("doc1", docs)
        self.assertIn("doc2", docs)


class TestFinancialTask(unittest.TestCase):
    """Tests for FinancialTask."""

    def setUp(self):
        self.task = FinancialTask(Path("/tmp/test_workspace"))
        self.llm = MockLLMBackend()

    def test_task_name(self):
        self.assertEqual(FinancialTask.TASK_NAME, "financial_analysis")
        self.assertEqual(FinancialTask.TASK_CATEGORY, TaskCategory.FINANCIAL)

    def test_get_tasks(self):
        tasks = self.task.get_tasks()
        self.assertGreater(len(tasks), 0)
        self.assertIsInstance(tasks[0], str)

    def test_get_lep_configs(self):
        configs = self.task.get_lep_configs()
        self.assertIn("FC2.2", configs)
        self.assertIn("FC1.3", configs)

    def test_generate_traces(self):
        traces = self.task.generate_traces(self.llm)
        self.assertIn("benign", traces)
        self.assertIn("malignant", traces)
        self.assertGreater(traces["benign"].num_events, 0)
        self.assertGreater(traces["malignant"].num_events, 0)

    def test_generate_traces_same_execution_id(self):
        traces = self.task.generate_traces(self.llm)
        benign = traces["benign"]
        malignant = traces["malignant"]
        # Different execution_ids since we generate new ones each time
        # But same trace_id prefix
        self.assertEqual(benign.execution_id, malignant.execution_id)


class TestCodeReviewTask(unittest.TestCase):
    """Tests for CodeReviewTask."""

    def test_task_name(self):
        self.assertEqual(CodeReviewTask.TASK_NAME, "code_review")

    def test_get_tasks(self):
        task = CodeReviewTask(Path("/tmp/test_workspace"))
        tasks = task.get_tasks()
        self.assertGreater(len(tasks), 0)


class TestResearchTask(unittest.TestCase):
    """Tests for ResearchTask."""

    def test_task_name(self):
        self.assertEqual(ResearchTask.TASK_NAME, "research")

    def test_get_lep_configs(self):
        task = ResearchTask(Path("/tmp/test_workspace"))
        configs = task.get_lep_configs()
        self.assertIn("FC2.3", configs)


class TestCompetitiveIntelligenceTask(unittest.TestCase):
    """Tests for CompetitiveIntelligenceTask."""

    def test_task_name(self):
        self.assertEqual(CompetitiveIntelligenceTask.TASK_NAME, "competitive_intelligence")

    def test_get_tasks(self):
        task = CompetitiveIntelligenceTask(Path("/tmp/test_workspace"))
        tasks = task.get_tasks()
        self.assertGreater(len(tasks), 0)


if __name__ == "__main__":
    unittest.main()
