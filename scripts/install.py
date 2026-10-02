#!/usr/bin/env python3
"""Install the double-diamond skill, subagents and command into Claude Code and/or OpenCode.

    python scripts/install.py                   # both tools
    python scripts/install.py --claude          # Claude Code only
    python scripts/install.py --opencode        # OpenCode only
    python scripts/install.py --dry-run         # show what would happen
    python scripts/install.py --force           # overwrite files that differ
    python scripts/install.py --uninstall       # remove what this script installs

Where things go:

  Claude Code   ~/.claude/skills/double-diamond/        the skill and its references
                ~/.claude/agents/dd-*.md                 read-only subagents
  OpenCode      ~/.config/opencode/agents/dd-*.md        read-only subagents (OpenCode format)
                ~/.config/opencode/commands/double-diamond.md   the /double-diamond command

OpenCode also reads ~/.claude/skills, so when both are installed the skill is copied once. With
--opencode alone it goes to ~/.config/opencode/skills. An existing `command` or `agent`
directory is used in place of `commands` or `agents`. Files that already exist and differ are
left alone unless you pass --force. Start a new session afterwards.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = "double-diamond"


def _norm(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    if path.is_file():
        h.update(_norm(path.read_bytes()))
    else:
        for f in sorted(p for p in path.rglob("*") if p.is_file()):
            h.update(f.relative_to(path).as_posix().encode())
            h.update(_norm(f.read_bytes()))
    return h.hexdigest()


def same(src: Path, dst: Path) -> bool:
    return dst.exists() and src.is_dir() == dst.is_dir() and _digest(src) == _digest(dst)


def pick(base: Path, singular: str, plural: str) -> Path:
    """Use the directory that already exists, else the documented plural form."""
    return base / singular if (base / singular).is_dir() else base / plural


@dataclass
class Item:
    label: str
    src: Path
    dst: Path


def plan(claude: Path | None, opencode: Path | None) -> list[Item]:
    items: list[Item] = []
    if claude is not None:
        items.append(Item("claude skill", ROOT / "skills" / SKILL, claude / "skills" / SKILL))
        for a in sorted((ROOT / "agents").glob("dd-*.md")):
            items.append(Item(f"claude agent {a.stem}", a, claude / "agents" / a.name))
    if opencode is not None:
        if claude is None:  # OpenCode reads ~/.claude/skills itself; only copy when that is not installed
            items.append(Item("opencode skill", ROOT / "skills" / SKILL, opencode / "skills" / SKILL))
        agents_dir = pick(opencode, "agent", "agents")
        for a in sorted((ROOT / "opencode" / "agents").glob("dd-*.md")):
            items.append(Item(f"opencode agent {a.stem}", a, agents_dir / a.name))
        cmd = ROOT / "opencode" / "commands" / f"{SKILL}.md"
        items.append(Item("opencode command", cmd, pick(opencode, "command", "commands") / cmd.name))
    return items


def put(item: Item, force: bool, dry: bool) -> str:
    if not item.src.exists():
        return f"MISSING SOURCE {item.src}"
    if same(item.src, item.dst):
        return "up to date"
    exists = item.dst.exists()
    if exists and not force:
        return "skipped: already exists and differs (use --force to overwrite)"
    if dry:
        return "would update" if exists else "would install"
    item.dst.parent.mkdir(parents=True, exist_ok=True)
    if exists:
        shutil.rmtree(item.dst) if item.dst.is_dir() else item.dst.unlink()
    if item.src.is_dir():
        shutil.copytree(item.src, item.dst, ignore=shutil.ignore_patterns("__pycache__"))
    else:
        shutil.copyfile(item.src, item.dst)
    return "updated" if exists else "installed"


def remove(item: Item, dry: bool) -> str:
    if not item.dst.exists():
        return "not installed"
    if dry:
        return "would remove"
    shutil.rmtree(item.dst) if item.dst.is_dir() else item.dst.unlink()
    return "removed"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--claude", action="store_true", help="Install for Claude Code.")
    p.add_argument("--opencode", action="store_true", help="Install for OpenCode.")
    p.add_argument("--claude-dir", type=Path, default=Path.home() / ".claude")
    p.add_argument("--opencode-dir", type=Path, default=Path.home() / ".config" / "opencode")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--uninstall", action="store_true")
    args = p.parse_args(argv)

    both = not (args.claude or args.opencode)
    items = plan(
        args.claude_dir if (args.claude or both) else None,
        args.opencode_dir if (args.opencode or both) else None,
    )

    bad = False
    for item in items:
        status = remove(item, args.dry_run) if args.uninstall else put(item, args.force, args.dry_run)
        bad |= status.startswith("MISSING")
        print(f"{item.label:<28} {status}\n{'':<28} {item.dst}")
    if not args.dry_run and not args.uninstall:
        print("\nStart a new session in each tool to pick these up.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
