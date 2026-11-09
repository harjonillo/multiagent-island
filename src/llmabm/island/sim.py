"""Island simulation loop.

The island runs on a 24-hour clock that rolls over at 05:00. Each action takes one
in-game hour. Villagers plan at the start of each plan window (one LLM call each),
giving every action an hour. The engine then steps hour by hour. Villagers acting
in the same hour reach a place in order of distance from their home; equal
distances are shuffled. At the social hours villagers meet at the plaza to trade,
post on the board, and send letters and parcels, which arrive the next morning. From prompt
version 7 there are no social hours: the plaza is open all day, and a plan can set any free hour
aside for it.

On day 0 everyone arrives at once, each in a tent on a homesite drawn at random, with
one of each tool. At the end of every day Isabelle (an LLM call) writes a summary of
the day from the log, and the whole simulation is checkpointed so it can be resumed
later, one day at a time if you like.
"""

from __future__ import annotations

import json
import os
import pickle
import random
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from ..agent import AgentSpec, load_agent_file
from ..llm import DailyLimit, LLMError
from .prompts import (PROMPT_CHANGES, PROMPT_VERSION, BOARD_CHARS, TRADE_TRIES, _count, keep_chat, open_trade,
                      problem_text, trade_tries, INBOX_SHOWN, LETTER_CHARS, ISABELLE_SYSTEM, OFFER_DAYS,
                      REQUEST_DAYS, at_place, turn_act_text,
                      encounter_prompt, evening_prompt, plaza_prompt, spot_prompt, action_text,
                      announcement_text, inbox_text, isabelle_announce_prompt,
                      light_system_prompt, notice_prompt, reflect_prompt,
                      isabelle_summary_prompt, plan_prompt, system_prompt)
from .energy import MentalEnergy
from .memory import consolidate, forget, settings as memory_settings, split_picks
from .grid import GridSpace
from .body import Bodies
from .world import Island, clock, load_world, to_game_hour


class EventLog:
    def __init__(self, path: Path, mode: str = "w"):
        self.f = open(path, mode, encoding="utf-8")

    def __call__(self, type_: str, **data) -> None:
        self.f.write(json.dumps({"type": type_, **data}, ensure_ascii=False) + "\n")
        self.f.flush()

    def close(self) -> None:
        self.f.close()


@dataclass
class IslandScenario:
    name: str
    description: str
    world: dict
    days: int
    villagers: dict[str, AgentSpec]
    brief: str = ""
    start_bells: int = 1000
    memory_size: int = 15
    temperature: float = 0.8
    max_tokens: int = 1200
    free_time: str = "wander"    # "wander" (short random walks) or "home"
    isabelle: dict = field(default_factory=lambda: {"summary": True, "announce": False, "backend": None})
    reflection: dict = field(default_factory=lambda: {"enabled": False})
    mental_energy: dict = field(default_factory=dict)
    memory: dict = field(default_factory=dict)  # long-term memory settings; empty keeps the last memory_size notes
    raw: dict = field(default_factory=dict)


def load_island_scenario(path: Path, seed: int = 0) -> IslandScenario:
    path = Path(path)
    root = path.parent.parent if path.parent.name == "scenarios" else path.parent
    cfg = yaml.safe_load(path.read_text())
    villagers = {}
    entries = cfg["villagers"]
    if isinstance(entries, str):  # path to a cast file: a list of {name, <traits>...}
        entries = yaml.safe_load((root / entries).read_text())
    template = cfg.get("villager_template", "villagers/blank.md")
    for v in entries:
        v = dict(v)
        name = v.pop("name")
        file = v.pop("file", template)
        overrides = {**v.pop("set", {}), "trait_values": v}
        spec = load_agent_file(root / file, root, name=name, overrides=overrides)
        villagers[spec.name] = spec
    # Species get invented names and prices are jittered, so ACNH knowledge doesn't steer anyone.
    world = load_world(root / cfg.get("world", "data/island_world.yaml"), "perturbed", seed)
    world["calendar"].update(cfg.get("calendar") or {})  # scenario can override the clock
    world.setdefault("encounters", {}).update(cfg.get("encounters") or {})
    world.setdefault("grid", {}).update(cfg.get("grid") or {})
    world.setdefault("mental_energy", {}).update(cfg.get("mental_energy") or {})
    llm = cfg.get("llm", {})
    return IslandScenario(
        name=cfg.get("name", path.stem),
        description=cfg.get("description", ""),
        world=world,
        days=int(cfg.get("days", 30)),
        villagers=villagers,
        brief=cfg.get("brief", ""),
        start_bells=int(cfg.get("start_bells", 1000)),
        memory_size=int(cfg.get("memory_size", 15)),
        temperature=float(llm.get("temperature", 0.8)),
        max_tokens=int(llm.get("max_tokens", 1200)),
        free_time=str(cfg.get("free_time", "wander")),
        isabelle={"summary": True, "announce": False, "backend": None, **(cfg.get("isabelle") or {})},
        reflection={"enabled": False, "max_words": 150, **(cfg.get("reflection") or {})},
        mental_energy=dict(world["mental_energy"]),
        memory=dict(cfg.get("memory") or {}),
        raw=cfg,
    )


class RunAborted(RuntimeError):
    pass


@dataclass
class Offer:
    id: str
    day: int
    sender: str
    recipient: str
    give: dict
    want: dict
    note: str = ""
    status: str = "pending"  # pending | accepted | rejected | countered | failed | expired
    parent: str | None = None


def _failed_give(items: dict, taker: str, p: dict) -> str:
    """What both villagers see when a gift in person doesn't go through (prompt versions 8 to 11), from
    `Island.give_problem`: what was missing, said the same way to both of them."""
    if p.get("room"):
        return f"tried to give {_count(items)} but {taker} had no room"
    short = f"had only {p['have']}" if p["have"] else f"had no {p['item']}"
    return f"tried to give {items[p['item']]} {p['item']} but {short}"


MAX_CHAT_LINES = 40  # a conversation with no set length still stops here (it matters only without mental energy)


def _turn_cap(cfg: dict) -> int | None:
    """Lines a conversation can have: `turns` from the world's encounter settings; none (null) means no set
    number, so it goes on until someone leaves, says nothing or runs out of energy."""
    n = cfg.get("turns", 2)
    return None if n is None else int(n)


def _ref_id(x, prefix: str = "o") -> str:
    """An offer or board-post id as villagers write it: "o18", "[o18]", "18" or 18 are all offer o18."""
    x = str(x or "").strip().strip("[]").strip().lower()
    return f"{prefix}{x}" if x.isdigit() else x


def _clean_side(d) -> dict:
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out[str(k).strip().lower()] = n
    return out


class IslandSimulation:
    def __init__(self, sc: IslandScenario, backends: dict, run_dir: Path, seed: int = 0,
                 backend_override: str | None = None, concurrency: int = 1,
                 log_prompts: bool = False, verbose: bool = True):
        self.sc = sc
        self.backends = backends
        self.run_dir = Path(run_dir)
        self.seed = seed
        self.override = backend_override
        self.concurrency = max(1, concurrency)
        self.log_prompts = log_prompts
        self.verbose = verbose
        self.rng = random.Random(f"island|{seed}")
        self.isl = Island(sc.world, list(sc.villagers), self.rng, sc.start_bells)
        # The mind (LLM) plans; bodies walk and act on the tile island; the world applies the rules.
        self.space = GridSpace(sc.world, seed)
        self.isl.sites = {sid: {"area": v["area"], "at": list(v["door"])} for sid, v in self.space.sites.items()}
        self.space.attach(self.isl)
        self.bodies = Bodies(self.isl, self.space)
        for n, spec in sc.villagers.items():  # schedules come from the cast traits
            vil = self.isl.v[n]
            vil.wakes = int(spec.traits.get("wakes", vil.wakes))
            vil.sleeps = int(spec.traits.get("sleeps", vil.sleeps))
        # Mental energy: one daily token budget per villager, spent on whatever they choose.
        self.energy = MentalEnergy(sc.mental_energy, list(sc.villagers))
        self.isl.energy = self.energy
        self.isl.memory = memory_settings(sc.memory)
        self.prompt_version = self.isl.prompt_version = PROMPT_VERSION
        # Where each villager has chosen to spend energy next (set in their plans).
        self.choices = {n: {"plaza": True, "write": True, "next": "plan"} for n in sc.villagers}
        self.habits: dict[tuple[str, int], list[dict]] = {}
        self._build_systems()
        self.yesterday: dict[str, list[str]] = {n: [] for n in sc.villagers}
        self.offers: dict[str, Offer] = {}
        self._oid = 0
        self.calls = 0
        self.provider_tokens = 0  # what the providers say this run used (cached replies cost nothing)
        self.reasoning_tokens = 0  # hidden reasoning in all replies, cached ones included
        self.failures = 0
        self.max_failures = 15  # consecutive failed calls before giving up on the run
        # Everyone lands together at the start of day 0, on a homesite drawn at random.
        start = sc.world["calendar"]["day_start"]
        self.arrival_hour = {n: start for n in sc.villagers}
        self.noticed: dict[str, list[str]] = {n: [] for n in sc.villagers}

    # --- persistence -------------------------------------------------------
    def __getstate__(self):
        state = dict(self.__dict__)
        for k in ("backends", "log", "_raw_log"):
            state.pop(k, None)
        return state

    def _intervene(self, from_day: int, what: str, details: list[str]) -> None:
        """Record a deliberate mid-run change, in the events and in the manifest."""
        self.log("intervention", from_day=from_day, what=what, details=details, time=time.time())
        m = json.loads((self.run_dir / "manifest.json").read_text())
        m.setdefault("interventions", []).append({"from_day": from_day, "what": what, "details": details})
        (self.run_dir / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False))

    @property
    def v12(self) -> bool:
        """Prompt version 12 on: pockets and home apart, trades in person, numbered board posts."""
        return self.prompt_version >= 12

    def _log_favors_done(self, d: int) -> None:
        """Log the requests closed since the last call, and clear the list."""
        for f in self.isl.favors_done_today:
            self.log("favor_done", day=d, **{k: v for k, v in f.items() if k != "day"}, asked_day=f["day"])
        self.isl.favors_done_today = []

    def _checkpoint(self) -> None:
        self._log_offset = self._raw_log.f.tell()  # where the log ends for this checkpoint
        tmp = self.run_dir / "checkpoint.pkl.tmp"
        with open(tmp, "wb") as f:
            pickle.dump(self, f)
        os.replace(tmp, self.run_dir / "checkpoint.pkl")
        m = json.loads((self.run_dir / "manifest.json").read_text())
        m["days_completed"] = self.isl.day + 1
        (self.run_dir / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False))

    @classmethod
    def resume(cls, run_dir: Path, backends: dict, days: int = 1, backend_override: str | None = None,
               concurrency: int | None = None, verbose: bool = True, prompt_version: int | str | None = None) -> Path:
        """Continue a run from its last checkpoint for `days` more days, appending to the same logs.
        The run keeps the prompt wording it started with; `prompt_version` (a number, or "latest")
        switches it from the next day on, logged as an intervention.
        Only load checkpoints you made yourself: pickle files can run code when loaded."""
        run_dir = Path(run_dir)
        with open(run_dir / "checkpoint.pkl", "rb") as f:
            sim: IslandSimulation = pickle.load(f)
        sim.backends = backends
        sim.run_dir = run_dir
        if not hasattr(sim, "energy"):  # checkpoints from before mental energy existed
            sim.energy = MentalEnergy({"enabled": False}, list(sim.isl.v))
            sim.isl.energy, sim.habits = sim.energy, {}
        elif any(isinstance(v, dict) for v in sim.energy.left.values()):  # from before energy was one pool
            sim.energy = MentalEnergy(sim.sc.mental_energy, list(sim.isl.v))
            sim.isl.energy = sim.energy
        if not hasattr(sim.isl, "memory"):  # checkpoints from before long-term memory
            sim.isl.memory = memory_settings(getattr(sim.sc, "memory", {}))
            for vil in sim.isl.v.values():
                vil.memories = []
        if not hasattr(sim, "choices"):
            sim.choices = {n: {"plaza": True, "write": True, "next": "plan"} for n in sim.isl.v}
        for attr, default in (("pending_moves", {}), ("moved_today", []), ("mail", [])):
            if not hasattr(sim.isl, attr):
                setattr(sim.isl, attr, default)
        for vil in sim.isl.v.values():
            if not hasattr(vil, "wear"):
                vil.wear = {}
        if not hasattr(sim.bodies, "broke"):
            sim.bodies.broke = []
        if not hasattr(sim, "provider_tokens"):
            sim.provider_tokens = 0
        if not hasattr(sim, "reasoning_tokens"):
            sim.reasoning_tokens = 0
        if not hasattr(sim, "prompt_version"):  # runs from before prompt versions used the original wording
            sim.prompt_version = 1
        sim.isl.prompt_version = sim.prompt_version
        sim.isl.number_board()  # checkpoints from before board posts had numbers
        change = None
        if prompt_version is not None:
            new = PROMPT_VERSION if str(prompt_version) == "latest" else int(prompt_version)
            if new not in PROMPT_CHANGES:
                raise ValueError(f"no prompt version {new}; there are {sorted(PROMPT_CHANGES)}")
            if new != sim.prompt_version:
                change = (sim.prompt_version, new)
                sim.prompt_version = sim.isl.prompt_version = new
        sim.verbose = verbose
        if backend_override:
            sim.override = backend_override
        if concurrency:
            sim.concurrency = concurrency
        sim.failures = 0
        # Rebuild system prompts from the saved specs with the current prompt code.
        sim._build_systems()
        _set_aside_unfinished(run_dir, getattr(sim, "_log_offset", None), sim.isl.day)
        _drop_unfinished_interventions(run_dir, sim.isl.day)
        sim._open_log("a")
        sim.log("resume", from_day=sim.isl.day + 1, days=days, time=time.time(), prompt_version=sim.prompt_version)
        if change:
            sim._intervene(sim.isl.day + 1, f"prompt version {change[0]} -> {change[1]}",
                           [_change_note(v, sim.sc.world) for v in range(change[0] + 1, change[1] + 1) if v in PROMPT_CHANGES]
                           if change[1] > change[0] else [f"back to: {PROMPT_CHANGES[change[1]]}"])
        # Changes patched into the checkpoint by hand, recorded here so they land in this attempt's log
        # (a resume that doesn't finish replays from the same checkpoint and records them again).
        for what, details in getattr(sim, "pending_interventions", []):
            sim._intervene(sim.isl.day + 1, what, details)
        sim.pending_interventions = []
        try:
            sim._run_days(days)
        finally:
            sim.hour = None
            sim.log("run_end", time=time.time(), calls=sim.calls, provider_tokens=sim.provider_tokens,
                    reasoning_tokens=sim.reasoning_tokens)
            sim._raw_log.close()
        return run_dir

    def _build_systems(self) -> None:
        """System prompts: persona, what the villager wrote last night (if reflection is on) and
        the island rules. Plans also get the action menu; conversations and the plaza don't.
        Rebuilt after every nightly reflection."""
        sc = self.sc
        selfdoc = self._self_doc
        v = getattr(self, "prompt_version", 1)
        self.systems = {n: system_prompt(s, sc.world, len(sc.villagers), sc.brief, selfdoc(n), version=v)
                        for n, s in sc.villagers.items()}
        self.talk_systems = {n: system_prompt(s, sc.world, len(sc.villagers), sc.brief, selfdoc(n), menu=False,
                                              version=v)
                             for n, s in sc.villagers.items()}
        self.light_systems = {n: light_system_prompt(s, selfdoc(n)) for n, s in sc.villagers.items()}

    def _self_doc(self, name: str) -> str | None:
        return self.isl.v[name].self_doc if self.sc.reflection.get("enabled") else None

    def _reflect(self, d: int) -> None:
        """Nightly: each villager writes a free-form private page that replaces the last one,
        unless they chose not to write tonight."""
        cfg = self.sc.reflection
        if not cfg.get("enabled"):
            return
        isl = self.isl
        max_words = int(cfg.get("max_words", 150))
        who = [n for n in isl.v if isl.arrived[n] is not None]
        for name in [n for n in who if not self.choices[n]["write"]]:
            self.log("reflection", day=d, agent=name, self_doc=isl.v[name].self_doc, error=None, raw=None,
                     skipped="chose not to write tonight")
        who = [n for n in who if self.choices[n]["write"]]

        def reflect(name):
            user = reflect_prompt(isl, isl.v[name], max_words, isl.memory, list(self.offers.values()))
            if not self.energy.can(name, self._need(name, user)):
                return TIRED
            return self._call(name, user, f"d{d:03d}_reflect", system=self.light_systems[name], free_text=True,
                              max_tokens=max_words * 3 + 500, kind="thinking", what="reflect")

        results = self._parallel(reflect, who)
        for name in who:
            if results[name] is TIRED:
                self.energy.skip(name, "thinking")
                self.log("reflection", day=d, agent=name, self_doc=isl.v[name].self_doc, error=None, raw=None,
                         skipped="no energy left")
                continue
            reply, _, err = results[name]
            text = re.sub(r"<think>.*?</think>", "", reply.text if reply else "", flags=re.S | re.I).strip()
            kept = {}
            if isl.memory:  # the page, then what they chose to hold on to
                text, picks = split_picks(text)
                new, again = consolidate(isl.v[name].memories, picks, d, isl.memory)
                kept = {"remembered": [m["text"] for m in new], "rehearsed": [m["text"] for m in again]}
            words = text.split()
            if len(words) > max_words * 1.3:
                text = " ".join(words[: int(max_words * 1.3)])
            if text:
                isl.v[name].self_doc = text
            self.log("reflection", day=d, agent=name, self_doc=isl.v[name].self_doc, error=err,
                     raw=reply.text if reply else None, **kept)
        self._build_systems()

    def _forget(self, d: int) -> None:
        """Overnight, long-term memories that have faded below the threshold are gone."""
        if not self.isl.memory:
            return
        for name, vil in self.isl.v.items():
            gone = forget(vil.memories, d + 1, self.isl.memory)
            self.log("memory", day=d, agent=name, kept=len(vil.memories), forgotten=[m["text"] for m in gone])

    def _open_log(self, mode: str) -> None:
        self._raw_log = EventLog(self.run_dir / "events.jsonl", mode)
        self.hour = None
        # Every event carries the in-game hour it happened at.
        self.log = lambda type_, **k: self._raw_log(type_, **{"hour": self.hour, **k})

    # --- llm plumbing ------------------------------------------------------
    def _need(self, name: str, user: str) -> float:
        """Points of mental energy it takes to read this prompt: what changes from call to call
        (the situation, and the villager's own nightly page), not the fixed rules and persona."""
        return self.energy.points(self._self_doc(name) or "", user)

    def _call(self, name: str, user: str, tag: str, system: str | None = None, backend_name: str | None = None,
              max_tokens: int | None = None, free_text: bool = False, kind: str | None = None, what: str = "other",
              social: str = ""):
        """One LLM call. `social` is the part of the prompt that is other villagers' mail: reading it
        is booked as talking, whatever kind of call carries it."""
        if backend_name is None:
            backend_name = self.override or self.sc.villagers[name].model
        backend = self.backends[backend_name]
        msgs = [{"role": "system", "content": system or self.systems[name]}, {"role": "user", "content": user}]
        if self.log_prompts:
            d = self.run_dir / "prompts"
            d.mkdir(exist_ok=True)
            prompt_file = d / f"{tag}_{name}.txt"
            prompt_file.write_text(f"=== SYSTEM ===\n{msgs[0]['content']}\n\n=== USER ===\n{user}\n")
        try:
            reply = backend.complete(msgs, self.sc.temperature, max_tokens or self.sc.max_tokens)
        except DailyLimit as e:
            raise RunAborted(
                f"{e}\nThe days finished so far are checkpointed and today's replies are cached. Once the limit "
                f"resets, continue with: llmabm resume {self.run_dir} --days <days left>") from e
        except LLMError as e:
            self.failures += 1
            if self.failures >= self.max_failures:
                access = any(f"HTTP {c}" in str(e) for c in (401, 403, 404))
                hint = ("The provider refused the request: check the API key, and that the model in "
                        "config/models.yaml still exists and the key can use it." if access else "Likely a rate or daily cap.")
                raise RunAborted(
                    f"{self.failures} LLM calls in a row failed (last: {e}). {hint} "
                    "Rerun later with the same seed: cached replies replay the finished part for free.") from e
            return None, None, str(e)
        self.failures = 0
        self.calls += 1
        self.provider_tokens += reply.tokens
        self.reasoning_tokens += reply.reasoning_tokens
        if self.log_prompts:
            with prompt_file.open("a") as f:
                f.write(f"\n=== REASONING ({reply.reasoning_tokens} tokens, hidden from everyone) ===\n"
                        f"{reply.reasoning or '(none returned)'}\n\n=== REPLY ===\n{reply.text}\n")
        if free_text:  # Isabelle, and villagers' nightly page
            obj, err, thought = None, None, reply.text
        else:
            obj, err = parse_action_obj(reply.text)
            thought = " ".join(str((obj or {}).get(k) or "") for k in ("thoughts", "note"))
        if name in self.isl.v:
            read = self._need(name, user)
            self.energy.record(name, kind, what, read, self.energy.points(reply.text), self.energy.points(thought),
                               immediate=threading.current_thread() is threading.main_thread(),
                               fixed=self.energy.points(msgs[0]["content"], user) - read,
                               social=self.energy.points(social), hidden=reply.reasoning_tokens / self.energy.tpp)
        return reply, obj, err

    def _parallel(self, fn, names):
        with ThreadPoolExecutor(self.concurrency) as pool:
            out = dict(zip(names, pool.map(fn, names)))
        self.energy.settle()
        return out

    # --- main loop ---------------------------------------------------------
    def run(self, days: int | None = None) -> Path:
        days = days or self.sc.days
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._write_manifest(days)
        self._open_log("w")
        self.log("run_start", scenario=self.sc.name, seed=self.seed, days=days, time=time.time(),
                 arrivals={n: clock(h) for n, h in self.arrival_hour.items()})
        try:
            self._run_days(days)
        finally:
            self.hour = None
            self.log("run_end", time=time.time(), calls=self.calls, provider_tokens=self.provider_tokens,
                     reasoning_tokens=self.reasoning_tokens)
            self._raw_log.close()
        return self.run_dir

    def _run_days(self, days: int) -> None:
        self._t0 = time.monotonic()
        self._tokens0 = self.provider_tokens  # this session's tokens are counted from here
        self._progress_header()
        for _ in range(days):
            self._day()
            self._checkpoint()

    def _progress(self, what: str, quiet_ok: bool = False) -> None:
        """One line of progress: island day and clock, what just happened, calls, tokens, time taken.
        With quiet_ok, nothing is printed if no call was made since the last line."""
        if not self.verbose or (quiet_ok and self.calls == getattr(self, "_last_calls", None)):
            return
        self._last_calls = self.calls
        secs = int(time.monotonic() - getattr(self, "_t0", time.monotonic()))
        session = self.provider_tokens - getattr(self, "_tokens0", 0)
        print(f"  {self.isl.day:>3}  {clock(self.hour):>5}  {what:<18}  {self.calls:>9,}  {session:>14,}  "
              f"{self.provider_tokens:>10,}  {secs // 60:>4}m{secs % 60:02d}s", flush=True)

    def _progress_header(self) -> None:
        """Column labels for the progress lines. Tokens are as the provider counts them; cached replies cost
        none. "this session" starts at zero for each run or resume; "run total" carries on across resumes."""
        if self.verbose:
            print(f"  {'day':>3}  {'time':>5}  {'step':<18}  {'calls run':>9}  {'tokens session':>14}  "
                  f"{'tokens run':>10}  {'elapsed':>8}", flush=True)

    def _day(self) -> None:
        isl, log = self.isl, self.log
        cal = self.sc.world["calendar"]
        isl.new_day()
        self.energy.new_day()
        self.space.new_day(isl, self.rng)
        self.space.sync(isl)
        self.bodies.new_day()
        for n in isl.moved_today:  # Tom Nook moved their home overnight
            self.bodies.place_at_home(n)
        d = isl.day
        self.hour = cal["day_start"]
        self.first_plan = {n: True for n in isl.v}
        self.used = {n: 0 for n in isl.v}
        self.schedule: dict[int, list[tuple[str, dict]]] = {}
        self.speak = list(isl.v)
        self.noticed = {n: [] for n in isl.v}
        self.chats_today = {n: 0 for n in isl.v}
        self.decisions_today = {n: 0 for n in isl.v}
        self.spots_today = {n: 0 for n in isl.v}
        if getattr(self, "prompt_version", 1) >= 6:  # the plaza is opt-in: nobody is going until a plan says so
            for n in isl.v:
                self.choices[n]["plaza"] = False
        log("day_start", day=d, money_rock=isl.money_rock + 1, cranny=isl.cranny_stock,
            cranny_recipe=isl.cranny_recipe, able=isl.able_stock, pools=isl.pools(),
            ordinance=isl.ordinance, news=isl.yesterday_news, announcement=isl.announcement,
            heard=announcement_text(isl),  # Isabelle's announcement exactly as the first plan of the day shows it
            month=isl.month, season=isl.season, weather=isl.weather, date=isl.date_text(),
            awake={n: list(isl.awake_window(n)) for n in isl.v})
        if self.verbose:
            print(f"day {d} ({isl.date_text()})", flush=True)
        if getattr(self, "prompt_version", 1) >= 7:  # offers wait OFFER_DAYS days for an answer
            for o in self.offers.values():
                if o.status == "pending" and o.day <= d - OFFER_DAYS:
                    o.status = "expired"
                    log("offer_update", **asdict(o))
        if self.v12:  # requests stay open REQUEST_DAYS days
            for f in isl.favors:
                if f["status"] == "open" and f["day"] <= d - REQUEST_DAYS:
                    f["status"], f["done_day"] = "expired", d
                    log("favor_expired", day=d, **{k: v for k, v in f.items() if k != "day"}, asked_day=f["day"])
        if d == 0:
            self._settle_all(d)
        self._deliver_mail(d)
        self._morning_state()
        slots = self._plan_slots()
        for h in range(cal["day_start"], cal["day_start"] + cal.get("day_length", 24)):
            self.hour = h
            if slots.get(h):
                self._plan(d, h, slots[h])
                self._progress("plans made")
            self._run_hour(d, h)
            self._progress("hour over", quiet_ok=True)
        for n, vil in isl.v.items():
            self.yesterday[n] = list(vil.today)
            if isl.memory:  # notes last a few days unless picked at night
                vil.notes = [(nd, t) for nd, t in vil.notes if nd > d - isl.memory["notes_days"]]
            else:
                vil.notes = vil.notes[-self.sc.memory_size:]
        self.hour = cal["day_start"] + cal.get("day_length", 24)
        snap = isl.snapshot()
        snap["objects"] = self.space.objects_snapshot(isl)
        log("snapshot", **snap)
        self._reflect(d)
        self._forget(d)
        self._progress("nightly pages")
        log("energy", day=d, enabled=self.energy.enabled, villagers=self.energy.day_record(),
            provider_tokens=self.provider_tokens, reasoning_tokens=self.reasoning_tokens)
        self._isabelle(d)
        self._progress("Isabelle's report")
        if self.verbose:
            trades = sum(1 for o in self.offers.values() if o.day == d and o.status == "accepted")
            print(f"day {d:>3}: museum {sum(len(m) for m in isl.museum.values())}, "
                  f"bridge {isl.bridge_fund}, trades {trades}, calls so far {self.calls}, "
                  f"provider tokens so far {self.provider_tokens:,} ({self.reasoning_tokens:,} reasoning)", flush=True)

    # --- who gave what to whom ------------------------------------------------
    def _exchange(self, d: int, how: str, giver: str, taker: str, items: dict, got: dict | None = None,
                  **extra) -> None:
        """Remember something changing hands between two villagers: a gift in person, a parcel, or a trade
        (`got` is what the giver received back). `asked`: the items answered a request of the taker's;
        `asked_back`: `got` answered one of the giver's. From prompt version 8 each villager sees their own history
        with someone when they meet, and the day's exchanges at night. Recorded in every run."""
        asked = any(f["asker"] == taker and f["asked"] == giver for f in self.isl.favors_done_today)
        asked_back = any(f["asker"] == giver and f["asked"] == taker for f in self.isl.favors_done_today)
        self.isl.exchanges = getattr(self.isl, "exchanges", [])
        self.isl.exchanges.append({"day": d, "hour": self.hour, "how": how, "from": giver, "to": taker,
                                   "items": dict(items), "got": dict(got or {}), "asked": asked,
                                   **({"asked_back": True} if got and asked_back else {}), **extra})

    def _morning_state(self) -> None:
        """What each villager has as the day begins, to tell them at night what changed."""
        self.isl.morning_state = {n: {"inventory": Counter({k: c for k, c in v.inventory.items() if c > 0}),
                                      "bells": v.bells, "recipes": set(v.recipes),
                                      "home_items": Counter({k: c for k, c in v.home_items.items() if c > 0}),
                                      "home_bells": v.home_bells} for n, v in self.isl.v.items()}

    # --- mail ----------------------------------------------------------------
    def _deliver_mail(self, d: int) -> None:
        """Morning: letters and parcels sent before today arrive. A parcel that doesn't fit in
        the recipient's pockets waits for them, and they are told."""
        isl, waiting = self.isl, []
        for m in isl.mail:
            if m["sent_day"] >= d:
                waiting.append(m)
                continue
            vil = isl.v[m["to"]]
            if m["kind"] == "letter":
                vil.inbox.append(f"(sent day {m['sent_day']}) letter from {m['from']}: {m['text']}")
                continue
            got, left = isl.put_items(vil, m["parcel"])
            if got:
                self._parcel_received(d, m, got, f"(sent day {m['sent_day']}) parcel from {m['from']}: {_count(got)}"
                                      + (f' ("{m["note"]}")' if m.get("note") else ""))
            if left and isl.split_home():  # version 12: what doesn't fit in the pockets is left at home if there's room
                stored, left = isl.put_home(vil, left)
                if stored:
                    self._parcel_received(d, m, stored, f"(sent day {m['sent_day']}) parcel from {m['from']}, left at "
                                          f"your home (no room in your pockets): {_count(stored)}", " (left at home)")
            if left:
                vil.inbox.append(f"a parcel from {m['from']} with {', '.join(f'{n} {k}' for k, n in left['items'].items())} "
                                 "is waiting for you: no room in your pockets"
                                 + (" or at home" if isl.split_home() else ""))
                waiting.append({**m, "parcel": left})
        isl.mail = waiting

    def _parcel_received(self, d: int, m: dict, items: dict, line: str, where: str = "") -> None:
        """A parcel (or the part of it that fit) reached its recipient: tell them, close requests it answers,
        and record the gift."""
        self.isl.v[m["to"]].inbox.append(line)
        self.isl._settle_favors(m["from"], m["to"], items)
        self._exchange(d, "by mail", m["from"], m["to"], items, sent_day=m["sent_day"])
        self.log("gift", day=d, giver=m["from"], receiver=m["to"], items=items, ok=True, how="by mail",
                 result=_count(items) + where, sent_day=m["sent_day"])
        self._log_favors_done(d)

    # --- arrivals ----------------------------------------------------------
    def _settle_all(self, d: int) -> None:
        """Day 0: everyone lands at once, and Tom Nook has a tent ready for each of them on a
        homesite drawn at random. Moving later costs bells (move_house)."""
        isl, h = self.isl, self.hour
        names = sorted(isl.v)
        self.rng.shuffle(names)
        for name in names:
            site, _ = isl.settle(name, self.rng.choice(isl.free_sites()) if isl.free_sites() else None)
            isl.arrived[name] = h
            self.bodies.place_at_home(name)
            area = isl.sites[site]["area"] if site else None
            isl.v[name].today.append(f"{clock(h)} arrived on the island; Tom Nook had set up your tent "
                                     + (f"{area} ({site})" if site else "nowhere: there were no homesites left"))
            self.log("arrival", day=d, agent=name, homesite=site, area=area)

    # --- encounters --------------------------------------------------------
    def _radius(self) -> float:
        cfg = self.sc.world.get("encounters") or {}
        return float(cfg.get("grid_radius", 6))

    def _encounters(self, d: int, h: int, out: list[str], near: dict[str, list[str]]) -> None:
        """(1) Bodies notice who is near whom. (2) Each such villager decides, in a small LLM
        call, whether to approach one of them; approaching includes the opening line.
        (3) Approaches are taken in random order and become one-on-one chats."""
        cfg = self.sc.world.get("encounters") or {}
        if not cfg:
            return
        isl = self.isl
        skip = set(cfg.get("skip_places", ["home"]))
        cap_chat = int(cfg.get("max_chats_per_villager_per_day", 2))
        cap_dec = int(cfg.get("max_decisions_per_villager_per_day", 4))
        out = [n for n in out if self.bodies.b[n].place not in skip]
        near = {n: [m for m in near.get(n, []) if m in out and self.chats_today.get(m, 0) < cap_chat] for n in out}
        deciders = [n for n, ms in near.items() if ms and self.chats_today.get(n, 0) < cap_chat
                    and self.decisions_today.get(n, 0) < cap_dec]
        if not deciders:
            return

        def decide(name):
            place = self.bodies.b[name].place
            user = notice_prompt(isl, isl.v[name], place, h, near[name])
            if not self.energy.can(name, self._need(name, user)):
                return TIRED
            return self._call(name, user, f"d{d:03d}_h{h % 24:02d}_notice", system=self.light_systems[name],
                              max_tokens=500, kind="talking", what="notice")

        results = self._parallel(decide, deciders)
        busy: set[str] = set()
        order = list(deciders)
        self.rng.shuffle(order)
        for name in order:
            if results[name] is TIRED:
                self.energy.skip(name, "talking")
                self.log("encounter_decision", day=d, agent=name, place=self.bodies.b[name].place, nearby=near[name],
                         approach=None, outcome="too tired to talk to anyone", thoughts="", error=None, raw=None)
                continue
            reply, obj, err = results[name]
            self.decisions_today[name] = self.decisions_today.get(name, 0) + 1
            lower = {m.lower(): m for m in near[name]}
            target = lower.get(str((obj or {}).get("approach") or "").strip().lower())
            opener = str((obj or {}).get("say") or "").strip()[:500]
            outcome = "didn't approach anyone"
            if target and opener:
                if name in busy or target in busy:
                    outcome = f"wanted to talk to {target}, but one of them was already talking"
                else:
                    outcome = f"approached {target}"
            self.log("encounter_decision", day=d, agent=name, place=self.bodies.b[name].place, nearby=near[name],
                     approach=target, outcome=outcome, thoughts=_thoughts(obj), error=err,
                     raw=reply.text if reply else None)
            if outcome.startswith("approached"):
                busy |= {name, target}
                self._chat(d, h, self.bodies.b[name].place, name, target, _turn_cap(cfg),
                           opener={"speaker": name, "text": opener, "thoughts": _thoughts(obj), "error": err,
                                   "_obj": obj or {}})

    SPOT_TOOL = {"fish": "fishing rod", "bug": "net"}

    def _spot_decisions(self, d: int, h: int) -> None:
        """Prompt version 14: a villager who has just seen a bug or fish shadow, carries the net or rod for it,
        is awake next hour and has an action for it (or an action planned then to give up), decides in a small
        call whether to go after one of them next hour. At most `sightings.max_decisions_per_day` such calls a
        day each (default 3). Going after it replaces what was planned for that hour, if anything."""
        isl = self.isl
        just = getattr(self.space, "__dict__", {}).get("just_seen") or {}
        cap = int((self.sc.world.get("sightings") or {}).get("max_decisions_per_day", 3))
        budget = self.sc.world["calendar"]["actions_per_day"]
        spots = self.__dict__.setdefault("spots_today", {})
        nxt = h + 1
        if not any(just.values()):
            return
        live = {c["id"] for c in self.space._crit()}
        cands, planned, finds = [], {}, {}
        for n, seen in just.items():
            if not seen or not isl.is_awake(n, h) or not isl.is_awake(n, nxt) or spots.get(n, 0) >= cap:
                continue
            inv = isl.v[n].inventory
            ok = [c for c in seen if c["id"] in live and inv[self.SPOT_TOOL[c["kind"]]] > 0]
            mine = [act for m, act in self.schedule.get(nxt, []) if m == n]
            act_planned = mine[0] if mine and str(mine[0].get("do")).lower() != "plaza" else None
            if not ok or (act_planned is None and self.used[n] >= budget):
                continue
            cands.append(n)
            planned[n], finds[n] = (mine[0] if mine else None), ok
        if not cands:
            return

        def decide(name):
            p = planned[name]
            user = spot_prompt(isl, isl.v[name], self.bodies.b[name].place, h, [c["text"] for c in finds[name]],
                               action_text(p) if p else None, budget - self.used[name], budget)
            if not self.energy.can(name, self._need(name, user)):
                return TIRED
            return self._call(name, user, f"d{d:03d}_h{h % 24:02d}_spot", system=self.light_systems[name],
                              max_tokens=400, kind="thinking", what="spot")

        results = self._parallel(decide, cands)
        for name in cands:
            spots[name] = spots.get(name, 0) + 1
            texts = [c["text"] for c in finds[name]]
            if results[name] is TIRED:
                self.energy.skip(name, "thinking")
                self.log("spot_decision", day=d, hour=h, agent=name, seen=texts, planned=planned[name], go=None,
                         outcome="too tired to decide", thoughts="", error=None, raw=None)
                continue
            reply, obj, err = results[name]
            try:
                go = int(str((obj or {}).get("go") or 0).strip().lstrip("#"))
            except ValueError:
                go = 0
            act, outcome = None, "let it be"
            if 1 <= go <= len(finds[name]):
                c = finds[name][go - 1]
                act = ({"do": "fish", "where": c["habitat"], "size": c["size"]} if c["kind"] == "fish"
                       else {"do": "catch_bug", "where": c["habitat"], "species": c["species"]})
                p = planned[name]
                self.schedule[nxt] = [(m, a) for m, a in self.schedule.get(nxt, []) if m != name] + [(name, act)]
                if p is None or str(p.get("do")).lower() == "plaza":
                    self.used[name] += 1
                outcome = "went after it" + (f", instead of {action_text(p)}" if p else "")
                isl.v[name].today.append(f"{clock(h)} spotted {texts[go - 1]} and went after it at {clock(nxt)}"
                                         + (f" instead of {action_text(p)}" if p else ""))
            self.log("spot_decision", day=d, hour=h, agent=name, seen=texts, planned=planned[name], go=go or None,
                     action=act, outcome=outcome, thoughts=_thoughts(obj), error=err, raw=reply.text if reply else None)

    def _chat(self, d: int, h: int, place: str, a: str, b: str, n_turns: int | None, opener: dict | None = None,
              visit: bool = False) -> None:
        """A one-on-one conversation. `a` speaks first. On a visit, `a` is the guest and `b` the host.
        Each turn can also hand something over ("give") or ask for something ("ask")."""
        isl = self.isl
        turns: list[dict] = []
        if opener:
            obj0 = opener.pop("_obj", None) or {}
            self._chat_acts(d, a, b, obj0, opener, turns)
            turns.append(opener)
        v9 = getattr(self, "prompt_version", 1) >= 9
        if n_turns is None:  # no set length needs a way to leave (prompt version 9); before it, the old 2 lines
            n_turns = None if v9 else 2
        cap = MAX_CHAT_LINES if n_turns is None else max(1, n_turns)
        for i in range(len(turns), cap):
            speaker, other = (a, b) if i % 2 == 0 else (b, a)
            role = None if not visit else ("guest" if speaker == a else "host")
            last = None
            if v9:  # is this their last line: no more turns, or probably no energy for another
                probe = encounter_prompt(isl, isl.v[speaker], other, place, h, turns, opener=(i == 0), visit=role)
                cost = max(self._need(speaker, probe), self.energy.typical("chat"))
                if i + 2 >= cap and n_turns is not None:
                    last = "turns"
                elif self.energy.enabled and self.energy.left.get(speaker, 0) < 2 * cost + 1:
                    last = "energy"
            user = encounter_prompt(isl, isl.v[speaker], other, place, h, turns, opener=(i == 0), visit=role, last=last)
            if not self.energy.can(speaker, self._need(speaker, user)):
                self.energy.skip(speaker, "talking")
                turns.append({"speaker": speaker, "text": "", "thoughts": "", "error": None,
                              "act": "just nodded (too tired to talk)"})
                break
            reply, obj, err = self._call(speaker, user, f"d{d:03d}_h{h % 24:02d}_chat", system=self.talk_systems[speaker],
                                         kind="talking", what="chat")
            say = str((obj or {}).get("say") or "").strip()[:500]
            turn = {"speaker": speaker, "text": say, "thoughts": _thoughts(obj), "error": err}
            self._chat_acts(d, speaker, other, obj or {}, turn, turns)
            turns.append(turn)
            note = str((obj or {}).get("note") or "").strip()
            if note:
                isl.v[speaker].notes.append((d, note))
            if not say and not turn.get("act"):
                break
            if v9 and (obj or {}).get("leave") is True:  # they chose to end it here
                turn["leave"] = True
                turn["act"] = "; ".join(x for x in (turn.get("act"), "then went their way") if x)
                break
        v12 = self.v12
        if v12:
            keep_chat(isl, {"day": d, "hour": h, "place": place, "a": a, "b": b, "visit": visit,
                            "turns": [{k: t[k] for k in ("speaker", "text", "act", "acts", "leave") if k in t}
                                      for t in turns if t["text"] or t.get("act")]})
        self.chats_today[a] = self.chats_today.get(a, 0) + 1
        self.chats_today[b] = self.chats_today.get(b, 0) + 1
        spoken = [t for t in turns if t["text"] or t.get("act")]
        for me, other in ((a, b), (b, a)):
            where = (f"visited {other} at home" if me == a else f"{other} visited you at home") if visit \
                else (f"ran into {other} {at_place(place)}" if v12 else f"ran into {other} at the {place}")
            if spoken:
                lines = "; ".join(f'{"you" if t["speaker"] == me else other} said "{t["text"][:220]}"'
                                  + (f" [{turn_act_text(t, me, other, self.prompt_version >= 13) if v12 else t['act']}]"
                                     if t.get("act") else "") for t in spoken)
                isl.v[me].today.append(f"{clock(h)} {where}: {lines}")
            else:
                isl.v[me].today.append(f"{clock(h)} {where}; " + ("nobody said much" if visit else "you just waved"))
        self.log("encounter", day=d, location=place, villagers=[a, b], turns=turns, visit=visit)

    def _chat_acts(self, d: int, speaker: str, other: str, obj: dict, turn: dict, turns: list | None = None) -> None:
        """In-person gifts, requests and (from prompt version 12) trades made during a conversation. `turns` is
        the conversation so far, for the trade on the table."""
        isl = self.isl
        acts, said = [], []  # `said`: the same, structured, so each side can be told it their way (version 12)
        if self.v12 and obj.get("accept") is True:
            self._accept_trade(d, speaker, other, open_trade(turns or []), acts, said)
        give = obj.get("give")
        if isinstance(give, dict) and _clean_side(give):
            items = isl.resolve_side(speaker, _clean_side(give))
            ok, what = isl.transfer(speaker, other, items)
            self.log("gift", day=d, giver=speaker, receiver=other, items=items, ok=ok, how="in person",
                     result=what, **({"said": _clean_side(give)} if items != _clean_side(give) else {}))
            if ok:
                self._exchange(d, "in person", speaker, other, items)
                acts.append(f"gave {what}")
                said.append({"kind": "gave", "items": dict(items)})
            elif getattr(self, "prompt_version", 1) >= 8:
                problem = isl.give_problem(speaker, other, items)
                acts.append(_failed_give(items, other, problem))
                said.append({"kind": "failed", "items": dict(items), "problem": problem})
            else:
                acts.append(f"tried to give something but {what}")
        ask = obj.get("ask")
        unknown = isl.unknown_items(isl.resolve_side(other, _clean_side(ask))) if isinstance(ask, dict) else []
        if unknown:  # not kept as a request, so nobody sees it listed as a real thing
            acts.append(f"asked for {', '.join(f'{n} {k}' for k, n in _clean_side(ask).items())}, "
                        f"but there's nothing called {' or '.join(unknown)} on this island")
            said.append({"kind": "asked", "items": _clean_side(ask), "unknown": unknown})
        elif isinstance(ask, dict) and _clean_side(ask):
            before = len(isl.favors)
            f = isl.ask_favor(speaker, other, _clean_side(ask))
            if f:
                if len(isl.favors) > before:
                    self.log("favor_asked", day=d, **{k: v for k, v in f.items() if k != "day"}, asked_day=f["day"])
                acts.append("asked for " + ", ".join(f"{n} {k}" for k, n in f["items"].items()))
                said.append({"kind": "asked", "items": dict(f["items"])})
        trade = obj.get("trade")
        if self.v12 and isinstance(trade, dict) and \
                (_clean_side(trade.get("give")) or _clean_side(trade.get("want"))):
            self._propose_trade(d, speaker, other, trade, acts, said, turns or [])
        self._log_favors_done(d)
        if acts:
            turn["act"] = "; ".join(acts)
        if said:
            turn["acts"] = said

    def _propose_trade(self, d: int, speaker: str, other: str, trade: dict, acts: list, said: list,
                       turns: list) -> None:
        """Prompt version 12: put a trade on the table. It waits for the other's next turn. From prompt version
        13 a conversation holds at most TRADE_TRIES offers; one past that puts nothing on the table, and so does
        one the proposer's own pockets can't hold up (not enough of what they give, or no room for what they get)."""
        isl = self.isl
        give = isl.resolve_side(speaker, _clean_side(trade.get("give")))
        want = isl.resolve_side(other, _clean_side(trade.get("want")))
        if self.prompt_version >= 13 and trade_tries(turns) >= TRADE_TRIES:
            acts.append("tried to offer another trade, but none were left")
            said.append({"kind": "proposed", "give": give, "want": want, "over": True})
            self.log("trade_over", day=d, proposer=speaker, to=other, give=give, want=want)
            return
        unknown = isl.unknown_items({**give, **want})
        if unknown:
            acts.append(f"proposed a trade, but there's nothing called {' or '.join(unknown)} on this island")
            said.append({"kind": "proposed", "give": give, "want": want, "unknown": unknown})
        elif not (give and want):
            acts.append("proposed a trade with nothing on one side")
            said.append({"kind": "proposed", "give": give, "want": want, "one_sided": True})
        elif self.prompt_version >= 13 and (problem := isl.lacking(speaker, give)
                                            or isl.room_problem(speaker, give, want)):
            acts.append(f"tried to offer a trade: {_count(give)} for {_count(want)}, but {problem_text(problem)}")
            said.append({"kind": "proposed", "give": give, "want": want, "problem": problem})
            self.log("trade_cant_offer", day=d, proposer=speaker, to=other, give=give, want=want, problem=problem)
        else:
            acts.append(f"offered a trade: {_count(give)} for {_count(want)}")
            said.append({"kind": "proposed", "give": give, "want": want})
            self.log("trade_proposed", day=d, proposer=speaker, to=other, give=give, want=want)

    def _accept_trade(self, d: int, speaker: str, other: str, table: dict | None, acts: list, said: list) -> None:
        """Prompt version 12: the speaker accepts the trade `other` put on the table: both sides change hands at
        once, or nothing does."""
        isl = self.isl
        if not table or table["proposer"] != other:
            acts.append("accepted, but there was no trade on the table")
            said.append({"kind": "no_trade"})
            return
        give, want = table["give"], table["want"]
        ok, problem = isl.trade(other, speaker, give, want)
        if ok:
            self._exchange(d, "trade", other, speaker, give, want, place="in person")
            self.log("trade", day=d, offer=None, how="in person", seller=other, buyer=speaker, give=give, want=want,
                     give_cranny_value=isl.cranny_value(give), want_cranny_value=isl.cranny_value(want))
            acts.append(f"accepted the trade: {_count(give)} for {_count(want)}")
            said.append({"kind": "traded", "proposer": other, "give": give, "want": want})
        else:
            self.log("trade_failed", day=d, proposer=other, accepter=speaker, give=give, want=want, problem=problem)
            acts.append(f"accepted the trade, but it fell through: {problem_text(problem)}")
            said.append({"kind": "trade_failed", "proposer": other, "give": give, "want": want, "problem": problem})

    def _visits(self, d: int, h: int, doing: dict, ok_by: dict, busy: set) -> None:
        """Guests who walked to someone's home this hour: if the host is in and awake, they talk,
        and the guest sees inside. Otherwise nobody answers."""
        isl = self.isl
        guests = [n for n, act in doing.items() if act.get("do") == "visit" and ok_by.get(n)]
        self.rng.shuffle(guests)
        for g in guests:
            host = doing[g].get("who")
            home_now = (isl.is_awake(host, h) and self.bodies.b[host].place == "home" and host not in busy
                        and g not in busy)
            if home_now:
                busy |= {g, host}
                isl.v[g].today.append(f"{clock(h)} inside {host}'s home: "
                                      f"{', '.join(isl.v[host].interior) or 'not much furniture yet'}")
                self.log("visit", day=d, guest=g, host=host, answered=True, interior=list(isl.v[host].interior))
                self._chat(d, h, f"{host}'s home", g, host, _turn_cap(self.sc.world["encounters"]), visit=True)
            else:
                isl.v[g].today.append(f"{clock(h)} knocked at {host}'s home, but nobody answered")
                self.noticed[host].append(f"{clock(h)} {g} came by your home, but you weren't in")
                self.log("visit", day=d, guest=g, host=host, answered=False, interior=[])

    # --- Isabelle ----------------------------------------------------------
    def _isabelle(self, d: int) -> None:
        cfg = self.sc.isabelle
        if not (cfg.get("summary") or cfg.get("announce")):
            return
        from .analysis import day_digest, public_digest

        backend_name = self.override or cfg.get("backend") or "default"
        events = [json.loads(line) for line in (self.run_dir / "events.jsonl").read_text().splitlines()
                  if line.strip() and json.loads(line).get("day") == d]
        if cfg.get("summary"):
            digest = day_digest(events, self.isl)
            reply, _, err = self._call("isabelle", isabelle_summary_prompt(d, digest), f"d{d:03d}_isabelle",
                                       system=ISABELLE_SYSTEM, backend_name=backend_name, max_tokens=1200, free_text=True)
            text = (reply.text.strip() if reply else "") or f"(no summary: {err})"
            days_dir = self.run_dir / "days"
            days_dir.mkdir(exist_ok=True)
            (days_dir / f"day_{d:03d}_isabelle.md").write_text(f"# Isabelle's report, day {d}\n\n{text}\n")
            self.log("isabelle_summary", day=d, text=text, error=err, digest_chars=len(digest))
        if cfg.get("announce"):
            pub = public_digest(self.isl, events)
            reply, _, err = self._call("isabelle", isabelle_announce_prompt(d + 1, pub, getattr(self, "prompt_version", 1)),
                                       f"d{d:03d}_announce",
                                       system=ISABELLE_SYSTEM, backend_name=backend_name, max_tokens=600, free_text=True)
            self.isl.announcement = reply.text.strip() if reply else None
            self.log("isabelle_announcement", day=d, for_day=d + 1, text=self.isl.announcement, error=err)

    def _plan_slots(self) -> dict[int, list[tuple]]:
        """When each villager plans today: {hour: [(name, phase, hours covered, window, plans today)]}.

        Each villager plans `plans_per_day` times, spread over their own waking hours: when they
        get up, then at even intervals, each plan covering an equal share of the hours they are
        awake. So early risers and late sleepers get the same number of plans of the same length.
        Runs started before personal schedules have fixed windows (`plans: [[a, b], ...]`), the
        same for everyone; they keep them, so a resumed run plans as it started."""
        isl, cal = self.isl, self.sc.world["calendar"]
        start = cal["day_start"]
        slots: dict[int, list[tuple]] = {}
        for n in isl.v:
            awake = [h for h in range(start, start + cal.get("day_length", 24)) if isl.is_awake(n, h)]
            if "plans" in cal:
                for i, (a, b) in enumerate(cal["plans"]):
                    hours = [h for h in awake if a <= h < b]
                    if hours:
                        slots.setdefault(a, []).append((n, i, hours, (a, b), len(cal["plans"])))
                continue
            k = min(int(cal.get("plans_per_day", 2)), len(awake))
            for i in range(k):
                hours = awake[len(awake) * i // k: len(awake) * (i + 1) // k]
                slots.setdefault(hours[0], []).append((n, i, hours, (hours[0], hours[-1] + 1), k))
        return slots

    def _plan(self, d: int, at: int, slots: list[tuple]) -> None:
        """One LLM call per villager whose planning time is `at`, to schedule actions in the hours
        that plan covers. With personal schedules a plan may use at most an even share of the
        actions left (half of them, with two plans a day); whatever it doesn't use carries over.
        Before prompt version 7, those who chose habit for this planning time, or have no energy left,
        repeat what they did yesterday at this time. From version 7 there is no habit: a villager with
        no energy left keeps what they already planned and plans nothing new. Plans also set free hours
        aside for the plaza, and the first plan of the day splits the day's energy."""
        isl, log = self.isl, self.log
        budget = self.sc.world["calendar"]["actions_per_day"]
        fixed = "plans" in self.sc.world["calendar"]
        v7 = getattr(self, "prompt_version", 1) >= 7
        slot = {n: (phase, hours, window, k) for n, phase, hours, window, k in slots}
        # From version 7 everyone plans, even with no actions left: they can still go to the plaza.
        who = [n for n in slot if v7 or self.used[n] < budget]

        for n in who:
            isl.where_notes[n] = self.bodies.where_note(n)
        isl.at_home = {n: self.bodies.at_home(n) for n in isl.v}
        if getattr(isl, "prompt_version", 1) >= 3:
            isl.map_text = self.space.map_text(isl)

        # An even share of the actions left, rounded up; fixed windows had no shares.
        shares = {n: None if fixed else -(-(budget - self.used[n]) // (slot[n][3] - slot[n][0])) for n in who}

        def plan(name):
            vil = isl.v[name]
            phase, awake, _, k = slot[name]
            left = budget - self.used[name]
            if not v7 and self.choices[name]["next"] == "habit":
                return HABIT
            user = plan_prompt(isl, vil, awake[0], awake[-1] + 1, min(shares[name] or left, len(awake)), self.yesterday[name],
                               announcement=self.first_plan[name], noticed=self.noticed[name][-8:],
                               choices=self.choices[name])
            if not self.energy.can(name, self._need(name, user)):
                return TIRED
            self.first_plan[name] = False
            mail = inbox_text(vil) if vil.inbox else ""
            vil.inbox = vil.inbox[INBOX_SHOWN:]  # what was shown has been read
            return self._call(name, user, f"d{d:03d}_p{phase}", kind="planning", what="plan", social=mail)

        results = self._parallel(plan, who)
        for name in who:
            phase, awake, (a, b), _ = slot[name]
            if v7 and results[name] is TIRED:  # hold: what is already planned stands, nothing new
                self.energy.skip(name, "planning")
                isl.v[name].today.append(f"{clock(a)} too tired to plan, so you planned nothing new")
                log("plan", day=d, phase=phase, agent=name, window=[a, b], thoughts="", held=True,
                    held_reason="no energy left", scheduled=[], dropped=[], note="", error=None, raw=None,
                    cached=False)
                continue
            if results[name] in (TIRED, HABIT):  # do what they did yesterday at this time of day
                chose = results[name] is HABIT
                if chose:
                    self.choices[name]["next"] = "plan"  # habit covers one planning time; they plan again after it
                else:
                    self.energy.skip(name, "planning")
                acts = [dict(x) for x in self.habits.get((name, phase), [])]
                scheduled, dropped = self._assign_hours(name, acts, awake[0], awake[-1] + 1)
                for h, act in scheduled:
                    self.schedule.setdefault(h, []).append((name, act))
                why = "you went with habit" if chose else "too tired to plan"
                isl.v[name].today.append(f"{clock(a)} {why}, so you "
                                         + ("did what you did yesterday" if scheduled else "took it easy"))
                log("plan", day=d, phase=phase, agent=name, window=[a, b], thoughts="", habit=True,
                    habit_reason="chosen" if chose else "no energy left",
                    scheduled=[{"at": h, **act} for h, act in scheduled], dropped=dropped, note="", error=None,
                    raw=None, cached=False)
                continue
            reply, obj, err = results[name]
            acts = [x for x in (obj or {}).get("actions", []) if isinstance(x, dict)] if obj else []
            scheduled, dropped = self._assign_hours(name, acts, awake[0], awake[-1] + 1, cap=shares[name])
            for h, act in scheduled:
                self.schedule.setdefault(h, []).append((name, act))
            self.habits[(name, phase)] = [{"hour": h % 24, **act} for h, act in scheduled]
            chosen = self._apply_choices(name, obj or {})
            stashed = self._stash(d, name, a, (obj or {}).get("home"))
            split = self.energy.set_plan(name, (obj or {}).get("energy")) if v7 and self.energy.enabled else None
            note = str((obj or {}).get("note") or "").strip()
            if note:
                isl.v[name].notes.append((d, note))
            log("plan", day=d, phase=phase, agent=name, window=[a, b], thoughts=_thoughts(obj),
                scheduled=[{"at": h, **act} for h, act in scheduled], dropped=dropped, note=note, error=err,
                choices=chosen, **({"energy_plan": split} if split else {}), **({"home": stashed} if stashed else {}),
                raw=reply.text if reply else None, cached=reply.cached if reply else False)

    def _stash(self, d: int, name: str, at: int, home) -> list[str]:
        """Prompt version 12: a plan made at home can put things away there or take them out. It takes no time."""
        isl = self.isl
        if not (isl.split_home() and isinstance(home, dict) and isl.at_home.get(name)):
            return []
        store, take = home.get("store"), home.get("take")
        done = isl.stash(isl.v[name], _clean_side(store) if isinstance(store, dict) else {},
                         _clean_side(take) if isinstance(take, dict) else {})
        if done:
            isl.v[name].today.append(f"{clock(at)} at home: " + "; ".join(done))
        return done

    def _apply_choices(self, name: str, obj: dict) -> dict:
        """Read the choices from a plan reply. Anything missing or unreadable keeps its value. Without mental
        energy, only the plaza is a choice, and only in prompt version 6. From version 7 the plaza is in the
        plan itself and there is no habit, so writing tonight is the only choice left."""
        v = getattr(self, "prompt_version", 1)
        keys = (("plaza",) if v == 6 or (v < 7 and self.energy.enabled) else ()) + (("write",) if self.energy.enabled else ())
        if not keys:
            return {}
        ch, changed = self.choices[name], {}
        for key in keys:
            val = obj.get(key)
            if isinstance(val, str) and val.strip().lower() in ("true", "false", "yes", "no"):
                val = val.strip().lower() in ("true", "yes")
            if isinstance(val, bool):
                ch[key] = changed[key] = val
        nxt = str(obj.get("next") or "").strip().lower()
        if self.energy.enabled and v < 7 and nxt in ("plan", "habit"):
            ch["next"] = changed["next"] = nxt
        return changed

    def _assign_hours(self, name: str, acts: list[dict], a: int, b: int, cap: int | None = None):
        """Place each action at its requested hour, or the next free hour in the window.
        One action per hour; stops when the day's action budget, or this plan's share of it
        (`cap`), runs out. From prompt version 7 an hour at the plaza takes an hour but isn't an action:
        it doesn't count against either."""
        budget = self.sc.world["calendar"]["actions_per_day"]
        v7 = getattr(self, "prompt_version", 1) >= 7
        taken = {h for h, lst in self.schedule.items() for n, _ in lst if n == name}
        out, dropped, cursor, n_acts = [], [], a, 0
        for act in acts:
            # Keys the body fills in (targets, what was seen) can't come from the mind.
            act = {k: v for k, v in act.items() if not str(k).startswith("_") and k not in ("none_seen", "none_out", "habitat")}
            plaza = v7 and str(act.get("do") or "").strip().lower() == "plaza"
            if plaza:
                act = {"hour": act.get("hour"), "do": "plaza"} if "hour" in act else {"do": "plaza"}
            elif self.used[name] >= budget:
                dropped.append({**act, "why": "no actions left today"})
                continue
            elif cap is not None and n_acts >= cap:
                dropped.append({**act, "why": f"this plan could use only {cap} of your actions; the rest are for later"})
                continue
            want = act.pop("hour", None)
            start = self.sc.world["calendar"]["day_start"]
            try:
                h = to_game_hour(int(str(want).split(":")[0]), start) if want is not None else cursor
            except ValueError:
                h = cursor
            if not self.isl.is_awake(name, h):
                dropped.append({**act, "hour": want, "why": f"asleep at {clock(h)}"})
                continue
            h = max(h, a)
            while h < b and h in taken:
                h += 1
            if h >= b:
                dropped.append({**act, "why": f"no free hour left before {clock(b)}"})
                continue
            taken.add(h)
            if not plaza:
                self.used[name] += 1
                n_acts += 1
            cursor = h + 1
            out.append((h, act))
        return sorted(out, key=lambda x: x[0]), dropped

    def _run_hour(self, d: int, h: int) -> None:
        isl, log = self.isl, self.log
        cal = self.sc.world["calendar"]
        social = cal.get("social_hours", [])
        v7 = getattr(self, "prompt_version", 1) >= 7
        spoke = set()
        if h in social and not v7:  # from version 7 the plaza is open all day instead
            spoke = self._social_round(d, social.index(h))
        self.space.hour_tick(isl, h, self.rng)  # bugs and fish shadows come and go
        doing: dict[str, dict] = {}
        ok_by: dict[str, bool] = {}
        todo = list(self.schedule.get(h, []))
        to_plaza = [n for n, act in todo if v7 and act.get("do") == "plaza"]
        # Same hour: whoever has the shorter walk from where they are gets there first; ties are luck.
        batch = self.bodies.order([(n, act) for n, act in todo if n not in to_plaza], self.rng)
        for name, act in batch:
            vil = isl.v[name]
            before_v = (vil.bells, vil.loan, Counter(vil.inventory), len(vil.recipes), vil.house)
            before_p = isl.pools()
            ok, res = self.bodies.perform(name, act, h, self.rng)
            vil.today.append(f"{clock(h)} " + (res if ok else f"tried to {act.get('do', '?')}: {res}"))
            doing[name] = act
            ok_by[name] = ok
            log("action", day=d, agent=name, action=act, location=self.bodies.b[name].place, ok=ok, result=res,
                delta=self._delta(vil, before_v), pools_delta=_pools_delta(before_p, isl.pools()),
                at=list(self.bodies.b[name].pos) if self.bodies.b[name].pos else None)
        # A free hour set aside for the plaza (version 7): walk there and take a turn.
        plaza_line: dict[str, int] = {}
        if to_plaza:
            spoke, plaza_line = self._plaza_hour(d, h, to_plaza)
        # Free time: wander (random walk) or stay home; asleep villagers are home.
        wandering = set()
        for n in isl.v:
            if n in doing or n in plaza_line or isl.arrived[n] is None:
                continue
            stay_in = self.rng.random() < float(self.sc.world.get("grid", {}).get("wander_home_chance", 0.5))
            if isl.is_awake(n, h) and self.sc.free_time == "wander" and n not in spoke and not stay_in:
                if self.bodies.wander(n, int(self.sc.world.get("grid", {}).get("wander_steps", 12)), self.rng):
                    wandering.add(n)
                    continue
            self.bodies.go_home(n)
        # What each awake villager sees around them (bugs, fish shadows, shells, dig spots).
        for n in isl.v:
            if isl.arrived[n] is not None and isl.is_awake(n, h):
                seen = self.space.look(isl, n, self.bodies.b[n].pos, h)
                if seen:
                    self.log("sighting", day=d, agent=n, seen=seen)
        # Who saw whom. Bodies detect who is near whom; neighbors notice each other heading out.
        out = [n for n in doing if self.bodies.b[n].place != "home"] + sorted(plaza_line) + sorted(wandering)
        near = self.bodies.nearby(out, self._radius())
        for n in out:
            if n in doing and near[n]:
                isl.v[n].today[-1] += f" (also nearby: {', '.join(near[n])})"
            elif n in plaza_line and near[n]:  # seen after their turn there, like after an action
                isl.v[n].today[plaza_line[n]] += f" (also nearby: {', '.join(near[n])})"
        for n in list(doing) + sorted(plaza_line):
            place = self.bodies.b[n].place
            if place != "home":
                for m in isl.neighbors(n):
                    if isl.is_awake(m, h) and m not in doing:
                        self.noticed[m].append(f"{clock(h)} your neighbor {n} headed to the {place}")
        pairs = sorted({tuple(sorted((a, b))) for a, ms in near.items() for b in ms})
        if pairs:
            log("seen", day=d, pairs=[list(p) for p in pairs],
                places={n: self.bodies.b[n].place for p in pairs for n in p})
        for n, tool, during in self.bodies.broke:
            log("tool_broke", day=d, agent=n, tool=tool, during=during)
        self.bodies.broke = []
        busy: set[str] = set()
        self._visits(d, h, doing, ok_by, busy)
        self._encounters(d, h, [n for n in out if n not in busy], near)
        if getattr(self, "prompt_version", 1) >= 14:
            self._spot_decisions(d, h)
        villagers = {}
        for n, vil in isl.v.items():
            if isl.arrived[n] is None:
                activity, where = "not arrived", "-"
            elif d == 0 and isl.arrived[n] == h:
                activity, where = "arriving", isl.sites[isl.home[n]]["area"] if isl.home.get(n) else "-"
            elif n in doing:
                activity, where = doing[n].get("do", "?"), self.bodies.b[n].place
            elif n in spoke:
                activity, where = "socializing", "plaza"
            elif not isl.is_awake(n, h):
                activity, where = "asleep", "home"
            elif n in wandering:
                activity, where = "wandering", self.bodies.b[n].place
            else:
                activity, where = "free time", "home"
            villagers[n] = {"activity": activity, "location": where, "bells": vil.bells, "loan": vil.loan,
                            "items": vil.items(), "house": vil.house, **self.bodies.state(n)}
        log("hour_state", day=d, pools=isl.pools(), villagers=villagers)

    @staticmethod
    def _delta(vil, before) -> dict:
        bells, loan, inv, nrec, house = before
        items = {k: vil.inventory[k] - inv[k] for k in set(inv) | set(vil.inventory) if vil.inventory[k] != inv[k]}
        out = {}
        if vil.bells != bells:
            out["bells"] = vil.bells - bells
        if vil.loan != loan:
            out["loan"] = vil.loan - loan
        if items:
            out["items"] = items
        if len(vil.recipes) != nrec:
            out["recipes"] = len(vil.recipes) - nrec
        if vil.house != house:
            out["house"] = vil.house - house
        return out

    def _plaza_hour(self, d: int, h: int, names: list[str]) -> tuple[set, dict]:
        """From prompt version 7: villagers who set this free hour aside walk to the plaza and take one turn
        there (board, offers to anyone, letters, parcels). They aren't told who else is there; being there
        together, they can notice each other afterwards like anywhere else. Returns who got there, and the
        line in each one's day that says so."""
        isl, log = self.isl, self.log
        line: dict[str, int] = {}
        for n in sorted(names):
            ok, res = self.bodies.go_to(n, "plaza", "plaza")
            if not ok:
                isl.v[n].today.append(f"{clock(h)} tried to go to the plaza: {res}")
                log("evening_turn", day=d, round=0, place="plaza", agent=n, error=None, thoughts="", raw=None,
                    cached=False, skipped=f"didn't get there: {res}")
                continue
            isl.v[n].today.append(f"{clock(h)} went to the plaza{res}")
            line[n] = len(isl.v[n].today) - 1
        there = sorted(line)

        def turn(name):
            vil = isl.v[name]
            waiting = [o for o in self.offers.values() if o.recipient == name and o.status == "pending"]
            user = plaza_prompt(isl, vil, h, waiting, isl.board if self.v12 else isl.board[-8:])
            if not self.energy.can(name, self._need(name, user)):
                return TIRED
            mail = inbox_text(vil) if vil.inbox else ""
            vil.inbox = vil.inbox[INBOX_SHOWN:]
            return self._call(name, user, f"d{d:03d}_h{h % 24:02d}_plaza", system=self.talk_systems[name],
                              kind="talking", what="evening", social=mail)

        results = self._parallel(turn, there)
        order = list(there)
        self.rng.shuffle(order)
        for name in order:
            if results[name] is TIRED:
                self.energy.skip(name, "talking")
                isl.v[name].today[line[name]] += ", but were too tired to do anything there"
                log("evening_turn", day=d, round=0, place="plaza", agent=name, error=None, thoughts="", raw=None,
                    cached=False, skipped="too tired to do anything at the plaza")
                continue
            reply, obj, err = results[name]
            log("evening_turn", day=d, round=0, place="plaza", agent=name, error=err, thoughts=_thoughts(obj),
                raw=reply.text if reply else None, cached=reply.cached if reply else False)
            if obj:
                self._apply_evening(d, 0, name, obj)
        return set(there), line

    def _social_round(self, d: int, r: int) -> set:
        """Evening at the plaza. Round 0 includes everyone; later rounds only villagers
        with new offers waiting. Returns who took part."""
        isl, log = self.isl, self.log
        if r == 0:
            for o in self.offers.values():
                if o.status == "pending" and o.day < d - 1:
                    o.status = "expired"
                    log("offer_update", **asdict(o))
            self.speak = list(isl.v)
            for n in isl.v:
                if isl.is_awake(n, self.hour) and not self.choices[n]["plaza"]:
                    log("evening_turn", day=d, round=r, agent=n, error=None, thoughts="", raw=None, cached=False,
                        skipped="chose not to go to the plaza")
        speak = [n for n in self.speak if isl.is_awake(n, self.hour) and self.choices[n]["plaza"]]
        if not speak:
            return set()
        before = set(self.offers)

        def turn(name):
            vil = isl.v[name]
            waiting = [o for o in self.offers.values() if o.recipient == name and o.status == "pending"]
            user = evening_prompt(isl, vil, r, self.hour, waiting, isl.board[-8:])
            if not self.energy.can(name, self._need(name, user)):
                return TIRED
            vil.inbox = vil.inbox[INBOX_SHOWN:]
            return self._call(name, user, f"d{d:03d}_h{self.hour % 24:02d}", system=self.talk_systems[name],
                              kind="talking", what="evening")

        results = self._parallel(turn, speak)
        order = list(speak)
        self.rng.shuffle(order)
        for name in order:
            if results[name] is TIRED:
                self.energy.skip(name, "talking")
                log("evening_turn", day=d, round=r, agent=name, error=None, thoughts="", raw=None, cached=False,
                    skipped="too tired to go to the plaza")
                continue
            reply, obj, err = results[name]
            log("evening_turn", day=d, round=r, agent=name, error=err, thoughts=_thoughts(obj),
                raw=reply.text if reply else None, cached=reply.cached if reply else False)
            if obj:
                self._apply_evening(d, r, name, obj)
        new = [self.offers[i] for i in set(self.offers) - before]
        self.speak = sorted({o.recipient for o in new if o.status == "pending"})
        return {n for n in speak if results[n] is not TIRED}

    def _apply_evening(self, d: int, r: int, name: str, obj: dict) -> None:
        isl, log = self.isl, self.log
        vil = isl.v[name]
        lower = {n.lower(): n for n in isl.v}

        def who(x):
            return lower.get(str(x or "").strip().lower())

        v12 = self.v12  # no plaza trade offers: trades happen in person
        for resp in ([] if v12 else obj.get("responses") or []):
            if not isinstance(resp, dict):
                continue
            o = self.offers.get(_ref_id(resp.get("offer")))
            if not o or o.recipient != name or o.status != "pending":
                log("invalid", day=d, agent=name, what="response", detail=resp)
                continue
            dec = str(resp.get("decision", "")).lower()
            note = str(resp.get("note") or "")[:300]
            if dec == "accept":
                self._execute(o, d)
            elif dec == "counter":
                o.status = "countered"
                log("offer_update", **asdict(o))
                self._new_offer(d, name, o.sender, _clean_side(resp.get("give")), _clean_side(resp.get("want")), note, parent=o.id)
            else:
                o.status = "rejected"
                log("offer_update", **asdict(o))
                isl.v[o.sender].inbox.append(f"(day {d}) {name} turned down your offer [{o.id}]" + (f': "{note}"' if note else ""))

        for m in obj.get("letters") or obj.get("messages") or []:  # letters arrive tomorrow morning
            if not isinstance(m, dict):
                continue
            to, content = who(m.get("to")), str(m.get("content") or "").strip()[:LETTER_CHARS]
            if not to or to == name or not content:
                log("invalid", day=d, agent=name, what="message", detail=m)
                continue
            isl.mail.append({"kind": "letter", "from": name, "to": to, "sent_day": d, "text": content})
            log("message", day=d, round=r, sender=name, recipient=to, content=content, arrives_day=d + 1)

        for m in obj.get("parcels") or []:  # items leave now and arrive tomorrow morning
            if not isinstance(m, dict):
                continue
            to = who(m.get("to"))
            items = isl.resolve_side(name, _clean_side(m.get("items")))
            parcel = isl.take_items(vil, items) if to and to != name and items else None
            if parcel is None:
                log("invalid", day=d, agent=name, what="parcel", detail=m)
                vil.today.append(f"{clock(self.hour)} couldn't send a parcel: "
                                 + ("you don't have all of that" if to and items else "no one to send it to"))
                continue
            note = str(m.get("note") or "")[:300]
            isl.mail.append({"kind": "parcel", "from": name, "to": to, "sent_day": d, "parcel": parcel, "note": note})
            vil.today.append(f"{clock(self.hour)} sent {to} a parcel: {', '.join(f'{n} {k}' for k, n in items.items())}")
            log("parcel", day=d, round=r, sender=name, recipient=to, items=items, note=note, arrives_day=d + 1)

        down = obj.get("take_down") if v12 else None
        for bid in ([down] if isinstance(down, (str, int)) else down if isinstance(down, list) else []):
            bid = _ref_id(bid, "b")
            post = next((p for p in isl.board if p[3] == bid), None)
            if post and post[1] == name:
                isl.board.remove(post)
                vil.today.append(f"{clock(self.hour)} took down your board post [{bid}]")
                log("board_removed", day=d, author=name, id=bid, content=post[2], posted_day=post[0])
            else:
                vil.today.append(f"{clock(self.hour)} couldn't take down board post [{bid}]: "
                                 + ("it isn't yours" if post else "there's no such post on the board"))
                log("invalid", day=d, agent=name, what="take_down", detail=bid)

        post = str(obj.get("board") or "").strip()[:BOARD_CHARS]
        if post:
            bid = isl.post(d, name, post)
            if v12:
                vil.today.append(f"{clock(self.hour)} posted on the board [{bid}]: \"{post[:80]}\"")
            log("board", day=d, round=r, author=name, content=post, id=bid)

        for o in ([] if v12 else obj.get("offers") or []):
            if not isinstance(o, dict):
                continue
            to = who(o.get("to"))
            give, want = _clean_side(o.get("give")), _clean_side(o.get("want"))
            if not to or to == name or not (give or want):
                log("invalid", day=d, agent=name, what="offer", detail=o)
                continue
            self._new_offer(d, name, to, give, want, str(o.get("note") or "")[:300])

        note = str(obj.get("note") or "").strip()
        if note:
            vil.notes.append((d, note))

    def _new_offer(self, d, sender, recipient, give, want, note, parent=None):
        give, want = self.isl.resolve_side(sender, give), self.isl.resolve_side(recipient, want)
        unknown = self.isl.unknown_items({**give, **want})
        if unknown:  # an offer of or for something that doesn't exist isn't made; the sender is told
            self.log("invalid", day=d, agent=sender, what="offer", detail={"to": recipient, "give": give, "want": want,
                                                                         "unknown": unknown})
            self.isl.v[sender].today.append(f"{clock(self.hour)} couldn't offer {recipient} that trade: there's "
                                            f"nothing called {' or '.join(unknown)} on this island")
            return
        self._oid += 1
        o = Offer(f"o{self._oid}", d, sender, recipient, give, want, note, parent=parent)
        self.offers[o.id] = o
        self.log("offer", **asdict(o))

    def _execute(self, o: Offer, d: int) -> None:
        isl = self.isl
        a, b = isl.v[o.sender], isl.v[o.recipient]

        def has(vil, side):
            return all((vil.bells if k == "bells" else vil.inventory[k]) >= n for k, n in side.items())

        def items(side):
            return sum(n for k, n in side.items() if k != "bells")

        problem = None
        if not has(a, o.give):
            problem = f"{a.name} no longer has what they offered"
        elif not has(b, o.want):
            problem = f"{b.name} doesn't have what was asked for"
        elif isl.space(b) + items(o.want) < items(o.give):
            problem = f"{b.name} has no room"
        elif isl.space(a) + items(o.give) < items(o.want):
            problem = f"{a.name} has no room"
        if problem:
            o.status = "failed"
            self.log("offer_update", **asdict(o), reason=problem)
            for v in (a, b):
                v.inbox.append(f"(day {d}) trade [{o.id}] fell through: {problem}")
            return
        pa = isl.take_items(a, o.give) if o.give else None
        pb = isl.take_items(b, o.want) if o.want else None
        if pa:
            isl.put_items(b, pa)
        if pb:
            isl.put_items(a, pb)
        o.status = "accepted"
        if not o.want:  # a gift by mail can fulfil an open request
            isl._settle_favors(o.sender, o.recipient, o.give)
        self._exchange(d, "trade" if o.give and o.want else "offer", o.sender, o.recipient, o.give, o.want, offer=o.id)
        if not o.want:
            self._log_favors_done(d)
        self.log("offer_update", **asdict(o))
        self.log("trade", day=d, offer=o.id, seller=o.sender, buyer=o.recipient, give=o.give, want=o.want,
                 give_cranny_value=isl.cranny_value(o.give), want_cranny_value=isl.cranny_value(o.want))
        a.inbox.append(f"(day {d}) {b.name} accepted your offer [{o.id}]")

    def _write_manifest(self, days: int) -> None:
        m = {
            "type": "island",
            "scenario": self.sc.name,
            "description": self.sc.description,
            "seed": self.seed,
            "days": days,
            "world_facts": "perturbed",
            "space": "grid",
            "free_time": self.sc.free_time,
            "backend_override": self.override,
            "prompt_version": self.prompt_version,
            # exactly which model and request settings each backend name meant for this run
            "backends": {k: getattr(b, "model_id", k) for k, b in self.backends.items()},
            "villagers": {n: {"persona": s.persona, "traits": s.traits, "model": self.override or s.model,
                              "source": s.source} for n, s in self.sc.villagers.items()},
            "world": self.sc.world,
            "scenario_config": self.sc.raw,
            "arrivals": {n: clock(h) for n, h in self.arrival_hour.items()},
            "system_prompts": self.systems,
        }
        (self.run_dir / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False))


def _change_note(version: int, world: dict) -> str:
    """What prompt version `version` changed, for the intervention record. The axe note says what this run's
    villagers carry: a run's world (and so its starting tools) is the one it started with."""
    if version == 4 and "stone axe" not in ((world.get("tools") or {}).get("start_with") or []):
        stone = ("a stone axe only gathers wood" if "stone axe" in json.dumps(world)
                 else "this run's island has no stone axe")
        return ("axes: chopping wood with the axe (iron) brings the tree down when it takes the tree's last wood; "
                f"{stone}. Fruit trees have wood too, and drop their fruit when felled. There is no separate "
                "chop_down_tree. This run's villagers started with the axe, so they keep it")
    return PROMPT_CHANGES[version]


def _drop_unfinished_interventions(run_dir: Path, day: int) -> None:
    """A resume replays from the end of `day`: interventions recorded for later days belong to the
    attempt that didn't finish (its events were set aside), and the resume records its own again."""
    path = run_dir / "manifest.json"
    if not path.exists():
        return
    m = json.loads(path.read_text())
    kept = [i for i in m.get("interventions", []) if i.get("from_day", 0) <= day]
    if len(kept) != len(m.get("interventions", [])):
        if kept:
            m["interventions"] = kept
        else:
            m.pop("interventions")
        path.write_text(json.dumps(m, indent=2, ensure_ascii=False))


def _set_aside_unfinished(run_dir: Path, offset: int | None, day: int) -> Path | None:
    """Before a resume replays from the checkpoint at the end of `day`: move whatever was logged
    after it (a day that didn't finish) out of events.jsonl into events.unfinished-<time>.jsonl,
    so the replayed day isn't logged twice. Checkpoints from before the log position was saved
    are cut after the last event of `day`. Returns the file the tail was moved to, if any."""
    path = run_dir / "events.jsonl"
    if not path.exists():
        return None
    data = path.read_bytes()
    if offset is None:
        offset, pos = None, 0
        for line in data.splitlines(keepends=True):
            pos += len(line)
            try:
                if json.loads(line).get("day") == day:
                    offset = pos
            except ValueError:
                continue
        if offset is None:
            return None
    tail = data[offset:]
    later = [json.loads(ln) for ln in tail.splitlines() if ln.strip()]
    if not any(isinstance(e.get("day"), int) and e["day"] > day for e in later):
        return None  # nothing but bookkeeping after the checkpoint: the day finished cleanly
    out = run_dir / f"events.unfinished-{int(time.time())}.jsonl"
    out.write_bytes(tail)
    path.write_bytes(data[:offset])
    return out


TIRED = ("tired",)  # marker: the villager had no energy for this call, so it wasn't made
HABIT = ("habit",)  # marker: the villager chose habit for this planning time, so no call was made


def _thoughts(obj) -> str:
    """A villager's private reasoning. Logged for the observer; never shown to other villagers."""
    return str((obj or {}).get("thoughts") or "").strip()


def _pools_delta(before: dict, after: dict) -> dict:
    """Only the public resources this action changed, as [before, after]."""
    out = {}
    for k, v in after.items():
        if before.get(k) != v:
            if k == "rocks":
                out["rocks"] = {i + 1: [b, a] for i, (b, a) in enumerate(zip(before[k], v)) if b != a}
            else:
                out[k] = [before.get(k), v]
    return out


def parse_action_obj(text: str):
    """Pull the first JSON object out of a reply. Free models are messy, so this tolerates
    code fences, reasoning blocks and trailing chatter."""
    import re

    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    s, e = t.find("{"), t.rfind("}")
    if s < 0 or e <= s:
        return None, "no JSON object in reply"
    try:
        obj = json.loads(t[s:e + 1])
    except json.JSONDecodeError as err:
        return None, f"invalid JSON: {err}"
    return (obj, None) if isinstance(obj, dict) else (None, "reply JSON is not an object")
