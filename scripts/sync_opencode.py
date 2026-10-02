#!/usr/bin/env python3
"""Generate the OpenCode subagent files from the Claude Code ones.

The two harnesses use different frontmatter (Claude: tools/model/maxTurns/effort;
OpenCode: mode/permission), but the prompt body is identical. `agents/` is the
source of truth; `opencode/agents/` is generated from it.

    python scripts/sync_opencode.py          # write
    python scripts/sync_opencode.py --check  # fail if out of date (used in CI)

The generated agents set no `model`, so they run on the model of the session that
spawns them. To make the judge differ from the candidates (recommended), add
`model: provider/model-id` to `dd-judge.md` after installing.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "agents"
DST = ROOT / "opencode" / "agents"

# OpenCode permissions that keep a subagent read-only.
PERMISSIONS = {"edit": "deny", "bash": "deny", "webfetch": "deny"}


def render(source: Path) -> str:
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", source.read_text(encoding="utf-8"), re.S)
    if not m:
        raise SystemExit(f"{source}: no frontmatter")
    meta = yaml.safe_load(m.group(1))
    body = m.group(2).lstrip("\n").replace("\r\n", "\n")
    perms = "\n".join(f"  {k}: {v}" for k, v in PERMISSIONS.items())
    # json.dumps yields a valid YAML double-quoted scalar, so colons and quotes are safe.
    return (
        "---\n"
        f"description: {json.dumps(meta['description'])}\n"
        "mode: subagent\n"
        "permission:\n"
        f"{perms}\n"
        "---\n"
        f"{body}"
    )


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale: list[str] = []
    DST.mkdir(parents=True, exist_ok=True)
    wanted = {p.name for p in SRC.glob("*.md")}
    for source in sorted(SRC.glob("*.md")):
        out = DST / source.name
        text = render(source)
        current = out.read_text(encoding="utf-8").replace("\r\n", "\n") if out.exists() else None
        if current != text:
            stale.append(out.relative_to(ROOT).as_posix())
            if not check:
                out.write_text(text, encoding="utf-8", newline="\n")
    for extra in DST.glob("*.md"):
        if extra.name not in wanted:
            stale.append(extra.relative_to(ROOT).as_posix() + " (no Claude source)")
    if check:
        if stale:
            print("out of date; run `python scripts/sync_opencode.py`:\n  " + "\n  ".join(stale))
            return 1
        print("opencode/agents is in sync with agents/")
        return 0
    print("wrote: " + (", ".join(stale) if stale else "nothing (already in sync)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
