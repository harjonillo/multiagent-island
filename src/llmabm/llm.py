"""LLM backends.

Everything real speaks the OpenAI chat-completions format, which Groq, Gemini,
OpenRouter, Cerebras, Mistral, Ollama and most free tiers expose. A `mock`
backend lets the whole pipeline run offline with no keys.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml


class LLMError(RuntimeError):
    pass


class DailyLimit(LLMError):
    """The provider's daily cap for this model is used up: retrying today is pointless."""


_DAILY = re.compile(r"per day|\b(TPD|RPD)\b", re.I)


@dataclass
class Completion:
    text: str
    cached: bool = False
    latency: float = 0.0
    tokens: int = 0  # prompt + completion (reasoning included) as the provider reported it; 0 if cached
    reasoning: str = ""        # the model's hidden reasoning, when the provider returns it
    reasoning_tokens: int = 0  # as reported, or estimated from the reasoning text; kept when cached


_THINK = re.compile(r"<think>(.*?)</think>", re.S)


def split_reasoning(msg: dict, usage: dict) -> tuple[str, str, int]:
    """(reply, reasoning, reasoning tokens) from one chat-completions message. Providers return
    reasoning in a separate field (gpt-oss on Groq: `reasoning`) or inline as <think>...</think>."""
    content = msg.get("content") or ""
    parts = [msg.get("reasoning") or msg.get("reasoning_content") or ""] + _THINK.findall(content)
    reasoning = "\n".join(p.strip() for p in parts if p and p.strip())
    content = _THINK.sub("", content).strip() if "<think>" in content else content
    n = ((usage or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
    return content, reasoning, int(n if n is not None else round(len(reasoning) / 4))


class RateLimiter:
    """Keeps under a free tier's per-minute caps: spaces requests evenly (rpm), and keeps the
    tokens sent in any 60 seconds under tpm. Each request reserves its prompt plus its full
    completion budget, then the reservation is corrected to what the provider says it used."""

    def __init__(self, rpm: float | None, tpm: float | None = None):
        self.interval = 60.0 / rpm if rpm else 0.0
        self.tpm = tpm
        self._lock = threading.Lock()
        self._next = 0.0
        self._window: list[list[float]] = []  # [time, tokens] in the last minute

    def wait(self, tokens: float = 0.0) -> list[float] | None:
        if self.interval:
            with self._lock:
                start = max(time.monotonic(), self._next)
                self._next = start + self.interval
            delay = start - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        if not self.tpm:
            return None
        tokens = min(tokens, self.tpm)
        while True:
            with self._lock:
                now = time.monotonic()
                self._window = [w for w in self._window if now - w[0] < 60]
                if sum(w[1] for w in self._window) + tokens <= self.tpm:
                    entry = [now, tokens]
                    self._window.append(entry)
                    return entry
                delay = 60 - (now - self._window[0][0]) + 0.05
            time.sleep(delay)

    def settle(self, entry: list[float] | None, tokens: float) -> None:
        if entry is not None:
            with self._lock:
                entry[1] = tokens


class OpenAICompatible:
    def __init__(
        self,
        name: str,
        base_url: str,
        model: str,
        api_key_env: str | None = None,
        rpm: float | None = None,
        tpm: float | None = None,
        json_mode: bool = False,
        timeout: float = 90,
        max_retries: int = 5,
        headers: dict | None = None,
        params: dict | None = None,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.json_mode = json_mode
        self.timeout = timeout
        self.max_retries = max_retries
        self.headers = headers or {}
        self.params = params or {}  # extra request fields, e.g. {"reasoning_effort": "low"}
        self.limiter = RateLimiter(rpm, tpm)
        self.model_id = f"{self.base_url}|{model}" + (f"|{json.dumps(self.params, sort_keys=True)}" if self.params else "")

    def complete(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 600) -> Completion:
        key = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        if self.api_key_env and not key:
            raise LLMError(f"backend '{self.name}': set {self.api_key_env} in your environment or .env")

        body = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                **self.params}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        # Some providers sit behind Cloudflare, which rejects Python's default User-Agent with
        # "error code: 1010" before the request ever reaches the API.
        headers = {"Content-Type": "application/json", "User-Agent": "llm-abm", **self.headers}
        if key:
            headers["Authorization"] = f"Bearer {key}"

        t0 = time.monotonic()
        budget = len(json.dumps(messages)) / 4 + max_tokens  # rough tokens, before the provider counts them
        for attempt in range(self.max_retries + 1):
            slot = self.limiter.wait(budget)
            req = urllib.request.Request(
                self.base_url + "/chat/completions",
                data=json.dumps(body).encode(),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read())
                usage = data.get("usage") or {}
                text, reasoning, n_reason = split_reasoning(data["choices"][0]["message"], usage)
                used = int(usage.get("total_tokens") or budget)
                self.limiter.settle(slot, used)
                return Completion(text, latency=time.monotonic() - t0, tokens=used, reasoning=reasoning,
                                  reasoning_tokens=n_reason)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:500]
                self.limiter.settle(slot, 0)
                if e.code == 429 and _DAILY.search(detail):
                    raise DailyLimit(f"backend '{self.name}' ({self.model}): daily limit reached: {detail}") from e
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    ra = e.headers.get("Retry-After", "")
                    try:
                        delay = float(ra)
                    except ValueError:
                        delay = 2**attempt + random.random()
                    # A long Retry-After means a daily cap: give up soon and let the run stop
                    # cleanly rather than sleep for hours.
                    delay = min(delay, 120.0)
                    why = "rate limited" if e.code == 429 else f"HTTP {e.code}"
                    print(f"    ({self.name}: {why}, waiting {delay:.0f}s, try {attempt + 2} of {self.max_retries + 1})",
                          file=sys.stderr, flush=True)
                    time.sleep(delay)
                    continue
                raise LLMError(f"backend '{self.name}': HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as e:
                if attempt < self.max_retries:
                    time.sleep(min(60.0, 2**attempt))
                    continue
                raise LLMError(f"backend '{self.name}': {e!r}") from e
        raise LLMError(f"backend '{self.name}': retries exhausted")


class MockBackend:
    """Offline stand-in. Routes each island prompt by its PHASE line to a canned but
    valid random reply. Deterministic given the prompt."""

    name = "mock"
    model_id = "mock"

    def complete(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 600) -> Completion:
        from .island import mock as island_mock

        prompt = messages[-1]["content"]
        rng = random.Random(hashlib.sha256(prompt.encode()).hexdigest())
        routes = {"PHASE: plan": island_mock.plan_reply,
                  "PHASE: isabelle": island_mock.isabelle_reply, "PHASE: encounter": island_mock.encounter_reply,
                  "PHASE: notice": island_mock.notice_reply, "PHASE: reflect": island_mock.reflect_reply,
                  "PHASE: spot": island_mock.spot_reply}
        fn = next((f for k, f in routes.items() if prompt.startswith(k)), island_mock.evening_reply)
        return Completion(fn(prompt, rng))


class Cached:
    """Disk cache keyed on (model, prompt, sampling params, salt). The salt is the
    run seed, so the same seed replays a run for free and a new seed resamples."""

    def __init__(self, inner, cache_dir: Path, salt: str = ""):
        self.inner = inner
        self.name = inner.name
        self.model_id = inner.model_id
        self.dir = Path(cache_dir)
        self.salt = salt

    def complete(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 600) -> Completion:
        key = hashlib.sha256(
            json.dumps([self.model_id, messages, temperature, max_tokens, self.salt], sort_keys=True).encode()
        ).hexdigest()
        path = self.dir / key[:2] / f"{key}.json"
        if path.exists():
            d = json.loads(path.read_text())
            return Completion(d["text"], cached=True, reasoning=d.get("reasoning", ""),
                              reasoning_tokens=d.get("reasoning_tokens", 0))
        c = self.inner.complete(messages, temperature=temperature, max_tokens=max_tokens)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": self.model_id, "text": c.text, "reasoning": c.reasoning,
                                    "reasoning_tokens": c.reasoning_tokens}))
        return c


def load_backends(config_path: Path | None, cache_dir: Path | None = None, salt: str = "") -> dict:
    """Returns {name: backend}. 'mock' is always present; 'default' aliases the configured default."""
    backends: dict = {"mock": MockBackend()}
    default = "mock"
    if config_path and Path(config_path).exists():
        cfg = yaml.safe_load(Path(config_path).read_text()) or {}
        default = cfg.get("default", default)
        for name, spec in (cfg.get("backends") or {}).items():
            backends[name] = OpenAICompatible(name=name, **spec)
    if cache_dir is not None:
        backends = {n: (b if isinstance(b, MockBackend) else Cached(b, cache_dir, salt)) for n, b in backends.items()}
    if default not in backends:
        raise LLMError(f"default backend '{default}' is not defined in {config_path}")
    backends["default"] = backends[default]
    return backends
