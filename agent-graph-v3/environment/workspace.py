"""Virtual workspace with file-based tool execution.

Provides the same tools as the original benchmark (list_directory,
read_text_file, write_file, search_files, create_directory) but
operates on a real directory on disk.

LEPs are now injected at the file level (poisoned content baked into
files before the trace runs), not via tool result corruption.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict


class Workspace:
    """File-based workspace that agents interact with via tools."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def execute(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Execute a tool against the workspace files."""
        if tool_name == "list_directory":
            return self._list_directory(args)
        elif tool_name == "read_text_file":
            return self._read_text_file(args)
        elif tool_name == "write_file":
            return self._write_file(args)
        elif tool_name == "search_files":
            return self._search_files(args)
        elif tool_name == "create_directory":
            return self._create_directory(args)
        elif tool_name == "write_memory":
            return self._write_memory(args)
        else:
            return f"Error: unknown tool '{tool_name}'"

    def _list_directory(self, args: Dict[str, Any]) -> str:
        path = args.get("path", ".")
        full = self._safe_path(path)
        if not full.exists():
            return f"Error: path '{path}' does not exist"
        entries = sorted(full.iterdir())
        lines = []
        for e in entries:
            kind = "[DIR]" if e.is_dir() else "[FILE]"
            lines.append(f"{kind} {e.name}")
        return "\n".join(lines) if lines else "(empty directory)"

    def _read_text_file(self, args: Dict[str, Any]) -> str:
        path = args.get("path", "")
        full = self._safe_path(path)
        if not full.exists():
            return f"Error: file '{path}' not found"
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"Error reading '{path}': {e}"
        return content  # full content — no truncation

    def _write_file(self, args: Dict[str, Any]) -> str:
        path = args.get("path", "")
        content = args.get("content", "")
        if not content:
            return f"Error: write_file requires non-empty 'content'. Got: {repr(content)}. Include the file content to write."
        full = self._safe_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        byte_count = len(content.encode("utf-8"))
        return f"Write completed successfully. {byte_count} bytes saved to {path}."

    def _search_files(self, args: Dict[str, Any]) -> str:
        path = args.get("path", ".")
        pattern = args.get("pattern", "*")
        full = self._safe_path(path)
        if not full.exists():
            return f"Error: path '{path}' does not exist"
        matches = list(full.rglob(pattern))
        names = [str(m.relative_to(self.root)) for m in matches[:50]]
        return "\n".join(names) if names else "(no matches)"

    def _create_directory(self, args: Dict[str, Any]) -> str:
        path = args.get("path", "")
        full = self._safe_path(path)
        full.mkdir(parents=True, exist_ok=True)
        return f"Created directory: {path}"

    def _write_memory(self, args: Dict[str, Any]) -> str:
        # Memory writes are handled by the memory layer, not filesystem
        return "Memory record stored."

    def _safe_path(self, relative: str) -> Path:
        """Resolve a relative path within the workspace, preventing escapes."""
        p = self.root / relative
        p = p.resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError(f"Path escapes workspace: {relative}")
        return p

    def reset_from_base(self, base_path: Path) -> None:
        """Reset workspace to a clean copy of the base directory."""
        import shutil
        if self.root.exists():
            shutil.rmtree(self.root)
        shutil.copytree(base_path, self.root)
