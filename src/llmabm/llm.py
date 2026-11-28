"""LLM backends.

Everything real speaks the OpenAI chat-completions format, which Groq, Gemini,
OpenRouter, Cerebras, Mistral, Ollama and most free tiers expose. A `mock`
backend lets the whole pipeline run offline with no keys.
"""

from __future__ import annotations

import hashlib
import http.client
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
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml


class LLMError(RuntimeError):
    """A model call that could not be made or gave nothing usable, after any retries."""

    pass


class DailyLimit(LLMError):
    """The provider's daily cap for this model is used up: retrying today is pointless."""


_DAILY = re.compile(r"per day|\b(TPD|RPD)\b", re.I)


@dataclass
class Completion:
    """One reply from a chat backend, with what it cost."""

    text: str
    cached: bool = False
    tokens: int = (
        0  # prompt + completion (reasoning included) as the provider reported it; 0 if cached
    )
    reasoning: str = ""  # the model's hidden reasoning, when the provider returns it
    reasoning_tokens: int = (
        0  # as reported, or estimated from the reasoning text; kept when cached
    )


class ChatBackend(Protocol):
    """What the simulation needs of a chat model: a name, an id for the cache key, and `complete`."""

    name: str
    model_id: str

    def complete(
        self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 600
    ) -> Completion:
        """The model's reply to a chat."""
        ...


class Embedder(Protocol):
    """What the simulation needs of an embedding model: a name, an id for the cache key, and `embed`."""

    name: str
    model_id: str

    def embed(self, texts: list[str], role: str = "passage") -> list[list[float]]:
        """One unit vector per text; `role` is "query" for a situation and "passage" for a memory."""
        ...


_THINK = re.compile(r"<think>(.*?)</think>", re.S)


def split_reasoning(msg: dict, usage: dict) -> tuple[str, str, int]:
    """(reply, reasoning, reasoning tokens) from one chat-completions message. Providers return
    reasoning in a separate field (gpt-oss on Groq: `reasoning`) or inline as <think>...</think>.
    """
    content = msg.get("content") or ""
    parts = [
        msg.get("reasoning") or msg.get("reasoning_content") or ""
    ] + _THINK.findall(content)
    reasoning = "\n".join(p.strip() for p in parts if p and p.strip())
    content = _THINK.sub("", content).strip() if "<think>" in content else content
    n = ((usage or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
    return content, reasoning, int(n if n is not None else round(len(reasoning) / 4))


class RateLimiter:
    """Keeps under a free tier's per-minute caps: spaces requests evenly (rpm), and keeps the
    tokens sent in any 60 seconds under tpm. Each request reserves its prompt plus its full
    completion budget, then the reservation is corrected to what the provider says it used.
    """

    def __init__(self, rpm: float | None, tpm: float | None = None) -> None:
        """A limiter for `rpm` requests and `tpm` tokens a minute; None or 0 means no cap."""
        self.interval = 60.0 / rpm if rpm else 0.0
        self.tpm = tpm
        self._lock = threading.Lock()
        self._next = 0.0
        self._window: deque[list[float]] = (
            deque()
        )  # [time, tokens] in the last minute, oldest first

    def wait(self, tokens: float = 0.0) -> list[float] | None:
        """Sleep until a request reserving `tokens` may be sent.

        Returns:
            The reservation, to hand to `settle` once the real count is known; None when there is
            no token cap.
        """
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
                while self._window and now - self._window[0][0] >= 60:
                    self._window.popleft()
                if sum(w[1] for w in self._window) + tokens <= self.tpm:
                    entry = [now, tokens]
                    self._window.append(entry)
                    return entry
                delay = 60 - (now - self._window[0][0]) + 0.05
            time.sleep(delay)

    def settle(self, entry: list[float] | None, tokens: float) -> None:
        """Correct a reservation from `wait` to the tokens the request really used."""
        if entry is not None:
            with self._lock:
                entry[1] = tokens


class OpenAICompatible:
    """A chat model behind an OpenAI-compatible `/chat/completions` endpoint, rate limited and retried."""

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
    ) -> None:
        """Set up the backend from its entry in the models file.

        Args:
            name: The backend's name in the models file.
            base_url: The API root, without `/chat/completions`.
            model: The provider's name for the model.
            api_key_env: The environment variable holding the key; None when no key is needed.
            rpm: Requests a minute allowed; None for no cap.
            tpm: Tokens a minute allowed; None for no cap.
            json_mode: Ask the provider for a JSON object as the reply.
            timeout: Seconds to wait for one reply.
            max_retries: Tries after the first before giving up.
            headers: Extra HTTP headers.
            params: Extra request fields, sent with every call and part of the cache key.
        """
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.json_mode = json_mode
        self.timeout = timeout
        self.max_retries = max_retries
        self.headers = headers or {}
        self.params = (
            params or {}
        )  # extra request fields, e.g. {"reasoning_effort": "low"}
        self.limiter = RateLimiter(rpm, tpm)
        self.model_id = f"{self.base_url}|{model}" + (
            f"|{json.dumps(self.params, sort_keys=True)}" if self.params else ""
        )

    def complete(
        self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 600
    ) -> Completion:
        """The model's reply to a chat.

        Raises:
            DailyLimit: The provider's daily cap for this model is used up.
            LLMError: The key is missing, or every try failed.
        """
        headers = self._headers()
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **self.params,
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        budget = (
            len(json.dumps(messages)) / 4 + max_tokens
        )  # rough tokens, before the provider counts them

        def read(data: dict) -> tuple[Completion, int]:
            """The reply in the provider's JSON, and the tokens it used."""
            usage = data.get("usage") or {}
            text, reasoning, n_reason = split_reasoning(
                data["choices"][0]["message"], usage
            )
            used = int(usage.get("total_tokens") or budget)
            return (
                Completion(
                    text,
                    tokens=used,
                    reasoning=reasoning,
                    reasoning_tokens=n_reason,
                ),
                used,
            )

        return self._post("/chat/completions", body, headers, budget, read)

    def _headers(self) -> dict[str, str]:
        """The HTTP headers of a request, with the key; raises LLMError when the key is not set."""
        key = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        if self.api_key_env and not key:
            raise LLMError(
                f"backend '{self.name}': set {self.api_key_env} in your environment or .env"
            )
        # Some providers sit behind Cloudflare, which rejects Python's default User-Agent with
        # "error code: 1010" before the request ever reaches the API.
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "llm-abm",
            **self.headers,
        }
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _post[T](
        self,
        path: str,
        body: dict,
        headers: dict,
        budget: float,
        read: Callable[[dict], tuple[T, int]],
    ) -> T:
        """One request, with the rate limiter and the retries. `read(data)` turns the provider's JSON into
        (result, tokens used); a reply it can't read counts as a failed try.
        """
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
                    raise DailyLimit(
                        f"backend '{self.name}' ({self.model}): daily limit reached: {detail}"
                    ) from e
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
                    print(
                        f"    ({self.name}: {why}, waiting {delay:.0f}s, try {attempt + 2} of {self.max_retries + 1})",
                        file=sys.stderr,
                        flush=True,
                    )
                    time.sleep(delay)
                    continue
                raise LLMError(f"backend '{self.name}': HTTP {e.code}: {detail}") from e
            # OSError and HTTPException cover a connection dropped while waiting for or reading the reply
            # (RemoteDisconnected, ConnectionResetError, IncompleteRead), which urllib does not wrap in URLError.
            except (
                OSError,
                http.client.HTTPException,
                KeyError,
                IndexError,
                TypeError,
                json.JSONDecodeError,
            ) as e:
                if attempt < self.max_retries:
                    time.sleep(min(60.0, 2**attempt))
                    continue
                raise LLMError(f"backend '{self.name}': {e!r}") from e
        raise LLMError(f"backend '{self.name}': retries exhausted")


class MockBackend:
    """Offline stand-in. Routes each island prompt by its PHASE line to a canned but
    valid random reply. Deterministic given the prompt.
    """

    name = "mock"
    model_id = "mock"

    def complete(
        self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 600
    ) -> Completion:
        """The canned reply to the last message, drawn with an rng seeded from its text."""
        from .island import mock as island_mock

        prompt = messages[-1]["content"]
        rng = random.Random(hashlib.sha256(prompt.encode()).hexdigest())
        # Prompt version 19: open the Island 101 about one time in four it is there, closed. Decided from the
        # prompt and not from `rng`, so the reply to a prompt is drawn as it was before.
        if (
            "\nThe Island 101 (closed): " in prompt
            and hashlib.sha256(prompt.encode()).digest()[0] < 64
        ):
            return Completion('{"open": "island 101"}')
        routes = {
            "PHASE: plan": island_mock.plan_reply,
            "PHASE: isabelle": island_mock.isabelle_reply,
            "PHASE: encounter": island_mock.encounter_reply,
            "PHASE: notice": island_mock.notice_reply,
            "PHASE: reflect": island_mock.reflect_reply,
            "PHASE: spot": island_mock.spot_reply,
        }
        fn = next(
            (f for k, f in routes.items() if prompt.startswith(k)),
            island_mock.evening_reply,
        )
        return Completion(fn(prompt, rng))


def _cache_file(cache_dir: Path, key: list) -> Path:
    """The file a cache keeps `key`'s value in: named by the key's hash, in a folder of its first two
    characters so that no folder grows huge.
    """
    digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    return cache_dir / digest[:2] / f"{digest}.json"


def _models_file(config_path: Path | None) -> dict:
    """The models file as a dict; empty when there is none."""
    if config_path and Path(config_path).exists():
        return yaml.safe_load(Path(config_path).read_text()) or {}
    return {}


class Cached:
    """Disk cache keyed on (model, prompt, sampling params, salt). The salt is the
    run seed, so the same seed replays a run for free and a new seed resamples. With `refresh`
    (`--no-cache`) nothing is read from the cache: every call is made, and its reply replaces
    whatever was kept for that prompt, so a later replay gets the new replies. An empty reply is
    never kept or replayed: a resumed run asks again.
    """

    def __init__(
        self,
        inner: ChatBackend,
        cache_dir: Path,
        salt: str = "",
        refresh: bool = False,
    ) -> None:
        """Wrap `inner` so that its replies are kept under `cache_dir`."""
        self.refresh = refresh
        self.inner = inner
        self.name = inner.name
        self.model_id = inner.model_id
        self.dir = Path(cache_dir)
        self.salt = salt

    def complete(
        self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 600
    ) -> Completion:
        """The kept reply to this exact call when there is one, else a fresh one, which is then kept."""
        path = self._path(messages, temperature, max_tokens)
        if path.exists() and not self.refresh:
            d = json.loads(path.read_text())
            if d["text"].strip():  # an empty one kept by an older version: ask again
                return Completion(
                    d["text"],
                    cached=True,
                    reasoning=d.get("reasoning", ""),
                    reasoning_tokens=d.get("reasoning_tokens", 0),
                )
        c = self.inner.complete(
            messages, temperature=temperature, max_tokens=max_tokens
        )
        if not c.text.strip():
            return c  # nothing to replay: the next run asks again
        self.store(messages, temperature, max_tokens, c)
        return c

    def _path(self, messages: list[dict], temperature: float, max_tokens: int) -> Path:
        """Where the reply to this exact call is kept."""
        return _cache_file(
            self.dir, [self.model_id, messages, temperature, max_tokens, self.salt]
        )

    def store(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        reply: Completion,
    ) -> None:
        """Keep `reply` as the answer to this exact call, replacing any kept before."""
        path = self._path(messages, temperature, max_tokens)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "model": self.model_id,
                    "text": reply.text,
                    "reasoning": reply.reasoning,
                    "reasoning_tokens": reply.reasoning_tokens,
                }
            )
        )


def load_backends(
    config_path: Path | None,
    cache_dir: Path | None = None,
    salt: str = "",
    refresh: bool = False,
) -> dict[str, ChatBackend]:
    """Returns {name: backend}. 'mock' is always present; 'default' aliases the configured default."""
    backends: dict[str, ChatBackend] = {"mock": MockBackend()}
    cfg = _models_file(config_path)
    default = cfg.get("default", "mock")
    for name, spec in (cfg.get("backends") or {}).items():
        backends[name] = OpenAICompatible(name=name, **spec)
    if cache_dir is not None:
        backends = {
            n: (
                b if isinstance(b, MockBackend) else Cached(b, cache_dir, salt, refresh)
            )
            for n, b in backends.items()
        }
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
    `query_params` and `passage_params` are sent with each kind (NVIDIA's `input_type`, for one).
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        model: str,
        api_key_env: str | None = None,
        rpm: float | None = None,
        tpm: float | None = None,
        timeout: float = 90,
        max_retries: int = 5,
        headers: dict | None = None,
        params: dict | None = None,
        batch: int = 64,
        query_params: dict | None = None,
        passage_params: dict | None = None,
    ) -> None:
        """Set up the embedder from its entry in the models file.

        The arguments shared with `OpenAICompatible` mean the same here.

        Args:
            batch: Texts sent in one request.
            query_params: Extra request fields when embedding a situation.
            passage_params: Extra request fields when embedding a text to be matched.
        """
        super().__init__(
            name,
            base_url,
            model,
            api_key_env=api_key_env,
            rpm=rpm,
            tpm=tpm,
            timeout=timeout,
            max_retries=max_retries,
            headers=headers,
            params=params,
        )
        self.batch = max(1, int(batch))
        self.role_params = {
            "query": query_params or {},
            "passage": passage_params or {},
        }
        if query_params or passage_params:
            self.model_id += "|" + json.dumps(self.role_params, sort_keys=True)

    def embed(self, texts: list[str], role: str = "passage") -> list[list[float]]:
        """One unit vector per text, in order, asked for in batches."""
        headers = self._headers()
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = texts[i : i + self.batch]
            body = {
                "model": self.model,
                "input": chunk,
                **self.params,
                **self.role_params.get(role, {}),
            }
            budget = sum(len(t) for t in chunk) / 4

            def read(
                data: dict,
                n: int = len(chunk),  # noqa: B008  # bound now, to this chunk
                budget: float = budget,
            ) -> tuple[list[list[float]], int]:
                """The chunk's vectors in the order sent, and the tokens used."""
                rows = sorted(data["data"], key=lambda r: r.get("index", 0))
                if len(rows) != n:
                    raise KeyError(f"{len(rows)} embeddings for {n} texts")
                return [_unit([float(x) for x in r["embedding"]]) for r in rows], int(
                    (data.get("usage") or {}).get("total_tokens") or budget
                )

            out += self._post("/embeddings", body, headers, budget, read)
        return out


_WORD = re.compile(r"[a-z0-9']+")


class HashEmbeddings:
    """An embedder that needs no model and no network: each word and each pair of neighbouring words is
    hashed into one of `dims` buckets, so two texts are close when they share words. It is what tests and
    `mock` runs use, and a fallback when no embedding model is set up. It matches names, items and places
    well and misses paraphrases. The same text gives the same vector on every machine.
    """

    name = "hash"

    def __init__(self, dims: int = 256) -> None:
        """An embedder giving vectors of `dims` numbers."""
        self.dims = int(dims)
        self.model_id = f"hash-{self.dims}"

    def embed(self, texts: list[str], role: str = "passage") -> list[list[float]]:
        """One unit vector per text; `role` makes no difference."""
        out = []
        for text in texts:
            words = _WORD.findall(text.lower())
            vec = [0.0] * self.dims
            for term in words + [f"{a} {b}" for a, b in zip(words, words[1:])]:
                h = hashlib.sha256(term.encode()).digest()
                vec[int.from_bytes(h[:4], "big") % self.dims] += (
                    1.0 if h[4] & 1 else -1.0
                )
            out.append(_unit(vec))
        return out


class LocalEmbeddings:
    """An embedding model run on this machine, in this process, with the `fastembed` package (ONNX, no
    PyTorch): no server, no key, nothing leaves the machine. The model file is downloaded once into
    `model_dir`. It is only loaded when a text has to be embedded that isn't in the cache yet.
    `query_prefix` is put before a situation (some models want a short instruction there); none by default.
    """

    def __init__(
        self,
        name: str = "local",
        model: str = "BAAI/bge-small-en-v1.5",
        model_dir: str = ".cache/fastembed",
        batch: int = 64,
        threads: int | None = None,
        query_prefix: str = "",
    ) -> None:
        """Note which model to use; nothing is loaded or downloaded yet."""
        self.name, self.model, self.model_dir = name, model, model_dir
        self.batch, self.threads, self.query_prefix = (
            max(1, int(batch)),
            threads,
            query_prefix,
        )
        self.model_id = f"fastembed:{model}" + (
            f"|{query_prefix}" if query_prefix else ""
        )
        self._model: Any = None

    def _load(self) -> Any:
        """The fastembed model, loaded on first use; raises LLMError when it can't be."""
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as e:
                raise LLMError(
                    "the local embedding model needs the `fastembed` package: pip install fastembed"
                ) from e
            try:
                self._model = TextEmbedding(
                    model_name=self.model,
                    cache_dir=self.model_dir,
                    threads=self.threads,
                )
            except (
                Exception
            ) as e:  # not a model fastembed knows, or its first download failed
                raise LLMError(
                    f"couldn't load the local embedding model '{self.model}': {e}"
                ) from e
        return self._model

    def embed(self, texts: list[str], role: str = "passage") -> list[list[float]]:
        """One unit vector per text; a "query" gets `query_prefix` put before it."""
        if not texts:
            return []
        model = self._load()
        if role == "query":
            got = model.query_embed(
                [self.query_prefix + t for t in texts], batch_size=self.batch
            )
        else:
            got = model.passage_embed(texts, batch_size=self.batch)
        return [_unit([float(x) for x in v]) for v in got]


class CachedEmbeddings:
    """Keeps every vector on disk, keyed on (model, role, text). A text is embedded once: later runs, and a
    resume that replays a day, read the same vector back. There is no seed in the key, because an embedding
    depends only on the text.
    """

    def __init__(self, inner: Embedder, cache_dir: Path) -> None:
        """Wrap `inner` so that its vectors are kept under `cache_dir`."""
        self.inner = inner
        self.name = inner.name
        self.model_id = inner.model_id
        self.api_key_env = getattr(inner, "api_key_env", None)
        self.dir = Path(cache_dir)

    def _path(self, role: str, text: str) -> Path:
        """Where the vector of this text in this role is kept."""
        return _cache_file(self.dir, [self.model_id, role, text])

    def embed(self, texts: list[str], role: str = "passage") -> list[list[float]]:
        """One vector per text: read back when kept, else embedded by `inner` and kept."""
        found: dict[str, list[float]] = {}
        missing: list[str] = []
        for t in dict.fromkeys(texts):  # each text once, in the order first seen
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
                found[t] = json.loads(
                    json.dumps(vec)
                )  # exactly what a later read gives
        return [found[t] for t in texts]


def load_embedder(
    config_path: Path | None, cache_dir: Path | None = None, name: str | None = None
) -> Embedder:
    """The embedder named `name`, or the models file's `embeddings.default`, or the hash embedder when
    nothing is configured. `hash` and `mock` always mean the hash embedder.
    """
    cfg = _models_file(config_path).get("embeddings") or {}
    name = name or cfg.get("default") or "hash"
    if name in ("hash", "mock"):
        return HashEmbeddings()
    spec = (cfg.get("backends") or {}).get(name)
    if spec is None:
        raise LLMError(
            f"embeddings backend '{name}' is not defined under `embeddings:` in {config_path}"
        )
    spec = dict(spec)
    kind = spec.pop("kind", "api")
    if kind not in ("api", "fastembed"):
        raise LLMError(
            f"embeddings backend '{name}' has kind '{kind}'; it can be 'fastembed' (a model run in this "
            "process) or left out (an OpenAI-compatible /embeddings endpoint)"
        )
    inner = (
        LocalEmbeddings(name=name, **spec)
        if kind == "fastembed"
        else Embeddings(name=name, **spec)
    )
    return CachedEmbeddings(inner, cache_dir) if cache_dir is not None else inner
