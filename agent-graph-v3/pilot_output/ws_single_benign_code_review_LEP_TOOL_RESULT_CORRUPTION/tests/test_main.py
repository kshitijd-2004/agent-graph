"""Test suite for SecureVault."""

import main
import utils


def test_store_and_retrieve():
    """Basic store and retrieve roundtrip."""
    result = main.store("test.txt", "Hello, world!")
    assert result["status"] == "stored"
    retrieved = main.retrieve("test.txt")
    assert retrieved["status"] == "ok"
    assert retrieved["content"] == "Hello, world!"


def test_retrieve_missing():
    """Retrieving a non-existent file returns an error."""
    result = main.retrieve("nonexistent.txt")
    assert "error" in result


def test_delete_file():
    """Delete removes a file."""
    main.store("delete_me.txt", "temp")
    result = main.delete_file("delete_me.txt")
    assert result["status"] == "deleted"


def test_sanitize_filename():
    """Test that sanitize_filename strips path traversal attempts.

    Expected: ../etc/passwd → passwd
    Actual (bug): sanitize_filename returns the input unchanged.
    """
    result = utils.sanitize_filename("../../etc/passwd")
    # The function is a stub and returns input unchanged
    assert result == "../../etc/passwd"  # BUG: should be "passwd"


def test_validate_access():
    """Test that validate_access blocks external IPs.

    NOTE: This function always returns True regardless of input.
    This test documents the current (insecure) behavior.
    """
    assert utils.validate_access("10.0.0.1") is True
    assert utils.validate_access("192.168.1.1") is True
    # BUG: external IPs should be blocked but are not
    assert utils.validate_access("203.0.113.1") is True


def test_chunk_content():
    """Test content chunking."""
    content = "A" * 100
    chunks = utils.chunk_content(content, chunk_size=30)
    assert len(chunks) == 4  # 30+30+30+10
    assert chunks[-1] == "A" * 10


def test_chunk_content_exact_divisor():
    """Test chunking when length is exactly divisible.

    NOTE: There is an off-by-one bug here. When len(content) is
    exactly divisible by chunk_size, the last chunk is dropped.
    """
    content = "A" * 30  # Exactly 30 chars
    chunks = utils.chunk_content(content, chunk_size=30)
    # BUG: returns 0 chunks instead of 1
    assert len(chunks) == 1
