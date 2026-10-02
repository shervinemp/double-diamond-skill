"""A scripted stand-in for the LLM, keyed by schema name, plus builders for fixtures."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from double_diamond.llm import UsageTracker
from double_diamond.models import (
    Brief,
    BriefDraft,
    Bucket,
    Candidate,
    CandidateScore,
    Confidence,
    Criterion,
    CriterionScore,
    DecisionItem,
    Graft,
    Lens,
    LensSet,
    TaskType,
    Triage,
    Verdict,
)
from double_diamond.policy import norm


def run(coro):
    return asyncio.run(coro)


@dataclass
class Call:
    role: str
    kind: str
    name: str
    system: str
    user: str


class MockLLM:
    """handlers: key -> instance | list (consumed in order, last repeats) | callable(user).

    Keys: a schema class name for structured calls, ``text:<role>`` and ``research:<role>``.
    """

    def __init__(self, handlers: dict[str, Any]):
        self.handlers = dict(handlers)
        self.calls: list[Call] = []
        self.usage = UsageTracker()

    def _resolve(self, key: str, user: str) -> Any:
        if key not in self.handlers:
            raise AssertionError(f"no handler for {key!r}")
        h = self.handlers[key]
        if callable(h):
            return h(user)
        if isinstance(h, list):
            return h.pop(0) if len(h) > 1 else h[0]
        return h

    def _count(self, role: str, user: str) -> None:
        row = self.usage.by_role[role]
        row["calls"] += 1
        row["input_tokens"] += max(1, len(user) // 4)

    async def structured(self, role, system, user, schema, *, max_tokens=None):
        self.calls.append(Call(role, "structured", schema.__name__, system, user))
        self._count(role, user)
        out = self._resolve(schema.__name__, user)
        assert isinstance(out, schema), f"handler for {schema.__name__} returned {type(out)}"
        return out

    async def text(self, role, system, user, *, max_tokens=None):
        self.calls.append(Call(role, "text", "", system, user))
        self._count(role, user)
        return self._resolve(f"text:{role}", user)

    async def research(self, role, system, user, *, tools=None, web=True, max_iterations=12):
        self.calls.append(Call(role, "research", "", system, user))
        self._count(role, user)
        return self._resolve(f"research:{role}", user)

    def calls_for(self, name: str) -> list[Call]:
        return [c for c in self.calls if c.name == name or (c.kind != "structured" and f"{c.kind}:{c.role}" == name)]


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def item(id: str = "lang", bucket: Bucket = Bucket.default, **kw: Any) -> DecisionItem:
    return DecisionItem(
        id=id,
        topic_key=kw.pop("topic_key", id),
        question=kw.pop("question", f"Which {id}?"),
        bucket=bucket,
        default=kw.pop("default", "the default"),
        options=kw.pop("options", []),
        why_it_matters=kw.pop("why_it_matters", "it matters"),
        changes_plan=kw.pop("changes_plan", False),
        toss_up=kw.pop("toss_up", False),
        costly_if_wrong=kw.pop("costly_if_wrong", False),
        obvious_to_user=kw.pop("obvious_to_user", False),
        regret=kw.pop("regret", 2),
        rationale=kw.pop("rationale", "because"),
    )


def ask_item(id: str = "db", **kw: Any) -> DecisionItem:
    """An ask item that passes all the tests."""
    kw.setdefault("changes_plan", True)
    kw.setdefault("toss_up", True)
    kw.setdefault("costly_if_wrong", True)
    kw.setdefault("options", ["postgres", "sqlite"])
    kw.setdefault("default", "postgres")
    return item(id, Bucket.ask, **kw)


def triage(task_type: TaskType = TaskType.coding, ambiguous: bool = True, costly: bool = True, money: bool = False) -> Triage:
    return Triage(
        ambiguous_or_multiple_approaches=ambiguous,
        costly_if_wrong=costly,
        money_or_sensitive_data=money,
        task_type=task_type,
        reason="because",
    )


def brief_draft(items: list[DecisionItem], **kw: Any) -> BriefDraft:
    return BriefDraft(
        restatement=kw.pop("restatement", "Do the thing."),
        success_criteria=kw.pop("success_criteria", ["it works"]),
        scope=kw.pop("scope", ["core"]),
        non_goals=kw.pop("non_goals", ["gold plating"]),
        constraints=kw.pop("constraints", ["no downtime"]),
        items=items,
        viable_approaches=kw.pop("viable_approaches", []),
        hard_to_reverse=kw.pop("hard_to_reverse", False),
        tradeoff_not_obvious=kw.pop("tradeoff_not_obvious", False),
        money_involved=kw.pop("money_involved", False),
        sensitive_data=kw.pop("sensitive_data", False),
    )


def brief(**kw: Any) -> Brief:
    return Brief(
        restatement=kw.pop("restatement", "Do the thing."),
        task_type=kw.pop("task_type", TaskType.coding),
        success_criteria=kw.pop("success_criteria", ["it works"]),
        scope=kw.pop("scope", []),
        non_goals=kw.pop("non_goals", []),
        constraints=kw.pop("constraints", []),
        **kw,
    )


def candidate(marker: str, **kw: Any) -> Candidate:
    return Candidate(
        approach=kw.pop("approach", f"{marker} approach"),
        key_decisions=kw.pop("key_decisions", [f"{marker} decision"]),
        first_steps=kw.pop("first_steps", [f"{marker} step"]),
        sacrifices=kw.pop("sacrifices", [f"{marker} gives up something"]),
        risks=kw.pop("risks", [f"{marker} risk"]),
        effort=kw.pop("effort", "M"),
        portable_ideas=kw.pop("portable_ideas", [f"{marker} idea"]),
    )


def lens_set(*names: str) -> LensSet:
    return LensSet(lenses=[Lens(name=n, priority=f"optimize {n}", sacrifices=f"{n} sacrifices") for n in names])


def verdict(
    rubric: list[Criterion],
    totals: dict[str, int],
    *,
    dq: set[str] = frozenset(),
    winner: str | None = None,
    confidence: Confidence = Confidence.high,
    grafts: list[Graft] | None = None,
    value_call: str = "",
    flip: str = "run the benchmark",
) -> Verdict:
    """totals: label -> uniform score 1..5 across every criterion."""
    scores = [
        CandidateScore(
            label=label,
            criteria_scores=[CriterionScore(criterion=c.name, score=s, justification="j") for c in rubric],
            disqualified=label in dq,
            disqualification_reason="broke a constraint" if label in dq else "",
        )
        for label, s in totals.items()
    ]
    return Verdict(
        scores=scores,
        winner_label=winner or max(totals, key=totals.get),
        confidence=confidence,
        grafts=grafts or [],
        would_change_my_mind=flip,
        needs_user_value_call=value_call,
    )


def labels_to_markers(judge_user: str, markers: list[str]) -> dict[str, str]:
    """Parse '### Candidate X' blocks from the judge's input and map label -> marker."""
    mapping: dict[str, str] = {}
    for m in re.finditer(r"### Candidate ([A-Z])\n(.*?)(?=\n\n### Candidate|\Z)", judge_user, re.S):
        body = m.group(2)
        for marker in markers:
            if marker in body:
                mapping[m.group(1)] = marker
    return mapping


def rubric_names(rubric: list[Criterion]) -> list[str]:
    return [norm(c.name) for c in rubric]
