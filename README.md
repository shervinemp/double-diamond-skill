# double-diamond-skill

An expand-then-contract request handler for Claude Code, based on the Double Diamond idea: surface what the request leaves unsaid, decide it cheaply where possible, compare real alternatives where the choice matters, then deliver one plan.

It is a Claude Code **skill**, packaged as a **plugin** so it installs in one command. It runs inside your Claude Code session on your normal login. There is no separate program, server, or API key.

## Install

### As a plugin

```bash
claude plugin marketplace add shervinemp/double-diamond-skill
claude plugin install double-diamond@double-diamond
```

Plugin components are namespaced, so the invocation is `/double-diamond:double-diamond <request>`. To try it from a clone without installing: `claude --plugin-dir .`

### As a plain skill (no namespace)

```bash
git clone https://github.com/shervinemp/double-diamond-skill.git
cd double-diamond-skill
mkdir -p ~/.claude/skills ~/.claude/agents
cp -r skills/double-diamond ~/.claude/skills/
cp agents/dd-*.md ~/.claude/agents/
```

Restart Claude Code. The invocation is `/double-diamond <request>`. The skill only runs when you invoke it.

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
| `agents/` | `dd-candidate` and `dd-judge`: read-only subagents with fresh, isolated context. |
| `.claude-plugin/` | `plugin.json` and `marketplace.json`. |
| `snippets/CLAUDE.disposition.md` | The cheap always-on slice (ask vs. default, the obvious-requirements baseline) to paste into a CLAUDE.md. |
| `benchmarks/` | A blind A/B protocol and starter prompts, run in Claude Code. |
| `scripts/validate.py` | Offline plugin, skill and agent checks, run in CI. |

## Status

- `scripts/validate.py` checks the manifests, frontmatter, file references and subagent names, and CI runs it. It approximates `claude plugin validate`; run the real command too (`claude plugin validate .`).
- **Not yet run end to end in Claude Code.** The skill's behavior (how well it triages, how rarely it asks, how good the plans are) is unmeasured. The benchmarks are the way to find out.
- Reducing risk is not eliminating it. For recommendations, fake reviews and fake businesses can fool a model as well as a person; the skill leans toward established, accountable options for high-stakes documents and tells you what it could not verify.

## License

No license file yet. Until one is added the code is all-rights-reserved by default.
