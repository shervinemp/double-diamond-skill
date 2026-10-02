"""Command line interface: ``double-diamond``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Callable

import anthropic

from . import __version__, evals
from .ask import Asker, AutoAsker, CliAsker
from .config import Settings
from .llm import LLM, AnthropicBackend, LLMError, RefusedError
from .models import Plan
from .pipeline import Pipeline, RunOptions
from .render import render_audit, render_plan
from .store import Store

AUTH_HINT = (
    "Could not authenticate. Set ANTHROPIC_API_KEY, or run `ant auth login` to create a profile the SDK reads automatically."
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="double-diamond", description="Expand-then-contract request pipeline.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--state-dir", help="Where preferences, events and runs are stored (default ~/.double-diamond).")
    # Accept --state-dir after the subcommand too; SUPPRESS keeps it from clobbering the top-level value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state-dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Plan a request.", parents=[common])
    run.add_argument("request", nargs="+", help="The request, or '-' to read it from stdin.")
    run.add_argument("--force", action="store_true", help="Run the full pipeline even if triage says to skip it.")
    run.add_argument("--yes", action="store_true", help="Never ask; accept every recommended default.")
    run.add_argument("--workdir", type=Path, help="Let discovery read files under this directory (read-only).")
    run.add_argument("--no-web", action="store_true", help="Disable web search.")
    run.add_argument("--seed", type=int, help="Seed for candidate shuffling (reproducible runs).")
    run.add_argument("--audit", action="store_true", help="Also print the deliberation.")
    run.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    show = sub.add_parser("show", help="Print a saved run.", parents=[common])
    show.add_argument("run_id")
    show.add_argument("--audit", action="store_true")

    reopen = sub.add_parser("reopen", help="Re-open one assumption of a saved plan with new evidence.", parents=[common])
    reopen.add_argument("run_id")
    reopen.add_argument("--item", required=True, help="Assumption id from the plan's ledger.")
    reopen.add_argument("--evidence", required=True)
    reopen.add_argument("--yes", action="store_true")

    sub.add_parser("runs", help="List saved runs.", parents=[common])
    sub.add_parser("stats", help="Show tuning signals: questions, go rate, override rate.", parents=[common])

    forget = sub.add_parser("forget", help="Forget a standing preference.", parents=[common])
    forget.add_argument("topic_key")

    ev = sub.add_parser("eval", help="Blind A/B evaluation against a plain model call.", parents=[common])
    ev_sub = ev.add_subparsers(dest="eval_command", required=True)
    ev_run = ev_sub.add_parser("run", parents=[common])
    ev_run.add_argument("prompts", type=Path, help="JSONL file: id, prompt, expect, kind.")
    ev_run.add_argument("--out", type=Path, required=True)
    ev_run.add_argument("--judge", choices=("llm", "human"), default="llm")
    ev_run.add_argument("--limit", type=int)
    ev_run.add_argument("--seed", type=int, default=0)
    ev_rev = ev_sub.add_parser("reveal", parents=[common])
    ev_rev.add_argument("out", type=Path)
    ev_rev.add_argument("scores", type=Path)
    return p


def _stderr(msg: str) -> None:
    print(f"· {msg}", file=sys.stderr, flush=True)


def main(
    argv: list[str] | None = None,
    *,
    llm_factory: Callable[[Settings], LLM] | None = None,
    asker: Asker | None = None,
    out: Callable[[str], None] = print,
) -> int:
    args = build_parser().parse_args(argv)
    store = Store(args.state_dir)
    try:
        return _dispatch(args, store, llm_factory, asker, out)
    except RefusedError as e:
        out(f"The request was declined: {e}")
        return 2
    except LLMError as e:
        text = str(e)
        out(f"Error: {text}")
        if "authenticat" in text.lower() or "api key" in text.lower() or "credential" in text.lower():
            out(AUTH_HINT)
        return 2
    except anthropic.AnthropicError as e:
        out(f"Error: {e}")
        out(AUTH_HINT)
        return 2
    except (KeyError, ValueError, FileNotFoundError) as e:
        out(f"Error: {e}")
        return 2


def _make_llm(factory: Callable[[Settings], LLM] | None) -> tuple[LLM, Settings]:
    settings = Settings.from_env()
    return (factory or (lambda s: AnthropicBackend(s)))(settings), settings


def _dispatch(
    args: argparse.Namespace,
    store: Store,
    llm_factory: Callable[[Settings], LLM] | None,
    asker: Asker | None,
    out: Callable[[str], None],
) -> int:
    cmd = args.command

    if cmd == "stats":
        out(json.dumps(store.stats(), indent=2))
        return 0
    if cmd == "runs":
        for rid in store.list_runs():
            out(rid)
        return 0
    if cmd == "forget":
        out("forgotten" if store.forget(args.topic_key) else "no such preference")
        return 0
    if cmd == "show":
        state = store.load_run(args.run_id)
        if state.get("skipped"):
            out(state.get("answer", ""))
        else:
            out(render_plan(Plan.model_validate(state["plan"])))
        if args.audit:
            out("\n---\n" + render_audit(state.get("audit", {})))
        return 0
    if cmd == "eval" and args.eval_command == "reveal":
        out(json.dumps(evals.reveal(args.out, args.scores), indent=2))
        return 0

    llm, settings = _make_llm(llm_factory)

    if cmd == "run":
        request = " ".join(args.request)
        if request.strip() == "-":
            request = sys.stdin.read()
        chosen = asker or (AutoAsker() if args.yes or not sys.stdin.isatty() else CliAsker())
        options = RunOptions(force=args.force, workdir=args.workdir, web=not args.no_web, seed=args.seed)
        pipeline = Pipeline(llm, chosen, store, settings, options, log=_stderr)
        result = asyncio.run(pipeline.run(request))
        if args.json:
            out(
                json.dumps(
                    {
                        "run_id": result.run_id,
                        "skipped": result.skipped,
                        "markdown": result.markdown,
                        "plan": result.plan.model_dump(mode="json") if result.plan else None,
                        "usage": result.usage,
                    },
                    indent=2,
                )
            )
        else:
            out(result.markdown)
            if args.audit and not result.skipped:
                out("\n---\n" + result.audit_markdown())
            out(f"\n(run {result.run_id})")
        return 0

    if cmd == "reopen":
        chosen = asker or (AutoAsker() if args.yes or not sys.stdin.isatty() else CliAsker())
        pipeline = Pipeline(llm, chosen, store, settings, RunOptions(), log=_stderr)
        result = asyncio.run(pipeline.reopen(args.run_id, args.item, args.evidence))
        out(result.markdown)
        return 0

    if cmd == "eval" and args.eval_command == "run":
        items = evals.load_prompts(args.prompts)
        if args.limit:
            items = items[: args.limit]
        scratch = Store(Path(args.out) / "state")  # keep eval runs out of your real preferences

        def make_pipeline() -> Pipeline:
            return Pipeline(llm, AutoAsker(), scratch, settings, RunOptions(), log=_stderr)

        result = asyncio.run(
            evals.run_eval(items, llm, make_pipeline, args.out, judge=args.judge, seed=args.seed, log=_stderr)
        )
        out(evals.render_report(result))
        return 0

    raise ValueError(f"unknown command: {cmd}")


def entry() -> None:  # pragma: no cover
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    entry()
