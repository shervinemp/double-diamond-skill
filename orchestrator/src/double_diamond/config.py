"""Model and behavior settings, overridable through the environment.

Defaults: Claude Opus 5.5 for reasoning-heavy roles, Claude Sonnet 5.5 for the
cheap gate and for the parallel candidate generators. The judge runs on a
different model than the candidates so it does not grade its own work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"

ROLES = (
    "triage",
    "direct",
    "expand",
    "research",
    "lenses",
    "candidate",
    "rubric",
    "judge",
    "contract",
    "reopen",
    "baseline",
    "eval_judge",
)

DEFAULT_MODELS: dict[str, str] = {
    "triage": SONNET,
    "direct": OPUS,
    "expand": OPUS,
    "research": OPUS,
    "lenses": OPUS,
    "candidate": SONNET,
    "rubric": OPUS,
    "judge": OPUS,
    "contract": OPUS,
    "reopen": OPUS,
    "baseline": OPUS,
    "eval_judge": OPUS,
}

# Effort is set explicitly because defaults differ by model (Opus 5.5 defaults to medium).
DEFAULT_EFFORT: dict[str, str] = {
    "triage": "low",
    "direct": "medium",
    "expand": "medium",
    "research": "medium",
    "lenses": "medium",
    "candidate": "medium",
    "rubric": "low",
    "judge": "high",
    "contract": "medium",
    "reopen": "medium",
    "baseline": "medium",
    "eval_judge": "medium",
}

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def supports_effort(model: str) -> bool:
    """Effort is accepted on the current Opus, Sonnet and Fable models, not Haiku 4.5."""
    return "haiku" not in model


def supports_fallbacks(model: str) -> bool:
    """Server-side refusal fallbacks apply to these models on the Claude API."""
    return model.startswith(
        ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
    )


@dataclass(frozen=True)
class Settings:
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    effort: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_EFFORT))
    fallbacks: bool = True
    max_tokens: int = 16000
    max_candidates: int = 4
    ask_cap: int = 3
    max_grafts: int = 2

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        models = dict(DEFAULT_MODELS)
        if env.get("DD_MODEL"):
            models = {role: env["DD_MODEL"] for role in ROLES}
        effort = dict(DEFAULT_EFFORT)
        for role in ROLES:
            if env.get(f"DD_MODEL_{role.upper()}"):
                models[role] = env[f"DD_MODEL_{role.upper()}"]
            level = env.get(f"DD_EFFORT_{role.upper()}")
            if level:
                if level not in EFFORT_LEVELS:
                    raise ValueError(
                        f"DD_EFFORT_{role.upper()}={level!r}; expected one of {EFFORT_LEVELS}"
                    )
                effort[role] = level
        fallbacks = env.get("DD_FALLBACKS", "1").strip().lower() not in ("0", "false", "no", "off")
        return cls(models=models, effort=effort, fallbacks=fallbacks)
