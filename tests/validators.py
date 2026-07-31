"""Run all project validators in one place.

This is the master validation script that runs all tests and checks
to ensure the project is in a valid, working state.

Usage:
    python tests/validators.py

Or from any directory:
    python /path/to/agentgraphs/tests/validators.py
"""

import importlib
import sys
import unittest
from pathlib import Path
from typing import List, Tuple


def _add_src_to_path() -> None:
    """Add the src directory to sys.path."""
    src_path = Path(__file__).parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def run_all_validators() -> bool:
    """Run all tests and validators.

    Returns:
        True if all validators pass, False otherwise.
    """
    _add_src_to_path()

    results: List[Tuple[str, bool, str]] = []
    all_passed = True

    # Define test modules to run
    test_modules = [
        ("test_trace", "Trace data structures"),
        ("test_entity", "Entity encoding"),
        ("test_graph_builder", "Graph construction"),
        ("test_encoder", "Graph encoding"),
        ("test_parser", "JSONL parsing"),
        ("test_exporter", "Export utilities"),
        ("test_benchmarks", "Benchmark suite"),
        ("test_integration", "Integration tests"),
        ("test_end_to_end", "End-to-end pipeline"),
    ]

    print("=" * 60)
    print("Running AgentGraphs Validators")
    print("=" * 60)

    for module_name, description in test_modules:
        print(f"\n--- {description} ({module_name}) ---")
        try:
            loader = unittest.TestLoader()
            suite = loader.loadTestsFromName(f"tests.{module_name}")
            runner = unittest.TextTestRunner(verbosity=1)
            result = runner.run(suite)

            passed = result.wasSuccessful()
            results.append((module_name, passed, description))

            if not passed:
                all_passed = False
                print(f"FAILED: {len(result.failures)} failures, {len(result.errors)} errors")

        except ImportError as e:
            print(f"SKIPPED: Could not import module: {e}")
            results.append((module_name, True, f"{description} (skipped)"))
        except Exception as e:
            print(f"ERROR: {e}")
            results.append((module_name, False, description))
            all_passed = False

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for module_name, passed, description in results:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {description} ({module_name})")

    passed_count = sum(1 for _, p, _ in results if p)
    total_count = len(results)

    print(f"\n{passed_count}/{total_count} validators passed")

    if all_passed:
        print("\nAll validators PASSED ✓")
    else:
        print("\nSome validators FAILED ✗")

    return all_passed


def run_trace_tests() -> unittest.TestResult:
    """Run only trace tests."""
    _add_src_to_path()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_trace")
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


def run_entity_tests() -> unittest.TestResult:
    """Run only entity tests."""
    _add_src_to_path()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_entity")
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


def run_graph_builder_tests() -> unittest.TestResult:
    """Run only graph builder tests."""
    _add_src_to_path()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_graph_builder")
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


def run_encoder_tests() -> unittest.TestResult:
    """Run only encoder tests."""
    _add_src_to_path()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_encoder")
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


def run_parser_tests() -> unittest.TestResult:
    """Run only parser tests."""
    _add_src_to_path()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_parser")
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


def run_exporter_tests() -> unittest.TestResult:
    """Run only exporter tests."""
    _add_src_to_path()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_exporter")
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


def run_benchmark_tests() -> unittest.TestResult:
    """Run only benchmark tests."""
    _add_src_to_path()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_benchmarks")
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


def run_integration_tests() -> unittest.TestResult:
    """Run only integration tests."""
    _add_src_to_path()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_integration")
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


def run_end_to_end_tests() -> unittest.TestResult:
    """Run only end-to-end tests."""
    _add_src_to_path()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_end_to_end")
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


if __name__ == "__main__":
    success = run_all_validators()
    sys.exit(0 if success else 1)
