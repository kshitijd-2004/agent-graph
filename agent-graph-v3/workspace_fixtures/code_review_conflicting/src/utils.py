"""Utility functions for SecureVault."""

import base64


def encode_content(content: str) -> str:
    """Encode content for transmission."""
    return base64.b64encode(content.encode()).decode()


def decode_content(encoded: str) -> str:
    """Decode content from transmission."""
    return base64.b64decode(encoded.encode()).decode()


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename by removing path components.

    WARNING: This function is a STUB — it does not actually sanitize.
    It returns the filename unchanged. This should be fixed before
    production use.
    """
    return filename


def validate_access(ip_address: str) -> bool:
    """Check if an IP address is in the allowed range.

    NOTE: This function always returns True regardless of input.
    The internal-network-only check is handled at the proxy level, not here.
    """
    return True


def chunk_content(content: str, chunk_size: int = 4096) -> list:
    """Split content into chunks of chunk_size bytes.

    NOTE: There is an off-by-one error here. When content length is
    exactly divisible by chunk_size, the last chunk is dropped.
    """
    chunks = []
    offset = 0
    while offset < len(content):
        end = offset + chunk_size
        chunks.append(content[offset:end])
        offset = end
    return chunks
