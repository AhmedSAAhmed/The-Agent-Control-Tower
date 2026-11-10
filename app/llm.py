"""LLM router over free tiers: Groq -> Gemini -> OpenRouter, with a scripted fallback.

All three speak the OpenAI chat-completions dialect, so one client covers them. A fleet-wide
rate limiter and per-provider cool-downs keep the demo inside free quotas; when every provider
is unavailable, hybrid mode hands the step to the agent's deterministic (scripted) brain.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field

import httpx

from . import config

# List-price equivalents in USD per 1M tokens (input, output). Free tiers cost $0 in fact;
# the tower reports what the same traffic would cost on the paid tier, which is what an
# operator budgets against in production.
PRICES = {
    "openai/gpt-oss-20b": (0.10, 0.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "google/gemma-4-31b-it:free": (0.10, 0.30),
    "meta-llama/llama-prompt-guard-2-86m": (0.03, 0.03),
    "scripted": (0.0, 0.0),
}


@dataclass
class Provider:
    name: str
    base_url: str
    key_env: str
    model: str
    json_mode: bool = True
    extra: dict = field(default_factory=dict)
    cooldown_until: float = 0.0

    @property
    def key(self) -> str:
        return config.env(self.key_env)


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    data: dict | None = None
    provider: str = "scripted"
    model: str = "scripted"
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    error: str = ""

    @property
    def cost_usd(self) -> float:
        pin, pout = PRICES.get(self.model, (0.10, 0.40))
        return (self.tokens_in * pin + self.tokens_out * pout) / 1_000_000


class RateLimiter:
    """Sliding one-minute window shared by the whole fleet."""

    def __init__(self, rpm: int):
        self.rpm = rpm
        self.calls: list[float] = []

    def try_acquire(self) -> bool:
        now = time.monotonic()
        self.calls = [t for t in self.calls if now - t < 60]
        if len(self.calls) >= self.rpm:
            return False
        self.calls.append(now)
        return True


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


INJECTION_PATTERNS = re.compile(
    r"ignore (all )?(previous|prior) instructions|system (notice|override|prompt)|maintenance mode|"
    r"export (the )?(full|all|entire)|you are now|developer mode|تجاهل (كل )?التعليمات", re.I)


class LLMRouter:
    def __init__(self, mode: str = config.LLM_MODE, rpm: int = config.LLM_RPM):
        self.mode = mode
        self.limiter = RateLimiter(rpm)
        self.providers = [
            Provider("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-20b",
                     extra={"reasoning_effort": "low"}),
            Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                     "GEMINI_API_KEY", "gemini-2.5-flash-lite"),
            Provider("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                     "google/gemma-4-31b-it:free", json_mode=False),
        ]
        self.guard = Provider("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
                              "meta-llama/llama-prompt-guard-2-86m", json_mode=False)
        self._client = httpx.AsyncClient(timeout=30, headers={"User-Agent": "agent-control-tower/1.0"})
        self._sem = asyncio.Semaphore(2)
        self.stats = {"calls": 0, "fallbacks": 0, "errors": 0}

    def available(self) -> list[str]:
        return [p.name for p in self.providers if p.key]

    async def _call(self, p: Provider, messages: list[dict], max_tokens: int) -> LLMResult:
        payload = {"model": p.model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
        if p.json_mode:
            payload["response_format"] = {"type": "json_object"}
        payload.update(p.extra)
        started = time.monotonic()
        resp = await self._client.post(f"{p.base_url}/chat/completions", json=payload,
                                       headers={"Authorization": f"Bearer {p.key}"})
        latency = int((time.monotonic() - started) * 1000)
        if resp.status_code == 429:
            p.cooldown_until = time.monotonic() + 60
            return LLMResult(False, provider=p.name, model=p.model, latency_ms=latency, error="429 rate-limited")
        if resp.status_code >= 400:
            p.cooldown_until = time.monotonic() + 20
            return LLMResult(False, provider=p.name, model=p.model, latency_ms=latency,
                             error=f"HTTP {resp.status_code}")
        body = resp.json()
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        usage = body.get("usage") or {}
        return LLMResult(True, text=text, provider=p.name, model=p.model, latency_ms=latency,
                         tokens_in=int(usage.get("prompt_tokens") or 0),
                         tokens_out=int(usage.get("completion_tokens") or 0))

    async def complete_json(self, system: str, user: str, max_tokens: int = 700) -> LLMResult:
        """Ask for a JSON object. Returns ok=False (with the reason) when no provider delivered."""
        if self.mode == "scripted":
            return LLMResult(False, error="scripted mode")
        if not self.limiter.try_acquire():
            self.stats["fallbacks"] += 1
            return LLMResult(False, error="fleet LLM rate cap reached")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error = "no provider keys configured"
        async with self._sem:
            for p in self.providers:
                if not p.key or time.monotonic() < p.cooldown_until:
                    continue
                try:
                    result = await self._call(p, messages, max_tokens)
                except (httpx.HTTPError, ValueError) as exc:
                    p.cooldown_until = time.monotonic() + 20
                    result = LLMResult(False, provider=p.name, model=p.model, error=type(exc).__name__)
                if result.ok:
                    result.data = _parse_json(result.text)
                    if result.data is not None:
                        self.stats["calls"] += 1
                        return result
                    result.ok, result.error = False, "unparseable JSON"
                last_error = f"{p.name}: {result.error}"
                self.stats["errors"] += 1
        self.stats["fallbacks"] += 1
        return LLMResult(False, error=last_error)

    async def injection_score(self, text: str) -> tuple[float, str]:
        """Probability that `text` is a prompt-injection/jailbreak. Llama Prompt Guard 2 when
        reachable, otherwise a pattern heuristic. Returns (score, detector)."""
        heuristic = 0.97 if INJECTION_PATTERNS.search(text) else 0.02
        if self.mode == "scripted" or not self.guard.key or time.monotonic() < self.guard.cooldown_until:
            return heuristic, "pattern-heuristic"
        try:
            result = await self._call(self.guard, [{"role": "user", "content": text[:2000]}], 8)
            if result.ok:
                return float(result.text.strip()), "llama-prompt-guard-2-86m"
        except (httpx.HTTPError, ValueError):
            self.guard.cooldown_until = time.monotonic() + 30
        return heuristic, "pattern-heuristic"

    async def aclose(self) -> None:
        await self._client.aclose()
