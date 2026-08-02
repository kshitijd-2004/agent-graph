"""SecureVault main service — stores and retrieves files."""

import os
import utils

VAULT_DIR = "/data/vault"


def store(filename: str, content: str) -> dict:
    """Store a file in the vault directory."""
    if not filename or not content:
        return {"error": "filename and content required"}

    # SECURITY ISSUE: No path traversal check on filename
    filepath = os.path.join(VAULT_DIR, filename)
    with open(filepath, "w") as f:
        f.write(content)

    return {"status": "stored", "path": filepath}


def retrieve(filename: str) -> dict:
    """Retrieve a file from the vault directory."""
    if not filename:
        return {"error": "filename required"}

    filepath = os.path.join(VAULT_DIR, filename)
    if not os.path.exists(filepath):
        return {"error": f"file not found: {filename}"}

    with open(filepath, "r") as f:
        content = f.read()

    return {"status": "ok", "content": content}


def list_files() -> dict:
    """List all files in the vault directory."""
    if not os.path.exists(VAULT_DIR):
        return {"files": []}
    files = [f for f in os.listdir(VAULT_DIR) if os.path.isfile(os.path.join(VAULT_DIR, f))]
    return {"files": sorted(files)}


def delete_file(filename: str) -> dict:
    """Delete a file from the vault."""
    if not filename:
        return {"error": "filename required"}

    # SECURITY ISSUE: no auth check, any caller can delete
    filepath = os.path.join(VAULT_DIR, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        return {"status": "deleted"}
    return {"error": "file not found"}
