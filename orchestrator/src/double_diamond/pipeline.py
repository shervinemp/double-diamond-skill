"""The pipeline: triage, expand and ground, (diverge and compare), contract.

Control flow lives here, in code. Models only fill the narrow structured
transforms; ``policy`` enforces the rules on what they return.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from . import policy, prompts
from .ask import YOU_DECIDE, Asker, Question
from .config import Settings
from .llm import LLM, LLMError
from .models import (
    Brief,
    BriefDraft,
    Bucket,
    Candidate,
    ContractOutput,
    Graft,
    LedgerEntry,
    LensSet,
    Plan,
    ReopenDecision,
    ReopenVerdict,
    Resolutions,
    RubricDraft,
    Shortlist,
    TaskType,
    Triage,
    Verdict,
    WorkItem,
)
from .render import render_audit, render_plan
from .store import Store
from .tools import LocalTools


@dataclass
class RunOptions:
    force: bool = False
    workdir: Path | None = None
    web: bool = True
    seed: int | None = None


@dataclass
class RunResult:
    run_id: str
    skipped: bool
    markdown: str
    plan: Plan | None = None
    audit: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    questions_asked: int = 0

    def audit_markdown(self) -> str:
        return render_audit(self.audit)


@dataclass
class Decision:
    """What Phase 2 (or its absence) hands to the contractor."""

    chosen: Candidate | None = None
    grafts: list[Graft] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    open_issue: str | None = None
    confidence_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chosen": self.chosen.model_dump() if self.chosen else None,
            "grafts": [g.model_dump() for g in self.grafts],
            "rejected": self.rejected,
            "open_issue": self.open_issue,
            "confidence_note": self.confidence_note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Decision":
        if not d:
            return cls()
        return cls(
            chosen=Candidate.model_validate(d["chosen"]) if d.get("chosen") else None,
            grafts=[Graft.model_validate(g) for g in d.get("grafts", [])],
            rejected=list(d.get("rejected", [])),
            open_issue=d.get("open_issue"),
            confidence_note=d.get("confidence_note"),
        )


def _classify_answer(raw: str | None, default: str) -> tuple[str, str]:
    """Map a raw answer to (outcome, final value)."""
    if raw is None or not raw.strip():
        return "accepted_default", default
    if raw == YOU_DECIDE:
        return "you_decide", default
    if raw.strip().lower() == default.strip().lower():
        return "answered_same", default
    return "overridden", raw.strip()


def _options_for(item: WorkItem) -> list[str]:
    options = list(item.options)
    if item.default and item.default not in options:
        options.insert(0, item.default)
    return options


class Pipeline:
    def __init__(
        self,
        llm: LLM,
        asker: Asker,
        store: Store,
        settings: Settings | None = None,
        options: RunOptions | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.llm = llm
        self.asker = asker
        self.store = store
        self.settings = settings or Settings()
        self.options = options or RunOptions()
        self.log = log or (lambda _msg: None)
        self.rng = random.Random(self.options.seed)
        self.tools = LocalTools(self.options.workdir) if self.options.workdir else None

    # ------------------------------------------------------------------ run

    async def run(self, request: str) -> RunResult:
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        audit: dict[str, Any] = {"request": request}

        self.log("triage")
        triage = await self.llm.structured("triage", prompts.TRIAGE, request, Triage)
        audit["triage"] = triage.model_dump(mode="json")

        if not policy.should_run_pipeline(triage, self.options.force):
            self.log("clear enough to just do; skipping the pipeline")
            answer = await self.llm.text("direct", prompts.DIRECT, request)
            self.store.log("run", run_id=run_id, skipped=True)
            self.store.save_run(
                run_id,
                {"run_id": run_id, "request": request, "skipped": True, "audit": audit, "answer": answer},
            )
            return RunResult(run_id, True, answer, None, audit, self.llm.usage.snapshot())

        brief, asked = await self._phase1(run_id, request, triage, audit)

        decision = Decision()
        if brief.task_type == TaskType.recommendation:
            plan = await self._recommend(request, brief, audit)
        else:
            if policy.should_diverge(brief):
                decision = await self._phase2(brief, audit) or Decision()
            self.log("writing the plan")
            plan = await self._contract(brief, decision)

        markdown = render_plan(plan)
        usage = self.llm.usage.snapshot()
        self.store.log("run", run_id=run_id, skipped=False, questions=asked, task_type=brief.task_type.value)
        self.store.save_run(
            run_id,
            {
                "run_id": run_id,
                "request": request,
                "skipped": False,
                "revision": 1,
                "triage": audit["triage"],
                "brief": brief.model_dump(mode="json"),
                "decision": decision.to_dict(),
                "plan": plan.model_dump(mode="json"),
                "audit": audit,
                "usage": usage,
            },
        )
        return RunResult(run_id, False, markdown, plan, audit, usage, asked)

    # -------------------------------------------------------------- phase 1

    async def _phase1(self, run_id: str, request: str, triage: Triage, audit: dict[str, Any]) -> tuple[Brief, int]:
        self.log("expanding the request")
        guidance = prompts.TASK_GUIDANCE[triage.task_type]
        standing = self.store.standing_summary()
        user = (
            f"Today: {date.today().isoformat()}\n\nRequest:\n{request}\n\n"
            f"Task-specific guidance:\n{guidance}\n\n"
            f"Standing preferences (already settled):\n{json.dumps(standing)}"
        )
        draft = await self.llm.structured("expand", prompts.EXPANDER, user, BriefDraft)

        items = [WorkItem(**it.model_dump()) for it in draft.items]
        items, notes = policy.enforce_buckets(items, self.store.standing, self.settings.ask_cap)
        audit["bucket_notes"] = notes

        items = await self._discover(request, items, audit)
        asked = await self._ask(run_id, items)
        audit["items"] = [i.model_dump(mode="json") for i in items]

        ledger: list[LedgerEntry] = []
        confirmed: list[str] = []
        for i in items:
            if i.bucket == Bucket.irrelevant:
                continue
            if i.source == "answered":
                confirmed.append(f"{i.question} → {i.default}")
                continue
            ledger.append(
                LedgerEntry(
                    id=i.id,
                    topic_key=i.topic_key,
                    assumption=f"{i.question} → {i.default}",
                    why=i.rationale or i.why_it_matters,
                    how_to_flip=f'Tell me to use a different value for "{i.topic_key or i.id}".',
                    source=i.source,
                )
            )

        brief = Brief(
            restatement=draft.restatement,
            task_type=triage.task_type,
            success_criteria=draft.success_criteria,
            scope=draft.scope,
            non_goals=draft.non_goals,
            constraints=draft.constraints,
            confirmed=confirmed,
            ledger=ledger,
            viable_approaches=draft.viable_approaches,
            hard_to_reverse=draft.hard_to_reverse,
            tradeoff_not_obvious=draft.tradeoff_not_obvious,
            money_involved=draft.money_involved,
            sensitive_data=draft.sensitive_data,
        )
        return brief, asked

    async def _discover(self, request: str, items: list[WorkItem], audit: dict[str, Any]) -> list[WorkItem]:
        pending = [i for i in items if i.bucket == Bucket.discoverable]
        if not pending:
            return items

        def settle_as_default(reason: str) -> None:
            for i in pending:
                i.bucket = Bucket.default
                i.source = reason
            policy_defaults = [i for i in pending if not i.default.strip()]
            for i in policy_defaults:
                i.default = i.options[0] if i.options else policy.DEFAULT_PLACEHOLDER

        if not (self.options.web or self.tools is not None):
            settle_as_default("defaulted (nothing to look it up with)")
            return items

        self.log("looking things up")
        questions = "\n".join(f"- {i.id}: {i.question}" for i in pending)
        where = f"\nYou may read files under the working directory." if self.tools is not None else ""
        where += "\nWeb search is available." if self.options.web else ""
        user = f"Context: {request}\n\nQuestions to answer:\n{questions}{where}"
        try:
            notes = await self.llm.research(
                "research", prompts.DISCOVERY, user, tools=self.tools, web=self.options.web
            )
            resolved = await self.llm.structured(
                "expand",
                prompts.RESOLVER,
                f"Questions:\n{questions}\n\nResearch notes:\n{notes}",
                Resolutions,
            )
        except LLMError as e:
            self.log(f"discovery failed ({e}); falling back to defaults")
            settle_as_default("defaulted (lookup failed)")
            return items

        audit["discovery"] = notes
        by_id = {r.id: r for r in resolved.items}
        for i in pending:
            r = by_id.get(i.id)
            i.bucket = Bucket.default
            if r is not None and r.confident and r.answer.strip():
                i.resolved_value = r.answer
                i.default = r.answer
                i.source = f"discovered ({r.evidence})"
            else:
                i.source = "defaulted (could not be determined)"
                if not i.default.strip():
                    i.default = r.answer if r is not None and r.answer.strip() else policy.DEFAULT_PLACEHOLDER
        return items

    async def _ask(self, run_id: str, items: list[WorkItem]) -> int:
        asks = [i for i in items if i.bucket == Bucket.ask]
        if not asks:
            return 0
        self.log(f"asking {len(asks)} question(s)")
        questions = [
            Question(i.id, i.question, _options_for(i), i.default, i.why_it_matters) for i in asks
        ]
        answers = await self.asker.ask(questions)
        auto = not self.asker.interactive
        for i in asks:
            outcome, value = _classify_answer(answers.get(i.id), i.default)
            explicit = outcome in ("answered_same", "overridden")
            i.default = value
            i.source = "answered" if explicit else "default (asked, accepted)"
            i.bucket = Bucket.default
            if explicit and not auto:
                self.store.record_answer(i.topic_key, value)
            self.store.log(
                "question",
                run_id=run_id,
                id=i.id,
                topic_key=i.topic_key,
                recommended=i.default if not explicit else value,
                outcome=outcome,
                auto=auto,
            )
        return len(asks)

    # -------------------------------------------------------------- phase 2

    async def _phase2(self, brief: Brief, audit: dict[str, Any]) -> Decision | None:
        brief_json = brief.model_dump_json(indent=2)
        self.log("choosing lenses")
        lens_set = await self.llm.structured("lenses", prompts.LENSES, brief_json, LensSet)
        lenses = lens_set.lenses[: self.settings.max_candidates]
        if len(lenses) < 2:
            return None

        self.log(f"generating {len(lenses)} candidates in parallel")
        gate = asyncio.Semaphore(4)

        async def generate(lens) -> Candidate:
            async with gate:
                user = (
                    f"Brief:\n{brief_json}\n\nLens: {lens.name}\nPriority: {lens.priority}\n"
                    f"This lens gives up: {lens.sacrifices}"
                )
                return await self.llm.structured("candidate", prompts.CANDIDATE, user, Candidate)

        results = await asyncio.gather(*(generate(l) for l in lenses), return_exceptions=True)
        good = [(l, c) for l, c in zip(lenses, results) if isinstance(c, Candidate)]
        for l, c in zip(lenses, results):
            if isinstance(c, BaseException):
                self.log(f"candidate '{l.name}' failed: {c}")
        if len(good) < 2:
            return None

        anon = policy.anonymize([c for _, c in good], self.rng)
        lens_by_label = {lab: good[idx][0].name for lab, idx in anon.origin.items()}

        rubric_draft = await self.llm.structured("rubric", prompts.RUBRIC, brief_json, RubricDraft)
        rubric = policy.build_rubric(rubric_draft.criteria)

        self.log("judging")
        rubric_json = json.dumps([c.model_dump() for c in rubric], indent=2)
        candidates_text = "\n\n".join(
            f"### Candidate {lab}\n{json.dumps(anon.candidates[lab].model_dump(), indent=2)}"
            for lab in anon.labels
        )
        judge_user = (
            f"Brief:\n{brief_json}\n\nRubric (weights sum to 100):\n{rubric_json}\n\n"
            f"Candidates:\n{candidates_text}"
        )
        verdict = await self.llm.structured("judge", prompts.JUDGE, judge_user, Verdict)
        ranking = policy.rank_candidates(verdict, rubric)

        phase2: dict[str, Any] = {
            "lenses": [l.model_dump() for l in lenses],
            "lens_by_label": lens_by_label,
            "rubric": [c.model_dump() for c in rubric],
            "ranking": [
                {"label": r.label, "total": r.total, "disqualified": r.disqualified, "missing": r.missing}
                for r in ranking.ranked
            ],
            "winner": ranking.winner,
            "margin": ranking.margin,
            "confidence": ranking.confidence.value,
            "judge_winner_differs": ranking.judge_winner_differs,
            "verdict": verdict.model_dump(mode="json"),
            "grafts": [],
        }
        audit["phase2"] = phase2

        if ranking.winner is None:
            return Decision(
                open_issue="Every candidate approach violated a constraint, so none was chosen. "
                "Revisit the constraints or non-goals."
            )

        winner = ranking.winner
        value_call_changed = False
        if (
            ranking.needs_evidence
            and verdict.needs_user_value_call.strip()
            and ranking.runner_up is not None
            and self.asker.interactive
        ):
            runner = ranking.runner_up
            q = Question(
                "value-call",
                verdict.needs_user_value_call.strip(),
                [lens_by_label[winner], lens_by_label[runner]],
                lens_by_label[winner],
                "The top two approaches differ mainly on a trade-off only you can settle.",
            )
            answer = (await self.asker.ask([q])).get("value-call")
            if answer and answer != YOU_DECIDE and answer.strip().lower() == lens_by_label[runner].lower():
                winner, value_call_changed = runner, True
            self.store.log("question", run_id="phase2", id="value-call", topic_key="value-call",
                           recommended=lens_by_label[ranking.winner], outcome="overridden" if value_call_changed else "accepted_default",
                           auto=False)
            ranking.winner = winner

        grafts = [] if value_call_changed else policy.approve_grafts(verdict, winner, self.settings.max_grafts)
        phase2["grafts"] = [g.model_dump() for g in grafts]
        phase2["winner"] = winner
        rejected = policy.rejected_one_liners(ranking, lens_by_label, verdict, rubric)

        note = f"Judge confidence {ranking.confidence.value}; margin {ranking.margin:.0f} points."
        open_issue = None
        if ranking.needs_evidence and verdict.would_change_my_mind.strip() and not value_call_changed:
            open_issue = f"Close call between approaches. Cheapest way to settle it: {verdict.would_change_my_mind.strip()}"
        if ranking.judge_winner_differs:
            note += " The judge's stated winner differed from the weighted score; the score was used."
        return Decision(anon.candidates[winner], grafts, rejected, open_issue, note)

    # -------------------------------------------------------------- phase 3

    async def _contract(self, brief: Brief, decision: Decision) -> Plan:
        payload = policy.contractor_payload(
            brief,
            decision.chosen,
            decision.grafts,
            decision.rejected,
            decision.open_issue,
            decision.confidence_note,
        )
        guidance = prompts.TASK_GUIDANCE[brief.task_type]
        system = f"{prompts.CONTRACTOR}\n\nTask-type guidance:\n{guidance}"
        out = await self.llm.structured("contract", system, json.dumps(payload, indent=2), ContractOutput)
        open_items = list(out.open_items)
        if decision.open_issue and decision.open_issue not in open_items:
            open_items.append(decision.open_issue)
        return Plan(
            kind="plan",
            goal_and_done=out.goal_and_done,
            steps=out.steps,
            assumptions=brief.ledger,
            non_goals=brief.non_goals,
            trade_offs=out.trade_offs,
            risks=out.risks,
            open_items=open_items,
            confidence_note=decision.confidence_note,
        )

    # ------------------------------------------------------- recommendation

    async def _recommend(self, request: str, brief: Brief, audit: dict[str, Any]) -> Plan:
        brief_json = brief.model_dump_json(indent=2)
        if self.options.web:
            self.log("finding and vetting options")
            user = f"Today: {date.today().isoformat()}\n\nBrief:\n{brief_json}\n\nOriginal request:\n{request}"
            notes = await self.llm.research("research", prompts.RECOMMEND_RESEARCH, user, tools=self.tools, web=True)
        else:
            notes = "No web access was available, so nothing could be looked up or verified."
        audit["recommendation_notes"] = notes

        shortlist = await self.llm.structured(
            "contract",
            prompts.RECOMMEND_EXTRACT,
            f"Brief:\n{brief_json}\n\nResearch notes:\n{notes}",
            Shortlist,
        )
        shortlist = policy.enforce_shortlist(shortlist, sensitive=brief.sensitive_data or brief.money_involved)
        done = "; ".join(brief.success_criteria)
        return Plan(
            kind="shortlist",
            goal_and_done=f"{brief.restatement} Done when: {done}" if done else brief.restatement,
            assumptions=brief.ledger,
            non_goals=brief.non_goals,
            shortlist=shortlist,
        )

    # --------------------------------------------------------------- reopen

    async def reopen(self, run_id: str, item_id: str, evidence: str) -> RunResult:
        """Re-open one assumption with new evidence, then re-contract."""
        state = self.store.load_run(run_id)
        if state.get("skipped"):
            raise ValueError("that run was skipped by triage; there is no plan to amend")
        brief = Brief.model_validate(state["brief"])
        entry = next((e for e in brief.ledger if e.id == item_id), None)
        if entry is None:
            known = ", ".join(e.id for e in brief.ledger) or "none"
            raise KeyError(f"no assumption '{item_id}' in run {run_id} (known: {known})")

        user = json.dumps(
            {
                "assumption": entry.model_dump(),
                "new_evidence": evidence,
                "request": brief.restatement,
                "success_criteria": brief.success_criteria,
            },
            indent=2,
        )
        self.log("re-checking the assumption")
        decision = await self.llm.structured("reopen", prompts.REOPEN, user, ReopenDecision)

        new_value: str | None = None
        verdict = decision.verdict
        if verdict == ReopenVerdict.flip and decision.new_value.strip():
            new_value = decision.new_value.strip()
        elif verdict == ReopenVerdict.ask and self.asker.interactive:
            options = decision.question_options or [entry.assumption.rsplit("→ ", 1)[-1].strip()]
            q = Question(entry.id, entry.assumption.split(" → ")[0], options, options[0], decision.reason)
            raw = (await self.asker.ask([q])).get(entry.id)
            _, value = _classify_answer(raw, options[0])
            new_value = value if value.strip().lower() != options[0].strip().lower() else None

        if new_value is None:
            note = f"The assumption '{item_id}' stands: {decision.reason}"
            return RunResult(run_id, False, note, None, state.get("audit", {}), self.llm.usage.snapshot())

        prefix = entry.assumption.rsplit(" → ", 1)[0] if " → " in entry.assumption else entry.assumption
        entry.assumption = f"{prefix} → {new_value}"
        entry.source = f"reopened ({evidence[:60]})"
        self.store.log("flip", run_id=run_id, id=item_id, topic_key=entry.topic_key, new_value=new_value)

        previous = Decision.from_dict(state.get("decision"))
        self.log("writing the amended plan")
        plan = await self._contract(brief, previous)
        if decision.changes_approach:
            plan.open_items.append(
                "This change may affect which approach is best; re-run the full pipeline with --force to re-compare."
            )
        revision = int(state.get("revision", 1)) + 1
        state.update(
            {
                "revision": revision,
                "brief": brief.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "usage": self.llm.usage.snapshot(),
            }
        )
        self.store.save_run(run_id, state)
        return RunResult(run_id, False, render_plan(plan), plan, state.get("audit", {}), state["usage"])
