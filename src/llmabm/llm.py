"""LLM backends.

Everything real speaks the OpenAI chat-completions format, which Groq, Gemini,
OpenRouter, Cerebras, Mistral, Ollama and most free tiers expose. A `mock`
backend lets the whole pipeline run offline with no keys.
"""

from __future__ import annotations

import hashlib
import json
import math
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
        headers = self._headers()
        body = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                **self.params}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        t0 = time.monotonic()
        budget = len(json.dumps(messages)) / 4 + max_tokens  # rough tokens, before the provider counts them

        def read(data: dict) -> tuple[Completion, int]:
            usage = data.get("usage") or {}
            text, reasoning, n_reason = split_reasoning(data["choices"][0]["message"], usage)
            used = int(usage.get("total_tokens") or budget)
            return Completion(text, latency=time.monotonic() - t0, tokens=used, reasoning=reasoning,
                              reasoning_tokens=n_reason), used

        return self._post("/chat/completions", body, headers, budget, read)

    def _headers(self) -> dict:
        key = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        if self.api_key_env and not key:
            raise LLMError(f"backend '{self.name}': set {self.api_key_env} in your environment or .env")
        # Some providers sit behind Cloudflare, which rejects Python's default User-Agent with
        # "error code: 1010" before the request ever reaches the API.
        headers = {"Content-Type": "application/json", "User-Agent": "llm-abm", **self.headers}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _post(self, path: str, body: dict, headers: dict, budget: float, read):
        """One request, with the rate limiter and the retries. `read(data)` turns the provider's JSON into
        (result, tokens used); a reply it can't read counts as a failed try."""
        for attempt in range(self.max_retries + 1):
            slot = self.limiter.wait(budget)
            req = urllib.request.Request(
                self.base_url + path,
                data=json.dumps(body).encode(),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read())
                result, used = read(data)
                self.limiter.settle(slot, used)
                return result
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
            except (urllib.error.URLError, TimeoutError, KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
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
        # Prompt version 19: open the Island 101 about one time in four it is there, closed. Decided from the
        # prompt and not from `rng`, so the reply to a prompt is drawn as it was before.
        if "\nThe Island 101 (closed): " in prompt and hashlib.sha256(prompt.encode()).digest()[0] < 64:
            return Completion('{"open": "island 101"}')
        routes = {"PHASE: plan": island_mock.plan_reply,
                  "PHASE: isabelle": island_mock.isabelle_reply, "PHASE: encounter": island_mock.encounter_reply,
                  "PHASE: notice": island_mock.notice_reply, "PHASE: reflect": island_mock.reflect_reply,
                  "PHASE: spot": island_mock.spot_reply}
        fn = next((f for k, f in routes.items() if prompt.startswith(k)), island_mock.evening_reply)
        return Completion(fn(prompt, rng))


class Cached:
    """Disk cache keyed on (model, prompt, sampling params, salt). The salt is the
    run seed, so the same seed replays a run for free and a new seed resamples. With `refresh`
    (`--no-cache`) nothing is read from the cache: every call is made, and its reply replaces
    whatever was kept for that prompt, so a later replay gets the new replies."""

    def __init__(self, inner, cache_dir: Path, salt: str = "", refresh: bool = False):
        self.refresh = refresh
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
        if path.exists() and not self.refresh:
            d = json.loads(path.read_text())
            return Completion(d["text"], cached=True, reasoning=d.get("reasoning", ""),
                              reasoning_tokens=d.get("reasoning_tokens", 0))
        c = self.inner.complete(messages, temperature=temperature, max_tokens=max_tokens)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": self.model_id, "text": c.text, "reasoning": c.reasoning,
                                    "reasoning_tokens": c.reasoning_tokens}))
        return c


def load_backends(config_path: Path | None, cache_dir: Path | None = None, salt: str = "",
                  refresh: bool = False) -> dict:
    """Returns {name: backend}. 'mock' is always present; 'default' aliases the configured default."""
    backends: dict = {"mock": MockBackend()}
    default = "mock"
    if config_path and Path(config_path).exists():
        cfg = yaml.safe_load(Path(config_path).read_text()) or {}
        default = cfg.get("default", default)
        for name, spec in (cfg.get("backends") or {}).items():
            backends[name] = OpenAICompatible(name=name, **spec)
    if cache_dir is not None:
        backends = {n: (b if isinstance(b, MockBackend) else Cached(b, cache_dir, salt, refresh)) for n, b in backends.items()}
    if default not in backends:
        raise LLMError(f"default backend '{default}' is not defined in {config_path}")
    backends["default"] = backends[default]
    return backends


# --- embeddings ------------------------------------------------------------
# Text -> vector, for retrieving memories by what they are about (island/memory.py). An embedder is
# never a chat backend and is configured apart from them, under `embeddings:` in the models file.

def _unit(vec: list[float]) -> list[float]:
    """The vector at length 1, rounded: the same numbers whether it is fresh or read back from the cache."""
    norm = math.sqrt(math.fsum(x * x for x in vec))
    return [round(x / norm, 6) if norm else 0.0 for x in vec]


class Embeddings(OpenAICompatible):
    """An OpenAI-compatible `/embeddings` endpoint: a local server (Ollama, LM Studio, llama.cpp) or a
    hosted one (Groq has none). Some models embed a question differently from the text it is matched against:
    `query_params` and `passage_params` are sent with each kind (NVIDIA's `input_type`, for one)."""

    def __init__(self, name: str, base_url: str, model: str, api_key_env: str | None = None,
                 rpm: float | None = None, tpm: float | None = None, timeout: float = 90, max_retries: int = 5,
                 headers: dict | None = None, params: dict | None = None, batch: int = 64,
                 query_params: dict | None = None, passage_params: dict | None = None):
        super().__init__(name, base_url, model, api_key_env=api_key_env, rpm=rpm, tpm=tpm, timeout=timeout,
                         max_retries=max_retries, headers=headers, params=params)
        self.batch = max(1, int(batch))
        self.role_params = {"query": query_params or {}, "passage": passage_params or {}}
        if query_params or passage_params:
            self.model_id += "|" + json.dumps(self.role_params, sort_keys=True)

    def embed(self, texts: list[str], role: str = "passage") -> list[list[float]]:
        headers = self._headers()
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = texts[i:i + self.batch]
            body = {"model": self.model, "input": chunk, **self.params, **self.role_params.get(role, {})}
            budget = sum(len(t) for t in chunk) / 4

            def read(data: dict, n=len(chunk), budget=budget):
                rows = sorted(data["data"], key=lambda r: r.get("index", 0))
                if len(rows) != n:
                    raise KeyError(f"{len(rows)} embeddings for {n} texts")
                return [_unit([float(x) for x in r["embedding"]]) for r in rows], int((data.get("usage") or {}).get("total_tokens") or budget)

            out += self._post("/embeddings", body, headers, budget, read)
        return out


_WORD = re.compile(r"[a-z0-9']+")


class HashEmbeddings:
    """An embedder that needs no model and no network: each word and each pair of neighbouring words is
    hashed into one of `dims` buckets, so two texts are close when they share words. It is what tests and
    `mock` runs use, and a fallback when no embedding model is set up. It matches names, items and places
    well and misses paraphrases. The same text gives the same vector on every machine."""

    name = "hash"

    def __init__(self, dims: int = 256):
        self.dims = int(dims)
        self.model_id = f"hash-{self.dims}"

    def embed(self, texts: list[str], role: str = "passage") -> list[list[float]]:
        out = []
        for text in texts:
            words = _WORD.findall(text.lower())
            vec = [0.0] * self.dims
            for term in words + [f"{a} {b}" for a, b in zip(words, words[1:])]:
                h = hashlib.sha256(term.encode()).digest()
                vec[int.from_bytes(h[:4], "big") % self.dims] += 1.0 if h[4] & 1 else -1.0
            out.append(_unit(vec))
        return out


class LocalEmbeddings:
    """An embedding model run on this machine, in this process, with the `fastembed` package (ONNX, no
    PyTorch): no server, no key, nothing leaves the machine. The model file is downloaded once into
    `model_dir`. It is only loaded when a text has to be embedded that isn't in the cache yet.
    `query_prefix` is put before a situation (some models want a short instruction there); none by default."""

    def __init__(self, name: str = "local", model: str = "BAAI/bge-small-en-v1.5", model_dir: str = ".cache/fastembed",
                 batch: int = 64, threads: int | None = None, query_prefix: str = ""):
        self.name, self.model, self.model_dir = name, model, model_dir
        self.batch, self.threads, self.query_prefix = max(1, int(batch)), threads, query_prefix
        self.model_id = f"fastembed:{model}" + (f"|{query_prefix}" if query_prefix else "")
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as e:
                raise LLMError("the local embedding model needs the `fastembed` package: pip install fastembed") from e
            try:
                self._model = TextEmbedding(model_name=self.model, cache_dir=self.model_dir, threads=self.threads)
            except Exception as e:  # not a model fastembed knows, or its first download failed
                raise LLMError(f"couldn't load the local embedding model '{self.model}': {e}") from e
        return self._model

    def embed(self, texts: list[str], role: str = "passage") -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        if role == "query":
            got = model.query_embed([self.query_prefix + t for t in texts], batch_size=self.batch)
        else:
            got = model.passage_embed(texts, batch_size=self.batch)
        return [_unit([float(x) for x in v]) for v in got]


class CachedEmbeddings:
    """Keeps every vector on disk, keyed on (model, role, text). A text is embedded once: later runs, and a
    resume that replays a day, read the same vector back. There is no seed in the key, because an embedding
    depends only on the text."""

    def __init__(self, inner, cache_dir: Path):
        self.inner = inner
        self.name = inner.name
        self.model_id = inner.model_id
        self.api_key_env = getattr(inner, "api_key_env", None)
        self.dir = Path(cache_dir)

    def _path(self, role: str, text: str) -> Path:
        key = hashlib.sha256(json.dumps([self.model_id, role, text]).encode()).hexdigest()
        return self.dir / key[:2] / f"{key}.json"

    def embed(self, texts: list[str], role: str = "passage") -> list[list[float]]:
        found: dict[str, list[float]] = {}
        missing: list[str] = []
        for t in texts:
            if t in found or t in missing:
                continue
            path = self._path(role, t)
            if path.exists():
                found[t] = json.loads(path.read_text())
            else:
                missing.append(t)
        if missing:
            for t, vec in zip(missing, self.inner.embed(missing, role)):
                path = self._path(role, t)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(vec))
                found[t] = json.loads(json.dumps(vec))  # exactly what a later read gives
        return [found[t] for t in texts]


def load_embedder(config_path: Path | None, cache_dir: Path | None = None, name: str | None = None):
    """The embedder named `name`, or the models file's `embeddings.default`, or the hash embedder when
    nothing is configured. `hash` and `mock` always mean the hash embedder."""
    cfg = {}
    if config_path and Path(config_path).exists():
        cfg = (yaml.safe_load(Path(config_path).read_text()) or {}).get("embeddings") or {}
    name = name or cfg.get("default") or "hash"
    if name in ("hash", "mock"):
        return HashEmbeddings()
    spec = (cfg.get("backends") or {}).get(name)
    if spec is None:
        raise LLMError(f"embeddings backend '{name}' is not defined under `embeddings:` in {config_path}")
    spec = dict(spec)
    kind = spec.pop("kind", "api")
    if kind not in ("api", "fastembed"):
        raise LLMError(f"embeddings backend '{name}' has kind '{kind}'; it can be 'fastembed' (a model run in this "
                       "process) or left out (an OpenAI-compatible /embeddings endpoint)")
    inner = LocalEmbeddings(name=name, **spec) if kind == "fastembed" else Embeddings(name=name, **spec)
    return CachedEmbeddings(inner, cache_dir) if cache_dir is not None else inner
