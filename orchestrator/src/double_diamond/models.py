"""Schemas for every structured exchange.

Models that the LLM fills in (everything passed as ``output_format``) have no
defaults: every field is required so the generated JSON schema stays within
what structured outputs accept. Models that only the harness uses may have
defaults.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    coding = "coding"
    writing = "writing"
    research_decision = "research_decision"
    recommendation = "recommendation"
    other = "other"


class Bucket(str, Enum):
    discoverable = "discoverable"
    default = "default"
    ask = "ask"
    irrelevant = "irrelevant"


class Confidence(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class EvidenceGrade(str, Enum):
    verified = "verified"
    corroborated = "corroborated"
    platform_rating_only = "platform_rating_only"
    unverified = "unverified"


# --------------------------------------------------------------------------
# LLM-facing schemas (all fields required)
# --------------------------------------------------------------------------


class Triage(BaseModel):
    ambiguous_or_multiple_approaches: bool = Field(
        description="True if the request is ambiguous or has several viable approaches."
    )
    costly_if_wrong: bool = Field(
        description="True if a wrong result is irreversible, outward-facing, a security, "
        "privacy or data-loss risk, or expensive to redo."
    )
    money_or_sensitive_data: bool = Field(
        description="True if money or personal or sensitive documents change hands."
    )
    task_type: TaskType
    reason: str = Field(description="One sentence.")


class DecisionItem(BaseModel):
    id: str = Field(description="Short unique kebab-case id.")
    topic_key: str = Field(
        description="Stable lowercase kebab-case key for the underlying preference, "
        "such as package-manager or tone-of-voice, so repeated answers can be recognized."
    )
    question: str
    bucket: Bucket
    default: str = Field(
        description="The conventional default, or the recommended option for an ask item. "
        "Empty only for irrelevant items."
    )
    options: list[str] = Field(description="Options to offer if this is an ask item; else empty.")
    why_it_matters: str
    changes_plan: bool = Field(
        description="True if different answers would lead to a different approach or "
        "invalidate the plan, not just tweak a step."
    )
    toss_up: bool = Field(
        description="True only if you cannot call the answer with roughly 80% confidence."
    )
    costly_if_wrong: bool
    obvious_to_user: bool = Field(
        description="True if the user would likely answer 'obviously' or 'just do the sensible thing'."
    )
    regret: int = Field(description="1 (trivial) to 5 (severe): cost of guessing wrong.")
    rationale: str = Field(description="One line on why this bucket and default.")


class BriefDraft(BaseModel):
    restatement: str = Field(description="The request in one sentence.")
    success_criteria: list[str] = Field(description="What done looks like.")
    scope: list[str]
    non_goals: list[str]
    constraints: list[str]
    items: list[DecisionItem]
    viable_approaches: list[str] = Field(
        description="Genuinely distinct high-level approaches. Empty if one is clearly best."
    )
    hard_to_reverse: bool
    tradeoff_not_obvious: bool
    money_involved: bool
    sensitive_data: bool


class Resolution(BaseModel):
    id: str
    answer: str
    evidence: str = Field(description="File path or URL that supports the answer.")
    confident: bool = Field(description="True only if the notes clearly settle the question.")


class Resolutions(BaseModel):
    items: list[Resolution]


class Lens(BaseModel):
    name: str
    priority: str = Field(description="The real priority a reasonable person might pick.")
    sacrifices: str = Field(description="What this lens gives up.")


class LensSet(BaseModel):
    lenses: list[Lens]


class Candidate(BaseModel):
    approach: str = Field(description="Two or three sentences.")
    key_decisions: list[str]
    first_steps: list[str]
    sacrifices: list[str]
    risks: list[str]
    effort: str = Field(description="S, M or L with one line of why.")
    portable_ideas: list[str] = Field(
        description="One or two ideas that would strengthen a different approach, "
        "written to stand alone."
    )


class Criterion(BaseModel):
    name: str
    weight: int
    description: str


class RubricDraft(BaseModel):
    criteria: list[Criterion]


class CriterionScore(BaseModel):
    criterion: str = Field(description="Exactly the rubric criterion name.")
    score: int = Field(description="1 to 5.")
    justification: str


class CandidateScore(BaseModel):
    label: str
    criteria_scores: list[CriterionScore]
    disqualified: bool = Field(description="True if a hard constraint or non-goal is violated.")
    disqualification_reason: str


class Graft(BaseModel):
    from_label: str
    idea: str
    fixes_weakness_of_winner: str = Field(
        description="The specific weakness of the winner this fixes. Empty if none."
    )
    compatible: bool
    compatibility_note: str


class Verdict(BaseModel):
    scores: list[CandidateScore]
    winner_label: str
    confidence: Confidence
    grafts: list[Graft]
    would_change_my_mind: str = Field(
        description="The cheapest evidence that could flip the ranking."
    )
    needs_user_value_call: str = Field(
        description="If the top two differ mainly on a values trade-off the brief does not "
        "settle, the exact question to ask; otherwise an empty string."
    )


class Risk(BaseModel):
    risk: str
    mitigation: str


class ContractOutput(BaseModel):
    goal_and_done: str
    steps: list[str]
    trade_offs: list[str] = Field(description="One line per rejected alternative.")
    risks: list[Risk] = Field(description="Top two or three from the pre-mortem.")
    open_items: list[str]


class Option(BaseModel):
    name: str
    price: str
    price_basis: str = Field(description="quoted, listed, estimated or unknown.")
    quality_evidence: str
    trust_evidence: str
    evidence_grade: EvidenceGrade
    red_flags: list[str]
    disqualifying: bool = Field(description="True if a red flag rules this option out.")
    tradeoff: str
    payment_note: str


class Shortlist(BaseModel):
    options: list[Option]
    recommended: str = Field(description="Exact name of the recommended option.")
    privacy_note: str
    user_should_check: list[str]
    caveats: list[str]


class ReopenVerdict(str, Enum):
    still_valid = "still_valid"
    flip = "flip"
    ask = "ask"


class ReopenDecision(BaseModel):
    verdict: ReopenVerdict
    new_value: str = Field(description="The new value if flipping; else an empty string.")
    reason: str
    changes_approach: bool = Field(
        description="True if the change means the chosen approach should be re-compared."
    )
    question_options: list[str] = Field(description="Options to offer if verdict is ask.")


class PairScores(BaseModel):
    fit: int
    surprises_avoided: int
    clarity: int
    scope_discipline: int


class PairJudgement(BaseModel):
    first: PairScores
    second: PairScores
    preferred: str = Field(description="first, second or tie.")
    notes: str


# --------------------------------------------------------------------------
# Harness-side models
# --------------------------------------------------------------------------


class WorkItem(DecisionItem):
    """A decision item plus where its final value came from."""

    source: str = "model"
    resolved_value: str | None = None


class LedgerEntry(BaseModel):
    id: str
    topic_key: str
    assumption: str
    why: str
    how_to_flip: str
    source: str


class Brief(BaseModel):
    restatement: str
    task_type: TaskType
    success_criteria: list[str]
    scope: list[str]
    non_goals: list[str]
    constraints: list[str]
    confirmed: list[str] = Field(default_factory=list)
    ledger: list[LedgerEntry] = Field(default_factory=list)
    viable_approaches: list[str] = Field(default_factory=list)
    hard_to_reverse: bool = False
    tradeoff_not_obvious: bool = False
    money_involved: bool = False
    sensitive_data: bool = False


class Plan(BaseModel):
    kind: str = "plan"  # "plan" or "shortlist"
    goal_and_done: str
    steps: list[str] = Field(default_factory=list)
    assumptions: list[LedgerEntry] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    trade_offs: list[str] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    open_items: list[str] = Field(default_factory=list)
    shortlist: Shortlist | None = None
    confidence_note: str | None = None
