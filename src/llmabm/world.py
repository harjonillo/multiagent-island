"""The simulation loop.

Update scheme is synchronous: every agent acting at tick t sees only what was
delivered by the end of t-1 (plus seeds injected at t). Messages sent at t are
read at t+1. Agents act when they have mail, or spontaneously with probability
`activation`.
"""

from __future__ import annotations

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import networkx as nx

from .agent import (
    AgentState,
    Message,
    parse_action,
    parse_probe,
    probe_prompt,
    system_prompt,
    tick_prompt,
)
from .llm import LLMError
from .scenario import Scenario


class EventLog:
    def __init__(self, path: Path, mode: str = "w"):
        self.f = open(path, mode, encoding="utf-8")

    def __call__(self, type_: str, **data) -> None:
        self.f.write(json.dumps({"type": type_, **data}, ensure_ascii=False) + "\n")
        self.f.flush()

    def close(self) -> None:
        self.f.close()


class Simulation:
    def __init__(
        self,
        scenario: Scenario,
        backends: dict,
        run_dir: Path,
        seed: int = 0,
        backend_override: str | None = None,
        concurrency: int = 1,
        log_prompts: bool = False,
        verbose: bool = True,
    ):
        self.sc = scenario
        self.backends = backends
        self.run_dir = Path(run_dir)
        self.seed = seed
        self.override = backend_override
        self.concurrency = max(1, concurrency)
        self.log_prompts = log_prompts
        self.verbose = verbose
        self.state = {name: AgentState() for name in scenario.agents}
        self.inbox: dict[str, list[Message]] = {name: [] for name in scenario.agents}
        self.labels: dict[str, str] = {}  # message id -> sender label shown to agents
        self._next_id = 0
        self.systems = {n: system_prompt(a, scenario.world_brief) for n, a in scenario.agents.items()}

    # --- helpers ---------------------------------------------------------
    def contacts(self, name: str) -> list[str]:
        return sorted(self.sc.graph.neighbors(name))

    def backend_for(self, name: str):
        key = self.override or self.sc.agents[name].model
        if key not in self.backends:
            raise LLMError(f"agent {name} wants backend '{key}', which is not configured")
        return self.backends[key]

    def new_id(self) -> str:
        self._next_id += 1
        return f"m{self._next_id}"

    def rng(self, *parts) -> random.Random:
        return random.Random(f"{self.seed}|" + "|".join(map(str, parts)))

    def _resolve_targets(self, spec: list[str]) -> list[str]:
        names = list(self.sc.agents)
        out = []
        for t in spec:
            if t == "all":
                out += names
            elif t.startswith("random:"):
                out += self.rng("seed-target", t).sample(names, int(t.split(":")[1]))
            else:
                out.append(t)
        return list(dict.fromkeys(out))

    # --- main loop -------------------------------------------------------
    def run(self, steps: int | None = None) -> Path:
        steps = steps or self.sc.steps
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._write_manifest(steps)
        log = EventLog(self.run_dir / "events.jsonl")
        log("run_start", scenario=self.sc.name, seed=self.seed, steps=steps, time=time.time())

        for t in range(steps):
            for s in (s for s in self.sc.seeds if s.tick == t):
                for target in self._resolve_targets(s.to):
                    m = Message(id=f"seed:{s.id}", tick=t, sender="world", recipient=target, content=s.content, kind="seed")
                    self.labels[m.id] = s.source
                    self.inbox[target].append(m)
                    log("seed", **asdict(m), source=s.source)

            # Who acts this tick, and what they read (attention limit applies here).
            plans = {}
            for name, spec in self.sc.agents.items():
                box = self.inbox[name]
                self.inbox[name] = []
                if not box and self.rng("activate", t, name).random() >= spec.activation:
                    continue
                self.rng("attend", t, name).shuffle(box)
                read, dropped = box[: spec.attention], box[spec.attention :]
                if dropped:
                    log("dropped", tick=t, agent=name, ids=[m.id for m in dropped])
                plans[name] = read

            with ThreadPoolExecutor(self.concurrency) as pool:
                results = dict(zip(plans, pool.map(lambda kv: self._decide(t, *kv), plans.items())))

            outgoing: list[Message] = []
            n_err = 0
            for name in plans:  # apply in a fixed order so ids are deterministic
                read, reply, action, err = results[name]
                st = self.state[name]
                st.seen_ids.update(m.id for m in read)
                if err:
                    n_err += 1
                log(
                    "turn",
                    tick=t,
                    agent=name,
                    read=[m.id for m in read],
                    beliefs=action["beliefs"] if action else st.beliefs,
                    note=action["note"] if action else "",
                    error=err,
                    raw=reply.text if reply else None,
                    cached=reply.cached if reply else False,
                    latency=round(reply.latency, 3) if reply else 0,
                )
                if not action:
                    continue
                if action["beliefs"]:
                    st.beliefs = action["beliefs"]
                if action["note"]:
                    st.notes.append((t, action["note"]))
                for m in self._validate_messages(t, name, action["messages"], log):
                    outgoing.append(m)
                    st.notes.append((t, f"you told {m.recipient}: {m.content[:160]}"))
                st.notes = st.notes[-self.sc.agents[name].memory_size :]

            for m in outgoing:
                self.inbox[m.recipient].append(m)
                log("message", **asdict(m))

            self._run_probes(t, log)
            if self.verbose:
                print(f"tick {t:>3}: {len(plans):>3} acted, {len(outgoing):>3} messages, {n_err} errors", flush=True)

        log("run_end", time=time.time())
        log.close()
        return self.run_dir

    def _decide(self, t: int, name: str, read: list[Message]):
        spec = self.sc.agents[name]
        user = tick_prompt(spec, self.state[name], t, self.contacts(name), read, self.labels)
        msgs = [{"role": "system", "content": self.systems[name]}, {"role": "user", "content": user}]
        if self.log_prompts:
            pdir = self.run_dir / "prompts"
            pdir.mkdir(exist_ok=True)
            (pdir / f"t{t:03d}_{name}.txt").write_text(f"=== SYSTEM ===\n{msgs[0]['content']}\n\n=== USER ===\n{user}\n")
        try:
            reply = self.backend_for(name).complete(msgs, self.sc.temperature, self.sc.max_tokens)
        except LLMError as e:
            return read, None, None, str(e)
        action, err = parse_action(reply.text)
        return read, reply, action, err

    def _validate_messages(self, t: int, name: str, raw_msgs: list[dict], log) -> list[Message]:
        contacts = self.contacts(name)
        by_lower = {c.lower(): c for c in contacts}
        seen = self.state[name].seen_ids
        out = []
        for raw in raw_msgs:
            content = str(raw.get("content") or "").strip()
            if not content:
                continue
            to = raw.get("to") or []
            to = to if isinstance(to, list) else [to]
            targets = []
            for x in to:
                x = str(x).strip()
                if x.lower() == "all":
                    targets += contacts
                elif x.lower() in by_lower:
                    targets.append(by_lower[x.lower()])
                else:
                    log("invalid_recipient", tick=t, agent=name, to=x)
            based = raw.get("based_on") or []
            based = [str(b) for b in (based if isinstance(based, list) else [based])]
            valid = [b for b in based if b in seen]
            if len(valid) < len(based):
                log("unverified_based_on", tick=t, agent=name, ids=[b for b in based if b not in seen])
            for r in dict.fromkeys(targets):
                out.append(Message(self.new_id(), t, name, r, content, valid))
        return out

    def _run_probes(self, t: int, log) -> None:
        for p in (p for p in self.sc.probes if t in p.ticks):

            def ask(name):
                spec = self.sc.agents[name]
                user = probe_prompt(spec, self.state[name], t, self.contacts(name), p.question, p.options)
                msgs = [{"role": "system", "content": self.systems[name]}, {"role": "user", "content": user}]
                try:
                    reply = self.backend_for(name).complete(msgs, self.sc.temperature, 20)
                    return name, reply.text, parse_probe(reply.text, p.options), None
                except LLMError as e:
                    return name, None, None, str(e)

            with ThreadPoolExecutor(self.concurrency) as pool:
                for name, raw, answer, err in pool.map(ask, list(self.sc.agents)):
                    log("probe", tick=t, probe=p.id, agent=name, answer=answer, raw=raw, error=err)

    def _write_manifest(self, steps: int) -> None:
        pos = nx.spring_layout(self.sc.graph, seed=self.seed, k=1.2 / max(1, len(self.sc.graph)) ** 0.5)
        manifest = {
            "scenario": self.sc.name,
            "description": self.sc.description,
            "seed": self.seed,
            "steps": steps,
            "backend_override": self.override,
            "agents": {
                n: {
                    "persona": a.persona,
                    "traits": a.traits,
                    "docs": [d[0] for d in a.docs],
                    "model": self.override or a.model,
                    "attention": a.attention,
                    "memory_size": a.memory_size,
                    "activation": a.activation,
                    "source": a.source,
                }
                for n, a in self.sc.agents.items()
            },
            "edges": [list(e) for e in self.sc.graph.edges],
            "positions": {n: [round(float(x), 4), round(float(y), 4)] for n, (x, y) in pos.items()},
            "seeds": [asdict(s) for s in self.sc.seeds],
            "probes": [asdict(p) for p in self.sc.probes],
            "scenario_config": self.sc.raw,
        }
        (self.run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
