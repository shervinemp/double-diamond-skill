"""How the pipeline asks the human its (rare) questions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Protocol

YOU_DECIDE = "You decide (use the recommended default)"


@dataclass
class Question:
    id: str
    text: str
    options: list[str]
    recommended: str
    why: str


class Asker(Protocol):
    interactive: bool

    async def ask(self, questions: list[Question]) -> dict[str, str]:
        """Return a chosen option or free text per question id. A missing id means
        the recommended default was accepted. ``YOU_DECIDE`` means the same, explicitly."""
        ...


class AutoAsker:
    """Non-interactive: accept every recommended default."""

    interactive = False

    async def ask(self, questions: list[Question]) -> dict[str, str]:
        return {}


@dataclass
class ScriptedAsker:
    """Answers from a dict; used by tests and by eval runs that pin answers."""

    answers: dict[str, str] = field(default_factory=dict)
    asked: list[Question] = field(default_factory=list)
    interactive = True

    async def ask(self, questions: list[Question]) -> dict[str, str]:
        self.asked.extend(questions)
        return {q.id: self.answers[q.id] for q in questions if q.id in self.answers}


class CliAsker:
    """Plain terminal prompts. Enter or "go" accepts the recommended default."""

    interactive = True

    def __init__(self, input_fn: Callable[[str], str] = input, out: Callable[[str], None] = print):
        self._input = input_fn
        self._out = out

    async def ask(self, questions: list[Question]) -> dict[str, str]:
        self._out("\nA few decisions are genuinely yours. Press Enter (or type 'go') to accept the recommended answer.\n")
        answers: dict[str, str] = {}
        for q in questions:
            options = q.options + [YOU_DECIDE]
            self._out(f"{q.text}\n  why it matters: {q.why}")
            for i, opt in enumerate(options, 1):
                marker = "  (recommended)" if opt == q.recommended else ""
                self._out(f"  {i}. {opt}{marker}")
            raw = (await asyncio.to_thread(self._input, "> ")).strip()
            if raw.lower() == "go":
                return answers  # accept all remaining defaults too
            if not raw:
                continue
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                answers[q.id] = options[int(raw) - 1]
            else:
                answers[q.id] = raw
        return answers
