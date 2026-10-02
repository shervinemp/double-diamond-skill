# double-diamond-skill

An expand-then-contract request handler for Claude Code and OpenCode, based on the Double Diamond idea: surface what the request leaves unsaid, decide it cheaply where possible, compare real alternatives where the choice matters, then deliver one plan.

It is a **skill**, packaged as a Claude Code **plugin** and with OpenCode support. It runs inside your existing Claude Code or OpenCode session on your normal login. There is no separate program, server, or API key.

## Install

Clone the repo, then run the installer. It installs for both tools by default:

```bash
git clone https://github.com/shervinemp/double-diamond-skill.git
cd double-diamond-skill
python scripts/install.py
```

Options: `--claude` or `--opencode` for one tool, `--dry-run` to preview, `--force` to overwrite files that differ, `--uninstall` to remove everything it installed. It never overwrites a file that differs unless you pass `--force`. Start a new session afterwards.

| Installs | Claude Code | OpenCode |
|---|---|---|
| Skill | `~/.claude/skills/double-diamond/` | read from `~/.claude/skills` (copied to `~/.config/opencode/skills` only with `--opencode` alone) |
| Subagents | `~/.claude/agents/dd-*.md` | `~/.config/opencode/agents/dd-*.md` (OpenCode format, generated from `agents/`) |
| Command | n/a (the skill is the command) | `~/.config/opencode/commands/double-diamond.md` (an existing `command/` directory is reused) |

### Claude Code as a plugin instead

```bash
claude plugin marketplace add shervinemp/double-diamond-skill
claude plugin install double-diamond@double-diamond
```

Plugin components are namespaced, so the invocation becomes `/double-diamond:double-diamond <request>`. To try it from a clone without installing: `claude --plugin-dir .`

## Use

```
/double-diamond Where can I print and bind my immigration forms near Austin, Texas?
```

The same command works in both tools. It only runs when you invoke it, and it skips itself on small, clear requests. Expect one line saying which phases will run, usually no questions (reply `go` to accept the recommended answers if it asks), then one plan with its assumptions. Say `go` to execute, or tell it which assumption to flip.

**OpenCode notes**
- `/double-diamond` is a small command that tells the agent to load the skill. OpenCode only reads `name` and `description` from a skill's frontmatter, so it does not honor the skill's "explicit invocation only" flag; the command is the dependable way to start it.
- Subagents run on the same model as your session unless you set one. For a judge that differs from the candidates, add `model: provider/model-id` to `dd-judge.md` in your OpenCode agents folder.

## What it does

```
Triage ─► Phase 1: Expand & ground ─► Phase 2: Diverge & compare ─► Phase 3: Contract ─► (execute)
 gate      silent baseline, discover,     lenses → candidates → judge     scrub + pre-mortem    re-expand on a
           default, rarely ask            (only if hard to reverse)       one canonical plan    broken assumption
```

- **A silent baseline covers the obvious.** Price, quality, legitimacy, time, privacy, recourse, and the task's own rules are applied to every request without being asked. They surface only on a conflict with something you said, or a real concern.
- **Questions are rare by design.** A question must pass three tests (it changes the plan, it is a real toss-up, a wrong guess is costly) plus an obviousness check. The target is 0 or 1 per request. Everything else becomes a stated assumption you can flip at the plan checkpoint.
- **Four buckets, not two.** Discoverable / low-regret / ask / irrelevant. It looks before asking.
- **Lenses come from the real forks**, not from fixed MVP/enterprise/bleeding-edge archetypes, and Phase 2 is conditional.
- **Isolated subagents.** Candidates are generated in parallel by read-only `dd-candidate` subagents with fresh context. One `dd-judge` subagent scores them anonymized and shuffled, against a rubric weighted from your own constraints. Crossover is gated: each graft must name the failure mode it fixes.
- **Recommendation tasks get their own path.** For picking a vendor, service, or place it gathers options from several independent sources, vets them adversarially (does it exist, do the reviews hold up, is payment protected), and grades the evidence instead of trusting star ratings.
- **Task-type references** (coding, writing, research and decisions, recommendations) load only when relevant.
- **The assumption ledger outlives planning.** If execution contradicts an assumption, stop and re-open that one fork.

## Repository map

| Path | Role |
|---|---|
| `skills/double-diamond/` | `SKILL.md` and `references/` (coding, writing, research-decision, recommendation). |
| `agents/` | `dd-candidate` and `dd-judge` for Claude Code: read-only subagents with isolated context. Source of truth. |
| `opencode/` | OpenCode subagents (generated from `agents/`) and the `/double-diamond` command. |
| `.claude-plugin/` | `plugin.json` and `marketplace.json`. |
| `snippets/CLAUDE.disposition.md` | The cheap always-on slice (ask vs. default, the obvious-requirements baseline) to paste into a CLAUDE.md or AGENTS.md. |
| `benchmarks/` | A blind A/B protocol and starter prompts. |
| `scripts/` | `install.py`, `sync_opencode.py` (regenerates `opencode/agents/`), `validate.py` (offline checks, run in CI). |

After editing anything in `agents/`, run `python scripts/sync_opencode.py`; CI fails if the OpenCode copies are stale.

## Status

- `scripts/validate.py` checks the manifests, frontmatter, file references and subagent names, and CI runs it. It approximates `claude plugin validate`; run the real command too (`claude plugin validate .`).
- The installer is tested against throwaway directories: dry run, install, re-run, no-clobber, force, uninstall, and reuse of an existing OpenCode `command/` directory.
- **Behavior is unmeasured.** How well the skill triages, how rarely it asks, and how good the plans are have not been evaluated yet. The benchmarks are the way to find out.
- Reducing risk is not eliminating it. For recommendations, fake reviews and fake businesses can fool a model as well as a person; the skill leans toward established, accountable options for high-stakes documents and tells you what it could not verify.

## License

No license file yet. Until one is added the code is all-rights-reserved by default.
