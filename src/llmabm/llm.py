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
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml


class LLMError(RuntimeError):
    pass


@dataclass
class Completion:
    text: str
    cached: bool = False
    latency: float = 0.0


class RateLimiter:
    """Spaces requests evenly so a free-tier requests-per-minute cap is respected."""

    def __init__(self, rpm: float | None):
        self.interval = 60.0 / rpm if rpm else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            start = max(time.monotonic(), self._next)
            self._next = start + self.interval
        delay = start - time.monotonic()
        if delay > 0:
            time.sleep(delay)


class OpenAICompatible:
    def __init__(
        self,
        name: str,
        base_url: str,
        model: str,
        api_key_env: str | None = None,
        rpm: float | None = None,
        json_mode: bool = False,
        timeout: float = 90,
        max_retries: int = 5,
        headers: dict | None = None,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.json_mode = json_mode
        self.timeout = timeout
        self.max_retries = max_retries
        self.headers = headers or {}
        self.limiter = RateLimiter(rpm)
        self.model_id = f"{self.base_url}|{model}"

    def complete(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 600) -> Completion:
        key = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        if self.api_key_env and not key:
            raise LLMError(f"backend '{self.name}': set {self.api_key_env} in your environment or .env")

        body = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json", **self.headers}
        if key:
            headers["Authorization"] = f"Bearer {key}"

        t0 = time.monotonic()
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            req = urllib.request.Request(
                self.base_url + "/chat/completions",
                data=json.dumps(body).encode(),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read())
                text = data["choices"][0]["message"].get("content") or ""
                return Completion(text, latency=time.monotonic() - t0)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:500]
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    ra = e.headers.get("Retry-After", "")
                    try:
                        delay = float(ra)
                    except ValueError:
                        delay = min(60.0, 2**attempt + random.random())
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
        routes = {"PHASE: plan": island_mock.plan_reply, "PHASE: arrival": island_mock.arrival_reply,
                  "PHASE: isabelle": island_mock.isabelle_reply, "PHASE: encounter": island_mock.encounter_reply,
                  "PHASE: notice": island_mock.notice_reply, "PHASE: reflect": island_mock.reflect_reply}
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
            return Completion(json.loads(path.read_text())["text"], cached=True)
        c = self.inner.complete(messages, temperature=temperature, max_tokens=max_tokens)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": self.model_id, "text": c.text}))
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
