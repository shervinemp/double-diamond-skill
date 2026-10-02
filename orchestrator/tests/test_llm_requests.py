"""Verify the exact requests AnthropicBackend sends, with the HTTP layer faked.

No network and no API key: a mock transport records each request body and
returns a canned Messages API response. This checks model ids, effort, the
structured-output format, the fallback beta, tool definitions and the
pause_turn / tool_use loop against what the real SDK would put on the wire.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import pytest

from double_diamond.config import Settings
from double_diamond.llm import AnthropicBackend, LLMError, RefusedError
from double_diamond.models import Triage
from double_diamond.tools import LocalTools
from fakes import run


def message(content: list[dict[str, Any]], stop_reason: str = "end_turn", **extra: Any) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 11, "output_tokens": 7},
        **extra,
    }


def text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


class Recorder:
    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        self.bodies.append(json.loads(request.content))
        body = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return httpx2.Response(200, json=body)


def backend(recorder: Recorder, settings: Settings | None = None) -> AnthropicBackend:
    client = anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(recorder)),
    )
    return AnthropicBackend(settings or Settings(), client)


TRIAGE_JSON = json.dumps(
    {
        "ambiguous_or_multiple_approaches": True,
        "costly_if_wrong": True,
        "money_or_sensitive_data": False,
        "task_type": "coding",
        "reason": "r",
    }
)


def test_structured_request_shape_with_fallbacks_on():
    rec = Recorder([message([text(TRIAGE_JSON)])])
    out = run(backend(rec).structured("triage", "SYS", "the request", Triage))
    assert out.task_type.value == "coding" and out.costly_if_wrong

    body, req = rec.bodies[0], rec.requests[0]
    assert body["model"] == "claude-sonnet-5-5"  # triage runs on the cheap gate model
    assert body["system"] == "SYS"
    assert body["messages"] == [{"role": "user", "content": "the request"}]
    assert body["output_config"]["effort"] == "low"
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert "thinking" not in body  # thinking cannot be disabled on these models; we never send it
    assert not {"temperature", "top_p", "top_k"} & body.keys()  # sampling params are rejected on these models
    assert body["fallbacks"] == "default"  # refusal fallbacks on by default
    assert "server-side-fallback-2026-07-01" in req.headers["anthropic-beta"]
    assert body["max_tokens"] == 16000


def test_fallbacks_can_be_switched_off_and_then_the_stable_endpoint_is_used():
    rec = Recorder([message([text(TRIAGE_JSON)])])
    settings = Settings.from_env({"DD_FALLBACKS": "0"})
    run(backend(rec, settings).structured("triage", "S", "u", Triage))
    assert "fallbacks" not in rec.bodies[0]
    assert "server-side-fallback" not in rec.requests[0].headers.get("anthropic-beta", "")


def test_env_overrides_models_and_effort():
    s = Settings.from_env({"DD_MODEL": "claude-opus-5-5", "DD_MODEL_JUDGE": "claude-sonnet-5-5", "DD_EFFORT_JUDGE": "max"})
    assert s.models["candidate"] == "claude-opus-5-5" and s.models["judge"] == "claude-sonnet-5-5"
    assert s.effort["judge"] == "max"
    with pytest.raises(ValueError):
        Settings.from_env({"DD_EFFORT_JUDGE": "extreme"})


def test_haiku_gets_no_effort_and_no_fallbacks():
    rec = Recorder([message([text(TRIAGE_JSON)])])
    run(backend(rec, Settings.from_env({"DD_MODEL_TRIAGE": "claude-haiku-4-5"})).structured("triage", "S", "u", Triage))
    body = rec.bodies[0]
    assert body["model"] == "claude-haiku-4-5"
    assert "effort" not in body.get("output_config", {}) and "fallbacks" not in body


def test_text_call_and_usage_tracking():
    rec = Recorder([message([text("hello "), text("world")])])
    b = backend(rec)
    assert run(b.text("direct", "S", "u")) == "hello world"
    assert b.usage.total()["input_tokens"] == 11 and b.usage.by_role["direct"]["calls"] == 1
    assert rec.bodies[0]["model"] == "claude-opus-5-5"
    assert "output_format" not in rec.bodies[0]


def test_refusal_is_raised_with_its_category():
    rec = Recorder([message([text("")], stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": "no"})])
    with pytest.raises(RefusedError) as e:
        run(backend(rec).text("direct", "S", "u"))
    assert e.value.category == "cyber"


def test_truncation_is_an_error_not_silent():
    rec = Recorder([message([text("partial")], stop_reason="max_tokens")])
    with pytest.raises(LLMError, match="truncated"):
        run(backend(rec).text("direct", "S", "u"))


def test_api_errors_become_llm_errors():
    def handler(request):
        return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})

    client = anthropic.AsyncAnthropic(api_key="bad", max_retries=0, http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    with pytest.raises(LLMError, match="authentication|x-api-key"):
        run(AnthropicBackend(Settings(), client).text("direct", "S", "u"))


def test_research_loop_handles_pause_turn_and_local_tool_calls(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("lock", encoding="utf-8")
    rec = Recorder(
        [
            message([text("searching")], stop_reason="pause_turn"),
            message([{"type": "tool_use", "id": "toolu_1", "name": "list_dir", "input": {"path": "."}}], stop_reason="tool_use"),
            message([text("Found pnpm-lock.yaml")]),
        ]
    )
    out = run(backend(rec).research("research", "S", "find the package manager", tools=LocalTools(tmp_path), web=True))
    assert out == "Found pnpm-lock.yaml"
    assert len(rec.bodies) == 3

    first_tools = rec.bodies[0]["tools"]
    types = [t.get("type") or t["name"] for t in first_tools]
    assert types[0] == "web_search_20260209"  # current web search variant for Opus 5.5
    assert {"read_file", "list_dir", "search"} <= {t["name"] for t in first_tools}

    # after pause_turn the assistant turn is sent back unchanged
    assert rec.bodies[1]["messages"][-1]["role"] == "assistant"
    # after tool_use the result is returned in a single user message with the matching id
    last = rec.bodies[2]["messages"][-1]
    assert last["role"] == "user" and last["content"][0]["type"] == "tool_result"
    assert last["content"][0]["tool_use_id"] == "toolu_1" and "pnpm-lock.yaml" in last["content"][0]["content"]


def test_research_tool_errors_are_returned_as_errors_not_raised(tmp_path):
    rec = Recorder(
        [
            message([{"type": "tool_use", "id": "toolu_9", "name": "read_file", "input": {"path": "../../etc/passwd"}}], stop_reason="tool_use"),
            message([text("could not read it")]),
        ]
    )
    run(backend(rec).research("research", "S", "u", tools=LocalTools(tmp_path), web=False))
    result = rec.bodies[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "outside" in result["content"]


def test_research_gives_up_gracefully_after_the_iteration_cap(tmp_path):
    tool_turn = message([{"type": "tool_use", "id": "toolu_1", "name": "list_dir", "input": {"path": "."}}], stop_reason="tool_use")
    rec = Recorder([tool_turn, tool_turn, message([text("Here is what I found")])])
    out = run(backend(rec).research("research", "S", "u", tools=LocalTools(tmp_path), web=False, max_iterations=2))
    assert out == "Here is what I found"
    assert "tools" not in rec.bodies[-1]  # the final write-up call has no tools


def test_research_requires_some_tool():
    with pytest.raises(LLMError):
        run(backend(Recorder([message([text("x")])])).research("research", "S", "u", tools=None, web=False))
