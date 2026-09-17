"""
Multi-provider LLM client.

One small surface — `LLMClient.complete_json(system, user)` — over four
providers (Anthropic, OpenAI, Google Gemini, Groq). Each provider's SDK quirks
are hidden here so the Phase 1/2 pipelines never branch on provider.

Keys live only in the caller (Streamlit session_state); nothing is written to
disk or logged here.
"""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any


PROVIDERS = ["Anthropic", "OpenAI", "Google (Gemini)", "Groq"]

# Per-provider hard ceiling on output tokens. We escalate max_tokens on JSON
# truncation, but must never request more than a provider/model actually allows
# or the call 400s. Anthropic Opus 4.8 / Sonnet 4.6 / Haiku 4.5 all support 64k
# output; 32k is a safe, generous cap. OpenAI gpt-4o caps at 16384.
PROVIDER_MAX_OUTPUT_TOKENS = {
    "Anthropic": 32000,
    "OpenAI": 16384,
    "Google (Gemini)": 32000,
    "Groq": 8000,
}

DEFAULT_MODELS = {
    "Anthropic": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    "OpenAI": ["gpt-4o", "gpt-4o-mini", "gpt-4.1"],
    "Google (Gemini)": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
    "Groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "moonshotai/kimi-k2-instruct"],
}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMClient:
    provider: str
    api_key: str
    model: str
    temperature: float = 0.3
    max_tokens: int = 8192
    # Transient 5xx/429 "overloaded" events from providers can persist for tens
    # of seconds. With the parallel question-drafter fan-out, a thin retry budget
    # meant a single overloaded section killed the whole multi-minute run. Be
    # patient: ~5 attempts with jittered exponential backoff rides out a blip.
    max_api_retries: int = 5
    max_json_retries: int = 2
    retry_base_seconds: float = 0.75
    retry_max_seconds: float = 30.0
    usage: dict[str, Any] = field(default_factory=lambda: {
        "calls": 0,
        "input_chars": 0,
        "output_chars": 0,
        "estimated_input_tokens": 0,
        "estimated_output_tokens": 0,
        "estimated_total_tokens": 0,
        "estimated_cost_usd": None,
    })

    # ---- public API ---------------------------------------------------------
    def complete_text(self, system: str, user: str, max_tokens: int | None = None) -> str:
        if not self.api_key:
            raise LLMError("No API key provided.")
        mt = int(max_tokens or self.max_tokens)
        # Never request more output than the provider/model allows, or the call 400s.
        cap = PROVIDER_MAX_OUTPUT_TOKENS.get(self.provider)
        if cap:
            mt = min(mt, cap)
        if self.provider == "Anthropic":
            text = self._with_api_retries(lambda: self._anthropic(system, user, mt))
        elif self.provider == "OpenAI":
            text = self._with_api_retries(lambda: self._openai(system, user, mt))
        elif self.provider == "Google (Gemini)":
            text = self._with_api_retries(lambda: self._gemini(system, user, mt))
        elif self.provider == "Groq":
            text = self._with_api_retries(lambda: self._groq(system, user, mt))
        else:
            raise LLMError(f"Unknown provider: {self.provider}")
        self._record_usage(system, user, text)
        return text

    def usage_snapshot(self) -> dict[str, Any]:
        """Return provider/model usage estimates for UI/logging.

        Token counts are character-based estimates because provider SDKs expose
        usage metadata differently. Cost is populated only when generic cost
        rates are configured through environment variables.
        """
        return {
            "provider": self.provider,
            "model": self.model,
            **dict(self.usage),
        }

    def usage_summary(self) -> dict[str, Any]:
        return self.usage_snapshot()

    def complete_json(self, system: str, user: str, expected_type: type | tuple[type, ...] | None = None) -> Any:
        """Call the model and parse JSON, retrying malformed or wrong-shaped output."""
        json_system = system + "\n\nRespond with valid JSON only. No markdown fences, no prose."
        prompt = user
        last_error: Exception | None = None
        for attempt in range(self.max_json_retries + 1):
            # Escalate the output budget on retries: the most common failure is a
            # truncated response (JSON cut off mid-string), not malformed JSON.
            mt = self.max_tokens if attempt == 0 else min(self.max_tokens * 2, 16000)
            try:
                raw = self.complete_text(json_system, prompt, max_tokens=mt)
                data = _extract_json(raw)
                if expected_type is not None and not isinstance(data, expected_type):
                    exp = ", ".join(t.__name__ for t in expected_type) if isinstance(expected_type, tuple) else expected_type.__name__
                    raise LLMError(f"Expected JSON type {exp}; got {type(data).__name__}.")
                return data
            except LLMError as exc:
                last_error = exc
                if attempt >= self.max_json_retries:
                    break
                prompt = (
                    user
                    + "\n\nYour previous response could not be used: "
                    + str(exc)[:700]
                    + "\nReturn ONLY valid JSON matching the requested schema."
                )
                self._sleep_before_retry(attempt)
        raise last_error or LLMError("Could not parse JSON from model response.")

    def test_connection(self) -> tuple[bool, str]:
        try:
            txt = self.complete_text("You are a connection tester.", "Reply with the single word OK.")
            return True, txt.strip()[:80]
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    def _record_usage(self, system: str, user: str, output: str) -> None:
        input_chars = len(system or "") + len(user or "")
        output_chars = len(output or "")
        in_tokens = max(1, round(input_chars / 4))
        out_tokens = max(1, round(output_chars / 4))
        self.usage["calls"] = int(self.usage.get("calls", 0) or 0) + 1
        self.usage["input_chars"] = int(self.usage.get("input_chars", 0) or 0) + input_chars
        self.usage["output_chars"] = int(self.usage.get("output_chars", 0) or 0) + output_chars
        self.usage["estimated_input_tokens"] = int(self.usage.get("estimated_input_tokens", 0) or 0) + in_tokens
        self.usage["estimated_output_tokens"] = int(self.usage.get("estimated_output_tokens", 0) or 0) + out_tokens
        self.usage["estimated_total_tokens"] = int(self.usage.get("estimated_total_tokens", 0) or 0) + in_tokens + out_tokens
        self.usage["estimated_cost_usd"] = _estimate_cost_from_env(
            int(self.usage["estimated_input_tokens"]), int(self.usage["estimated_output_tokens"])
        )

    # ---- retry helpers ------------------------------------------------------
    def _sleep_before_retry(self, attempt: int) -> None:
        # Exponential backoff with "equal jitter": half the window is fixed
        # (guarantees a real wait), half is random. The randomness de-synchronizes
        # parallel calls that hit the same overload at the same instant, so they
        # stop colliding on retry (avoids a thundering herd).
        backoff = min(self.retry_base_seconds * (2 ** attempt), self.retry_max_seconds)
        time.sleep(backoff / 2 + random.uniform(0, backoff / 2))

    def _with_api_retries(self, fn):
        last_error: Exception | None = None
        for attempt in range(self.max_api_retries + 1):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt >= self.max_api_retries or not _is_retryable_exception(exc):
                    raise
                self._sleep_before_retry(attempt)
        raise last_error or LLMError("LLM request failed.")

    # ---- providers ----------------------------------------------------------
    def _anthropic(self, system: str, user: str, max_tokens: int) -> str:
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        # Newer Anthropic models (Opus 4.7 / 4.8, Fable/Mythos) reject `temperature`
        # with a 400. It is optional on every model, so we omit it for Anthropic and
        # let the model use its default.
        resp = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    def _openai(self, system: str, user: str, max_tokens: int) -> str:
        import openai
        client = openai.OpenAI(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""

    def _gemini(self, system: str, user: str, max_tokens: int) -> str:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=self.api_key)
        resp = client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=self.temperature,
                max_output_tokens=max_tokens,
            ),
        )
        return resp.text or ""

    def _groq(self, system: str, user: str, max_tokens: int) -> str:
        import groq
        client = groq.Groq(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""


# ---- JSON extraction --------------------------------------------------------
def _extract_json(raw: str) -> Any:
    """Extract the first balanced JSON object/array from a model response."""
    if raw is None:
        raise LLMError("Empty model response.")
    text = raw.strip()
    if not text:
        raise LLMError("Empty model response.")
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [(idx, open_ch, close_ch) for open_ch, close_ch in (("{", "}"), ("[", "]")) if (idx := text.find(open_ch)) != -1]
    for start, open_ch, close_ch in sorted(starts, key=lambda x: x[0]):
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == open_ch:
                    depth += 1
                elif c == close_ch:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
    raise LLMError(f"Could not parse JSON from model response:\n{raw[:500]}")


def _estimate_cost_from_env(input_tokens: int, output_tokens: int) -> float | None:
    """Estimate cost if generic per-million-token rates are configured.

    Set LLM_INPUT_COST_PER_1M and LLM_OUTPUT_COST_PER_1M in the environment to
    activate cost estimates without hard-coding model prices in the app.
    """
    import os
    try:
        in_rate = float(os.environ.get("LLM_INPUT_COST_PER_1M", ""))
        out_rate = float(os.environ.get("LLM_OUTPUT_COST_PER_1M", ""))
    except ValueError:
        return None
    return round((input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate, 6)


def _is_retryable_exception(exc: Exception) -> bool:
    """Best-effort provider-agnostic transient-error classifier."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    try:
        status_int = int(status) if status is not None else None
    except Exception:  # noqa: BLE001
        status_int = None
    if status_int in {408, 409, 425, 429} or (status_int is not None and status_int >= 500):
        return True
    msg = str(exc).lower()
    transient_markers = [
        "rate limit", "rate_limit", "too many requests", "timeout", "timed out",
        "temporarily", "temporary", "overloaded", "server error", "service unavailable",
        "connection", "reset", "try again", "unavailable", "deadline exceeded",
    ]
    return any(marker in msg for marker in transient_markers)
