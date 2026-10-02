#!/usr/bin/env python3
"""Offline checks for the plugin, skill and agent files.

This approximates `claude plugin validate` using the documented schemas. It is
not a replacement: run the real command too when the Claude Code CLI is
available (`claude plugin validate .`).

Exit status is 1 if any error is found.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent

SKILL_KEYS = {
    "name", "description", "when_to_use", "argument-hint", "arguments", "disable-model-invocation",
    "user-invocable", "allowed-tools", "disallowed-tools", "model", "effort", "context", "agent",
    "background", "hooks", "paths", "shell", "metadata", "license", "compatibility",
}
AGENT_KEYS = {
    "name", "description", "tools", "disallowedTools", "model", "permissionMode", "maxTurns", "skills",
    "mcpServers", "hooks", "memory", "background", "omitClaudeMd", "effort", "isolation", "color",
    "initialPrompt", "experimental",
}
MODEL_ALIASES = {"sonnet", "opus", "haiku", "fable", "inherit"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
RESERVED_PREFIXES = ("claude-", "anthropic-", "anthropics-", "cc-plugin-")
RESERVED_NAMES = {"claude", "anthropic", "anthropics", "claude-code", "claude-mods"}
RESERVED_MARKETPLACES = {
    "claude-code-marketplace", "claude-code-plugins", "claude-plugins-official", "anthropic-marketplace",
    "anthropic-plugins", "agent-skills", "anthropic-agent-skills", "life-sciences", "knowledge-work-plugins",
    "inline", "builtin", "skills-dir", "synced", "claude-plugin-test", "npm", "pip", "uv", "cargo", "github", "gh",
}
PLUGIN_ID_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        err(f"{path.relative_to(ROOT)}: missing")
    except ValueError as e:
        err(f"{path.relative_to(ROOT)}: invalid JSON ({e})")
    return None


def frontmatter(path: Path) -> tuple[dict, str] | None:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", text, re.S)
    if not m:
        err(f"{path.relative_to(ROOT)}: no YAML frontmatter")
        return None
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        err(f"{path.relative_to(ROOT)}: frontmatter is not valid YAML ({e})")
        return None
    if not isinstance(data, dict):
        err(f"{path.relative_to(ROOT)}: frontmatter must be a mapping")
        return None
    return data, m.group(2)


def check_plugin() -> str | None:
    path = ROOT / ".claude-plugin" / "plugin.json"
    data = load_json(path)
    if data is None:
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name:
        err("plugin.json: name is required")
        return None
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
        warn(f"plugin.json: name {name!r} is not kebab-case")
    if name.lower().startswith(RESERVED_PREFIXES) or name.lower() in RESERVED_NAMES:
        err(f"plugin.json: name {name!r} is reserved: it passes as one of Anthropic's own")
    for field in ("version", "description", "author"):
        if field not in data:
            warn(f"plugin.json: missing {field}")
    if "author" in data and not (isinstance(data["author"], dict) and data["author"].get("name")):
        err("plugin.json: author must be an object with a name")
    for field in ("homepage",):
        if field in data and not re.match(r"^https?://", str(data[field])):
            err(f"plugin.json: {field} must be a URL")
    for key in ("skills", "agents", "commands", "hooks"):
        paths = data.get(key)
        if isinstance(paths, str):
            paths = [paths]
        for p in paths if isinstance(paths, list) else []:
            if not str(p).startswith("./") and p != ".":
                err(f"plugin.json: {key} path {p!r} must start with ./")
            elif ".." in str(p) or not (ROOT / p).exists():
                err(f"plugin.json: {key} path {p!r} does not exist inside the plugin")
    return name


def check_marketplace(plugin_name: str | None) -> None:
    data = load_json(ROOT / ".claude-plugin" / "marketplace.json")
    if data is None:
        return
    name = data.get("name", "")
    if not isinstance(name, str) or not PLUGIN_ID_PART.match(name) or ".." in name:
        err(f"marketplace.json: invalid name {name!r}")
    if name in RESERVED_MARKETPLACES or str(name).startswith("claudeai-"):
        err(f"marketplace.json: name {name!r} is reserved")
    if not (isinstance(data.get("owner"), dict) and data["owner"].get("name")):
        err("marketplace.json: owner.name is required")
    if not data.get("description"):
        warn("marketplace.json: no description")
    plugins = data.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        err("marketplace.json: plugins must be a non-empty array")
        return
    seen: set[str] = set()
    for i, p in enumerate(plugins):
        label = f"marketplace.json: plugins[{i}]"
        pname = p.get("name")
        if not isinstance(pname, str) or not PLUGIN_ID_PART.match(pname):
            err(f"{label}: invalid name {pname!r}")
            continue
        if pname in seen:
            err(f"{label}: duplicate plugin name {pname!r}")
        seen.add(pname)
        src = p.get("source")
        if isinstance(src, str):
            if src != "." and not src.startswith("./"):
                err(f"{label}: relative source must be '.' or start with './' (got {src!r})")
            elif ".." in src:
                err(f"{label}: source must not contain '..'")
            elif not (ROOT / src).is_dir():
                err(f"{label}: source {src!r} is not a directory")
        elif not isinstance(src, dict):
            err(f"{label}: source is required")
        if plugin_name and pname == plugin_name and isinstance(src, str) and src in (".", "./"):
            manifest = ROOT / ".claude-plugin" / "plugin.json"
            if not manifest.exists():
                err(f"{label}: source is the repo root but plugin.json is missing")


def check_skills() -> set[str]:
    names: set[str] = set()
    skills_dir = ROOT / "skills"
    if not skills_dir.is_dir():
        err("skills/: missing")
        return names
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        rel = skill_md.relative_to(ROOT)
        parsed = frontmatter(skill_md)
        if parsed is None:
            continue
        fm, body = parsed
        unknown = set(fm) - SKILL_KEYS
        if unknown:
            warn(f"{rel}: unknown frontmatter keys (ignored by Claude Code): {sorted(unknown)}")
        if fm.get("name") != skill_md.parent.name:
            err(f"{rel}: name {fm.get('name')!r} must match the directory name {skill_md.parent.name!r}")
        names.add(str(fm.get("name")))
        desc = str(fm.get("description", ""))
        if not desc:
            err(f"{rel}: description is required")
        if len(desc) + len(str(fm.get("when_to_use", ""))) > 1536:
            err(f"{rel}: description + when_to_use exceeds 1,536 characters")
        if fm.get("effort") and fm["effort"] not in EFFORTS:
            err(f"{rel}: invalid effort {fm['effort']!r}")
        if len(body.splitlines()) > 500:
            warn(f"{rel}: over 500 lines; move detail into reference files")
        for ref in sorted(set(re.findall(r"`(references/[\w./-]+)`", body))):
            if not (skill_md.parent / ref).is_file():
                err(f"{rel}: references missing file {ref}")
        if "${CLAUDE_SKILL_DIR}" in body:
            warn(f"{rel}: uses ${{CLAUDE_SKILL_DIR}}, which OpenCode does not substitute; use paths relative to the skill")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", str(fm.get("name", ""))) or len(str(fm.get("name", ""))) > 64:
            err(f"{rel}: name must be lowercase kebab-case, at most 64 chars (OpenCode requirement)")
    return names


def check_agents() -> set[str]:
    names: set[str] = set()
    agents_dir = ROOT / "agents"
    if not agents_dir.is_dir():
        err("agents/: missing")
        return names
    for md in sorted(agents_dir.glob("*.md")):
        rel = md.relative_to(ROOT)
        parsed = frontmatter(md)
        if parsed is None:
            continue
        fm, _ = parsed
        unknown = set(fm) - AGENT_KEYS
        if unknown:
            warn(f"{rel}: unknown frontmatter keys: {sorted(unknown)}")
        if not fm.get("name"):
            err(f"{rel}: name is required")
        elif fm["name"] != md.stem:
            warn(f"{rel}: name {fm['name']!r} differs from the file name {md.stem!r}")
        names.add(str(fm.get("name")))
        if not fm.get("description"):
            err(f"{rel}: description is required")
        model = fm.get("model")
        if model and model not in MODEL_ALIASES and not str(model).startswith("claude-"):
            err(f"{rel}: model {model!r} is not an alias or a model id")
        if fm.get("effort") and fm["effort"] not in EFFORTS:
            err(f"{rel}: invalid effort {fm['effort']!r}")
        tools = fm.get("tools", "")
        listed = [t.strip() for t in (tools.split(",") if isinstance(tools, str) else tools) if str(t).strip()]
        writers = {"Write", "Edit", "Bash", "NotebookEdit"} & set(listed)
        if "read-only" in str(fm.get("description", "")).lower() and writers:
            err(f"{rel}: described as read-only but grants {sorted(writers)}")
    return names


def check_skill_agent_consistency(skill_names: set[str], agent_names: set[str]) -> None:
    for skill_md in sorted((ROOT / "skills").glob("*/SKILL.md")):
        body = skill_md.read_text(encoding="utf-8")
        for agent in re.findall(r"`(dd-[a-z-]+)`", body):
            if agent not in agent_names:
                err(f"{skill_md.relative_to(ROOT)}: refers to subagent {agent!r}, which is not defined in agents/")


def check_opencode() -> None:
    agents = ROOT / "opencode" / "agents"
    for md in sorted(agents.glob("*.md")) if agents.is_dir() else []:
        rel = md.relative_to(ROOT)
        parsed = frontmatter(md)
        if parsed is None:
            continue
        fm, _ = parsed
        if not fm.get("description"):
            err(f"{rel}: description is required")
        if fm.get("mode") != "subagent":
            err(f"{rel}: mode must be 'subagent'")
        perms = fm.get("permission") or {}
        for tool in ("edit", "bash"):
            if perms.get(tool) != "deny":
                err(f"{rel}: subagent must be read-only (permission.{tool}: deny)")
    cmd = ROOT / "opencode" / "commands" / "double-diamond.md"
    if cmd.exists():
        parsed = frontmatter(cmd)
        if parsed:
            fm, body = parsed
            if not fm.get("description"):
                err(f"{cmd.relative_to(ROOT)}: description is required")
            if "$ARGUMENTS" not in body:
                err(f"{cmd.relative_to(ROOT)}: body must pass $ARGUMENTS to the skill")
    else:
        err("opencode/commands/double-diamond.md: missing")


def main() -> int:
    plugin_name = check_plugin()
    check_opencode()
    check_marketplace(plugin_name)
    skills = check_skills()
    agents = check_agents()
    check_skill_agent_consistency(skills, agents)

    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error:   {e}")
    if errors:
        print(f"\nvalidation failed: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"validation passed ({len(warnings)} warning(s)): plugin={plugin_name}, skills={sorted(skills)}, agents={sorted(agents)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
