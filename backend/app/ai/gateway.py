"""Model Gateway (§61) — provider-agnostic chat with tool calling.

Providers: gemini (native function calling via REST), mock (scripted, tests).
Model profiles: fast | standard | reasoning — the TASK chooses the profile.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import settings
from app.core.errors import ExternalProviderError

MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "gemini": {
        "fast": {"model": "gemini-3.8-flash", "temperature": 0.2},
        "standard": {"model": "gemini-3.8-flash", "temperature": 0.4},
        "reasoning": {"model": "gemini-3.8-pro", "temperature": 0.3},
    },
    "mock": {
        "fast": {"model": "mock-fast"},
        "standard": {"model": "mock-standard"},
        "reasoning": {"model": "mock-reasoning"},
    },
}


@dataclass
class ModelResponse:
    text: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # [{name, args}]
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    provider: str = ""
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


class ModelProvider(Protocol):
    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
        model_profile: str,
    ) -> ModelResponse: ...


class GeminiProvider:
    provider = "gemini"

    def _model_for(self, model_profile: str) -> str:
        return MODEL_PROFILES["gemini"].get(model_profile, MODEL_PROFILES["gemini"]["standard"])["model"]

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
        model_profile: str,
    ) -> ModelResponse:
        model = self._model_for(model_profile)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        contents, system_instruction = self._to_gemini_contents(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
        }
        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if tools:
            body["tools"] = [{
                "functionDeclarations": [
                    {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                    }
                    for t in tools
                ]
            }]
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    url, json=body, headers={"x-goog-api-key": settings.gemini_api_key}
                )
        except httpx.HTTPError as exc:
            raise ExternalProviderError(f"Gemini unreachable: {exc}") from exc
        latency = int((time.monotonic() - started) * 1000)
        if resp.status_code >= 400:
            raise ExternalProviderError(f"Gemini error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return self._parse_response(data, model, latency)

    def _to_gemini_contents(self, messages: list[dict[str, Any]]):
        contents = []
        system_text = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                system_text.append(m["content"])
                continue
            if role == "tool":
                contents.append({
                    "role": "user",
                    "parts": [{"functionResponse": {
                        "name": m.get("name", "tool"),
                        "response": {"result": m["content"]},
                    }}],
                })
                continue
            gemini_role = "model" if role == "assistant" else "user"
            parts = [{"text": m.get("content") or ""}]
            for tc in m.get("tool_calls") or []:
                fc = {"name": tc["name"], "args": tc.get("args", {})}
                part = {"functionCall": fc}
                if tc.get("thought_signature"):
                    part["thoughtSignature"] = tc["thought_signature"]
                parts.append(part)
            contents.append({"role": gemini_role, "parts": parts})
        return contents, "\n\n".join(system_text) or None

    def _parse_response(self, data: dict[str, Any], model: str, latency: int) -> ModelResponse:
        candidates = data.get("candidates") or [{}]
        parts = (candidates[0].get("content") or {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if "text" in p)
        tool_calls = [
            {"name": p["functionCall"]["name"], "args": p["functionCall"].get("args", {}),
             "thought_signature": p.get("thoughtSignature")}
            for p in parts if "functionCall" in p
        ]
        usage = data.get("usageMetadata", {})
        return ModelResponse(
            text=text or None, tool_calls=tool_calls,
            input_tokens=int(usage.get("promptTokenCount", 0)),
            output_tokens=int(usage.get("candidatesTokenCount", 0)),
            model=model, provider=self.provider, latency_ms=latency, raw=data,
        )


class MockModelProvider:
    """Scripted provider for tests: pops canned ModelResponses in order."""

    provider = "mock"

    def __init__(self) -> None:
        self.script: list[ModelResponse] = []
        self.calls: list[dict[str, Any]] = []

    def script_response(self, *responses: ModelResponse) -> MockModelProvider:
        self.script.extend(responses)
        return self

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
        model_profile: str,
    ) -> ModelResponse:
        self.calls.append({"messages": messages, "tools": tools, "profile": model_profile})
        if self.script:
            resp = self.script.pop(0)
            resp.model = MODEL_PROFILES["mock"][model_profile]["model"]
            resp.provider = self.provider
            return resp
        return ModelResponse(text="OK", model="mock", provider="mock", latency_ms=1)


_gateway: ModelProvider | None = None


def get_model_provider() -> ModelProvider:
    global _gateway
    if _gateway is not None:
        return _gateway
    if settings.ai_provider == "gemini" and settings.gemini_api_key:
        _gateway = GeminiProvider()
    else:
        _gateway = MockModelProvider()
    return _gateway


def set_model_provider(provider: ModelProvider | None) -> None:
    """Test hook."""
    global _gateway
    _gateway = provider
