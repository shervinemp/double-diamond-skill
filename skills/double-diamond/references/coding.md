# Coding and engineering tasks

Load this for building, changing, migrating, or deploying software.

## Discover before asking

Almost every "which tool or convention" question is answerable from the repo. Check, in this order: `CLAUDE.md` and contributor docs, lockfiles and manifests (language, package manager, framework versions), CI config, existing tests and how they are run, directory conventions, recent git history for the touched area. Run the test suite or type-checker once to learn the baseline state. Default to what the repo already does.

## Decisions that usually matter

- **Blast radius and reversibility.** Data migrations, deletions, schema changes, public API changes, anything that deploys or sends. These are the usual genuine questions.
- **Compatibility.** Who consumes this? Versions that must keep working.
- **Scope.** Minimal change versus refactor. Default to minimal unless the request implies otherwise.
- **Verification.** How will we know it works? Prefer a runnable check over "looks right".

## Baseline additions for code

- Existing tests pass before and after; new behavior has a test.
- No secrets, keys, or personal data in code, logs, or commits.
- Dependencies: confirm a package actually exists and is maintained before adding it (hallucinated or typosquatted package names are real), check the license is compatible, and prefer what the project already uses.
- Input validation and error paths at trust boundaries.
- A rollback path for anything that touches data or production.
- Respect the project's own style; do not reformat unrelated code.

## Lenses for Phase 2 (examples; derive real ones from the forks)

Smallest change that works; most testable design; least disruption to existing consumers; fastest to ship behind a flag. Judge by evidence where possible: a quick spike, the type-checker, a benchmark, or a failing test beats opinion.

## Pre-mortem prompts

What breaks on first deploy? What data could be lost or corrupted? What happens to a consumer on the old version? Is the change reversible within a minute? What did the tests not cover?

## Plan shape

Numbered steps ordered smallest-useful-first, each with its verification command; a rollback note; an explicit "not touching" list.
