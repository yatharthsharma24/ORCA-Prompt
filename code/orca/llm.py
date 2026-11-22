"""LLM clients. Production uses a local Ollama server; tests use ScriptedLLM."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        stop: list[str] | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse: ...


class OllamaLLM:
    """Thin async client for Ollama's /api/chat endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        temperature: float = 0.1,
        num_ctx: int = 8192,
        num_thread: int | None = None,
        think: bool | None = False,
        timeout_s: float = 300.0,
        keep_alive: str = "30m",
        retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.num_thread = num_thread
        self.think = think
        self.keep_alive = keep_alive
        self.retries = retries
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=10.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        stop: list[str] | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        options: dict[str, Any] = {"temperature": self.temperature, "num_ctx": self.num_ctx}
        if self.num_thread:
            options["num_thread"] = self.num_thread
        if stop:
            options["stop"] = stop
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
            "keep_alive": self.keep_alive,
        }
        if json_schema is not None:
            body["format"] = json_schema
        if self.think is not None:
            body["think"] = self.think

        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = await self._client.post(f"{self.base_url}/api/chat", json=body)
                if resp.status_code == 400 and "think" in body and "think" in resp.text.lower():
                    # Model has no thinking support; retry once without the flag.
                    body.pop("think")
                    continue
                if resp.status_code == 404:
                    raise LLMError(
                        f"Ollama model '{model}' not found. Run: ollama pull {model}"
                    )
                resp.raise_for_status()
                data = resp.json()
                text = _THINK_RE.sub("", data.get("message", {}).get("content", "")).strip()
                return LLMResponse(
                    text=text,
                    model=model,
                    prompt_tokens=data.get("prompt_eval_count", 0) or 0,
                    completion_tokens=data.get("eval_count", 0) or 0,
                    duration_ms=int((data.get("total_duration", 0) or 0) / 1e6),
                )
            except LLMError:
                raise
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < self.retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    log.warning("Ollama call failed (%s), retrying", exc)
        raise LLMError(f"Ollama request failed at {self.base_url}: {last_exc}") from last_exc

    async def health(self, model: str) -> dict[str, Any]:
        """Report whether the server is reachable and the model is pulled."""
        try:
            resp = await self._client.get(f"{self.base_url}/api/tags", timeout=5.0)
            resp.raise_for_status()
            names = {m.get("name") for m in resp.json().get("models", [])}
            names |= {n.removesuffix(":latest") for n in names if n}
            return {"reachable": True, "model": model, "model_available": model in names}
        except Exception as exc:  # noqa: BLE001 - health check must never raise
            return {"reachable": False, "model": model, "model_available": False, "error": str(exc)}


@dataclass
class ScriptedLLM:
    """Deterministic LLM for tests: returns queued replies, records every call."""

    replies: list[str]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def chat(self, messages, *, model, stop=None, json_schema=None) -> LLMResponse:
        self.calls.append({"messages": list(messages), "model": model, "stop": stop, "json_schema": json_schema})
        if not self.replies:
            raise LLMError("ScriptedLLM ran out of replies")
        return LLMResponse(text=self.replies.pop(0), model=model)
