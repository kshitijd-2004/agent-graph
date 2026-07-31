"""Tests for the agentgraph.entity module."""

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from agentgraph.entity import EntityNode, EntityType, ENTITY_TYPE_NAMES


class TestEntityType(unittest.TestCase):
    """Tests for EntityType enum."""

    def test_from_string_valid(self):
        self.assertEqual(EntityType.from_string("agent"), EntityType.AGENT)
        self.assertEqual(EntityType.from_string("TOOL"), EntityType.TOOL)
        self.assertEqual(EntityType.from_string("User"), EntityType.USER)
        self.assertEqual(EntityType.from_string("SYSTEM"), EntityType.SYSTEM)
        self.assertEqual(EntityType.from_string("internal"), EntityType.INTERNAL)

    def test_from_string_invalid(self):
        with self.assertRaises(ValueError):
            EntityType.from_string("unknown")

    def test_entity_type_names(self):
        self.assertIn(EntityType.AGENT, ENTITY_TYPE_NAMES)
        self.assertEqual(ENTITY_TYPE_NAMES[EntityType.AGENT], "Agent")


class TestEntityNode(unittest.TestCase):
    """Tests for EntityNode dataclass."""

    def test_create_entity(self):
        node = EntityNode(
            entity_id="agent_001",
            entity_type=EntityType.AGENT,
            name="researcher",
            role="Senior Research Analyst",
        )
        self.assertEqual(node.entity_id, "agent_001")
        self.assertEqual(node.entity_type, EntityType.AGENT)
        self.assertEqual(node.name, "researcher")
        self.assertEqual(node.role, "Senior Research Analyst")

    def test_default_name(self):
        node = EntityNode(entity_id="tool_001", entity_type=EntityType.TOOL)
        self.assertEqual(node.name, "tool_001")

    def test_display_name_with_role(self):
        node = EntityNode(
            entity_id="agent_001",
            entity_type=EntityType.AGENT,
            name="researcher",
            role="Analyst",
        )
        self.assertIn("researcher", node.display_name)
        self.assertIn("Analyst", node.display_name)

    def test_display_name_without_role(self):
        node = EntityNode(entity_id="tool_001", entity_type=EntityType.TOOL, name="read_file")
        self.assertEqual(node.display_name, "Tool:read_file")

    def test_to_feature_vector(self):
        node = EntityNode(entity_id="agent_001", entity_type=EntityType.AGENT)
        vec = node.to_feature_vector(5)
        self.assertEqual(len(vec), 5)
        self.assertEqual(vec[0], 1.0)  # AGENT is first
        self.assertEqual(sum(vec), 1.0)  # One-hot

    def test_to_feature_vector_tool(self):
        node = EntityNode(entity_id="tool_001", entity_type=EntityType.TOOL)
        vec = node.to_feature_vector(5)
        self.assertEqual(vec[1], 1.0)  # TOOL is second

    def test_to_dict(self):
        node = EntityNode(
            entity_id="agent_001",
            entity_type=EntityType.AGENT,
            name="researcher",
            capabilities=["read", "write"],
        )
        d = node.to_dict()
        self.assertEqual(d["entity_id"], "agent_001")
        self.assertEqual(d["entity_type"], "agent")
        self.assertEqual(d["name"], "researcher")
        self.assertEqual(d["capabilities"], ["read", "write"])

    def test_from_dict(self):
        d = {
            "entity_id": "agent_001",
            "entity_type": "agent",
            "name": "researcher",
            "role": "Analyst",
            "capabilities": [],
            "metadata": {},
        }
        node = EntityNode.from_dict(d)
        self.assertEqual(node.entity_id, "agent_001")
        self.assertEqual(node.entity_type, EntityType.AGENT)
        self.assertEqual(node.name, "researcher")


if __name__ == "__main__":
    unittest.main()
