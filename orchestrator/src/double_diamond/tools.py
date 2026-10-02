"""Sandboxed, read-only local tools for the discovery step.

Everything is confined to one root directory. Paths are resolved (following
symlinks) and refused if they land outside it, and content is size-capped, so
an injected instruction in a file cannot read secrets elsewhere on disk.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 40_000
MAX_LIST_ENTRIES = 200
MAX_GREP_HITS = 60
MAX_GREP_FILE_BYTES = 400_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}


class ToolError(Exception):
    pass


class LocalTools:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f"working directory does not exist: {root}")

    # -- definitions --------------------------------------------------------

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "read_file",
                "description": "Read a text file under the working directory. Read-only.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative path."}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_dir",
                "description": "List a directory under the working directory. Read-only.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative path; '.' for the root."}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "search",
                "description": "Regex search over text files under a directory. Read-only.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Python regular expression."},
                        "path": {"type": "string", "description": "Relative directory; '.' for the root."},
                    },
                    "required": ["pattern", "path"],
                    "additionalProperties": False,
                },
            },
        ]

    # -- dispatch -----------------------------------------------------------

    def run(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Execute a tool. Returns (text, is_error); never raises."""
        try:
            if name == "read_file":
                return self.read_file(str(args["path"])), False
            if name == "list_dir":
                return self.list_dir(str(args["path"])), False
            if name == "search":
                return self.search(str(args["pattern"]), str(args["path"])), False
            return f"unknown tool: {name}", True
        except ToolError as e:
            return str(e), True
        except KeyError as e:
            return f"missing argument: {e}", True
        except OSError as e:
            return f"filesystem error: {e.strerror or e}", True

    # -- implementations ----------------------------------------------------

    def _resolve(self, rel: str) -> Path:
        candidate = (self.root / rel).resolve()
        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise ToolError("path is outside the working directory")
        return candidate

    @staticmethod
    def _looks_binary(sample: bytes) -> bool:
        return b"\x00" in sample

    def read_file(self, rel: str) -> str:
        p = self._resolve(rel)
        if not p.is_file():
            raise ToolError("not a file")
        data = p.read_bytes()[: MAX_FILE_BYTES + 1]
        if self._looks_binary(data[:2048]):
            raise ToolError("binary file skipped")
        text = data[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
        if len(data) > MAX_FILE_BYTES:
            text += "\n[truncated]"
        return text

    def list_dir(self, rel: str) -> str:
        p = self._resolve(rel)
        if not p.is_dir():
            raise ToolError("not a directory")
        entries = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if child.name in SKIP_DIRS:
                continue
            entries.append(child.name + ("/" if child.is_dir() else ""))
            if len(entries) >= MAX_LIST_ENTRIES:
                entries.append("[more entries omitted]")
                break
        return "\n".join(entries) or "[empty]"

    def search(self, pattern: str, rel: str) -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"invalid regex: {e}") from e
        base = self._resolve(rel)
        if not base.is_dir():
            raise ToolError("not a directory")
        hits: list[str] = []
        for path in sorted(base.rglob("*")):
            if len(hits) >= MAX_GREP_HITS:
                hits.append("[more matches omitted]")
                break
            if any(part in SKIP_DIRS for part in path.relative_to(base).parts):
                continue
            try:
                resolved = path.resolve()
                if not resolved.is_relative_to(self.root) or not resolved.is_file():
                    continue
                if resolved.stat().st_size > MAX_GREP_FILE_BYTES:
                    continue
                raw = resolved.read_bytes()
            except OSError:
                continue
            if self._looks_binary(raw[:2048]):
                continue
            for lineno, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    shown = line.strip()[:200]
                    hits.append(f"{path.relative_to(self.root).as_posix()}:{lineno}: {shown}")
                    if len(hits) >= MAX_GREP_HITS:
                        break
        return "\n".join(hits) or "[no matches]"
