"""LLM access: one narrow protocol and its Anthropic implementation.

The pipeline only ever talks to ``LLM``. ``AnthropicBackend`` is the real
implementation (official SDK, async). Tests substitute a scripted fake.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from .config import Settings, supports_effort, supports_fallbacks
from .tools import LocalTools

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """A call failed in a way the pipeline cannot work around."""


class RefusedError(LLMError):
    def __init__(self, category: str | None, explanation: str | None):
        self.category = category
        self.explanation = explanation
        super().__init__(f"request declined by safety classifiers (category={category}): {explanation}")


class UsageTracker:
    FIELDS = ("calls", "input_tokens", "output_tokens", "cache_read_input_tokens")

    def __init__(self) -> None:
        self.by_role: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(self.FIELDS, 0))

    def add(self, role: str, usage: Any) -> None:
        row = self.by_role[role]
        row["calls"] += 1
        for name in self.FIELDS[1:]:
            row[name] += int(getattr(usage, name, 0) or 0)

    def total(self) -> dict[str, int]:
        out = dict.fromkeys(self.FIELDS, 0)
        for row in self.by_role.values():
            for k, v in row.items():
                out[k] += v
        return out

    def snapshot(self) -> dict[str, Any]:
        return {"total": self.total(), "by_role": {r: dict(v) for r, v in self.by_role.items()}}


class LLM(Protocol):
    usage: UsageTracker

    async def structured(
        self, role: str, system: str, user: str, schema: type[T], *, max_tokens: int | None = None
    ) -> T: ...

    async def text(self, role: str, system: str, user: str, *, max_tokens: int | None = None) -> str: ...

    async def research(
        self,
        role: str,
        system: str,
        user: str,
        *,
        tools: LocalTools | None = None,
        web: bool = True,
        max_iterations: int = 12,
    ) -> str: ...


def web_search_tool(model: str) -> dict[str, Any]:
    """The current web search server tool where supported, else the basic one."""
    if "haiku" in model:
        return {"type": "web_search_20250305", "name": "web_search", "max_uses": 8}
    return {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}


def text_of(message: Any) -> str:
    return "".join(b.text for b in message.content if getattr(b, "type", None) == "text").strip()


class AnthropicBackend:
    def __init__(
        self,
        settings: Settings | None = None,
        client: anthropic.AsyncAnthropic | None = None,
        usage: UsageTracker | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.client = client or anthropic.AsyncAnthropic(max_retries=3)
        self.usage = usage or UsageTracker()

    # -- request assembly ---------------------------------------------------

    def _api(self, role: str) -> tuple[Any, dict[str, Any]]:
        """The messages resource and any extra kwargs (beta fallbacks) for this role."""
        model = self.settings.models[role]
        if self.settings.fallbacks and supports_fallbacks(model):
            return self.client.beta.messages, {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        return self.client.messages, {}

    def _kwargs(self, role: str, extra: dict[str, Any], max_tokens: int | None) -> dict[str, Any]:
        model = self.settings.models[role]
        kw: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens or self.settings.max_tokens,
            **extra,
        }
        level = self.settings.effort.get(role)
        if level and supports_effort(model):
            kw["output_config"] = {"effort": level}
        return kw

    def _check(self, role: str, message: Any) -> None:
        self.usage.add(role, getattr(message, "usage", None))
        reason = getattr(message, "stop_reason", None)
        if reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise RefusedError(getattr(details, "category", None), getattr(details, "explanation", None))
        if reason == "max_tokens":
            raise LLMError(f"{role}: response truncated at max_tokens; raise Settings.max_tokens")

    async def _call(self, role: str, awaitable: Any) -> Any:
        """Await an SDK call, converting API-level failures to LLMError."""
        try:
            return await awaitable
        except anthropic.APIError as e:
            raise LLMError(f"{role}: {e}") from e

    # -- calls --------------------------------------------------------------

    async def structured(
        self, role: str, system: str, user: str, schema: type[T], *, max_tokens: int | None = None
    ) -> T:
        api, extra = self._api(role)
        message = await self._call(
            role,
            api.parse(
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                **self._kwargs(role, extra, max_tokens),
            ),
        )
        self._check(role, message)
        parsed = getattr(message, "parsed_output", None)
        if parsed is None:
            raise LLMError(f"{role}: no parsed output for {schema.__name__}")
        return parsed

    async def text(self, role: str, system: str, user: str, *, max_tokens: int | None = None) -> str:
        api, extra = self._api(role)
        message = await self._call(
            role,
            api.create(
                system=system,
                messages=[{"role": "user", "content": user}],
                **self._kwargs(role, extra, max_tokens),
            ),
        )
        self._check(role, message)
        return text_of(message)

    async def research(
        self,
        role: str,
        system: str,
        user: str,
        *,
        tools: LocalTools | None = None,
        web: bool = True,
        max_iterations: int = 12,
    ) -> str:
        """Tool loop: server-side web search plus sandboxed local read-only tools."""
        model = self.settings.models[role]
        tool_defs: list[dict[str, Any]] = []
        if web:
            tool_defs.append(web_search_tool(model))
        if tools is not None:
            tool_defs.extend(tools.definitions())
        if not tool_defs:
            raise LLMError("research requires web access or a working directory")

        api, extra = self._api(role)
        kwargs = self._kwargs(role, extra, None)
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]

        for _ in range(max_iterations):
            message = await self._call(
                role, api.create(system=system, messages=messages, tools=tool_defs, **kwargs)
            )
            self._check(role, message)
            if message.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": message.content})
                continue
            if message.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": message.content})
                results = []
                for block in message.content:
                    if getattr(block, "type", None) != "tool_use":
                        continue
                    if tools is None:
                        out, is_error = f"unknown tool: {block.name}", True
                    else:
                        out, is_error = tools.run(block.name, dict(block.input))
                    result: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id, "content": out}
                    if is_error:
                        result["is_error"] = True
                    results.append(result)
                messages.append({"role": "user", "content": results})
                continue
            return text_of(message)

        # Out of iterations: ask for a write-up of what was found, with no tools.
        messages.append(
            {
                "role": "user",
                "content": "Stop researching. Write up what you found so far, "
                "and say what you could not determine.",
            }
        )
        final = await self._call(role, api.create(system=system, messages=messages, **kwargs))
        self._check(role, final)
        return text_of(final)
