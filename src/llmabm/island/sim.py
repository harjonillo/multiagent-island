"""Island simulation loop.

The island runs on a 24-hour clock that rolls over at 05:00. Each action takes one
in-game hour. Villagers plan at the start of each plan window (one LLM call each),
giving every action an hour. The engine then steps hour by hour. Villagers acting
in the same hour reach a place in order of distance from their home; equal
distances are shuffled. At the social hours villagers meet at the plaza for
messages, board posts, trades and recipe teaching.

On day 0 villagers arrive (all at once or trickling in), and each picks a homesite
for their tent. At the end of every day Isabelle (an LLM call) writes a summary of
the day from the log, and the whole simulation is checkpointed so it can be resumed
later, one day at a time if you like.
"""

from __future__ import annotations

import json
import os
import pickle
import random
import threading
import time
from collections import defaultdict
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from ..agent import AgentSpec, load_agent_file
from ..llm import LLMError
from .prompts import (ISABELLE_SYSTEM, arrival_prompt, encounter_prompt, evening_prompt, isabelle_announce_prompt,
                      light_system_prompt, notice_prompt, reflect_prompt,
                      isabelle_summary_prompt, plan_prompt, system_prompt)
from .energy import MentalEnergy
from .body import Bodies
from .space import PlaceSpace
from .world import Island, clock, load_world, location_of, to_game_hour


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
    world_facts: str
    days: int
    villagers: dict[str, AgentSpec]
    goals: dict[str, list[str]]
    brief: str = ""
    start_bells: int = 1000
    memory_size: int = 15
    temperature: float = 0.8
    max_tokens: int = 700
    arrivals: dict = field(default_factory=lambda: {"mode": "trickle", "window": [5, 18]})
    space: str = "places"        # "places" (named points) or "grid" (tile island)
    free_time: str = "home"      # "home" or "wander" (random walk; grid only)
    isabelle: dict = field(default_factory=lambda: {"summary": True, "announce": False, "backend": None})
    reflection: dict = field(default_factory=lambda: {"enabled": False})
    mental_energy: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def load_island_scenario(path: Path, seed: int = 0) -> IslandScenario:
    path = Path(path)
    root = path.parent.parent if path.parent.name == "scenarios" else path.parent
    cfg = yaml.safe_load(path.read_text())
    rng = random.Random(f"goals|{seed}")
    villagers = {}
    entries = cfg["villagers"]
    if isinstance(entries, str):  # path to a cast file: a list of {name, <traits>...}
        entries = yaml.safe_load((root / entries).read_text())
    template = cfg.get("villager_template", "villagers/villager.md")
    for v in entries:
        v = dict(v)
        name = v.pop("name")
        file = v.pop("file", template)
        overrides = {**v.pop("set", {}), "trait_values": v}
        spec = load_agent_file(root / file, root, name=name, overrides=overrides)
        villagers[spec.name] = spec
    goals: dict[str, list[str]] = {n: [] for n in villagers}
    g = cfg.get("goals") or {}
    for n in villagers:
        goals[n] += list(g.get("shared", []))
        personal = (g.get("personal") or {}).get(n)
        if personal:
            goals[n] += personal if isinstance(personal, list) else [personal]
        elif g.get("personal_pool"):
            goals[n].append(rng.choice(g["personal_pool"]))
    facts = cfg.get("world_facts", "canonical")
    world = load_world(root / cfg.get("world", "data/island_world.yaml"), facts, seed)
    world["calendar"].update(cfg.get("calendar") or {})  # scenario can override the clock
    world.setdefault("encounters", {}).update(cfg.get("encounters") or {})
    world.setdefault("grid", {}).update(cfg.get("grid") or {})
    world.setdefault("mental_energy", {}).update(cfg.get("mental_energy") or {})
    llm = cfg.get("llm", {})
    return IslandScenario(
        name=cfg.get("name", path.stem),
        description=cfg.get("description", ""),
        world=world,
        world_facts=facts,
        days=int(cfg.get("days", 30)),
        villagers=villagers,
        goals=goals,
        brief=cfg.get("brief", ""),
        start_bells=int(cfg.get("start_bells", 1000)),
        memory_size=int(cfg.get("memory_size", 15)),
        temperature=float(llm.get("temperature", 0.8)),
        max_tokens=int(llm.get("max_tokens", 700)),
        arrivals={"mode": "trickle", "window": [5, 18], **(cfg.get("arrivals") or {})},
        space=str(cfg.get("space", "places")),
        free_time=str(cfg.get("free_time", "home")),
        isabelle={"summary": True, "announce": False, "backend": None, **(cfg.get("isabelle") or {})},
        reflection={"enabled": False, "max_words": 150, "items": [], **(cfg.get("reflection") or {})},
        mental_energy=dict(world["mental_energy"]),
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
        # The mind (LLM) plans; bodies walk and act in space; the world applies the rules.
        if sc.space == "grid":
            from .grid import GridSpace

            self.space = GridSpace(sc.world, seed)
            # Homesites come from the generated island instead of the named-place map.
            self.isl.sites = {sid: {"area": v["area"], "at": list(v["door"])} for sid, v in self.space.sites.items()}
        else:
            self.space = PlaceSpace(sc.world)
        self.space.attach(self.isl)
        self.isl.space_desc = self.space.describe()
        self.bodies = Bodies(self.isl, self.space)
        for n, spec in sc.villagers.items():  # schedules come from the cast traits
            vil = self.isl.v[n]
            vil.wakes = int(spec.traits.get("wakes", vil.wakes))
            vil.sleeps = int(spec.traits.get("sleeps", vil.sleeps))
            vil.routine = str(spec.traits.get("routine", ""))
        # Mental energy: a daily token budget each villager splits between planning, thinking and talking.
        self.energy = MentalEnergy(sc.mental_energy, list(sc.villagers))
        self.isl.energy = self.energy
        self.habits: dict[tuple[str, int], list[dict]] = {}
        self._build_systems()
        self.yesterday: dict[str, list[str]] = {n: [] for n in sc.villagers}
        self.offers: dict[str, Offer] = {}
        self._oid = 0
        self.calls = 0
        self.failures = 0
        self.max_failures = 15  # consecutive failed calls before giving up on the run
        # Arrival hours on day 0, in game hours.
        start = sc.world["calendar"]["day_start"]
        a, b = (to_game_hour(x, start) if x < 24 else x for x in sc.arrivals.get("window", [5, 18]))
        arng = random.Random(f"arrivals|{seed}")
        if sc.arrivals.get("mode", "trickle") == "together":
            self.arrival_hour = {n: a for n in sc.villagers}
        else:
            self.arrival_hour = {n: arng.randrange(a, max(a + 1, b)) for n in sc.villagers}
        self.noticed: dict[str, list[str]] = {n: [] for n in sc.villagers}
        self.window_end = start

    # --- persistence -------------------------------------------------------
    def __getstate__(self):
        state = dict(self.__dict__)
        for k in ("backends", "log", "_raw_log"):
            state.pop(k, None)
        return state

    def _checkpoint(self) -> None:
        tmp = self.run_dir / "checkpoint.pkl.tmp"
        with open(tmp, "wb") as f:
            pickle.dump(self, f)
        os.replace(tmp, self.run_dir / "checkpoint.pkl")
        m = json.loads((self.run_dir / "manifest.json").read_text())
        m["days_completed"] = self.isl.day + 1
        (self.run_dir / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False))

    @classmethod
    def resume(cls, run_dir: Path, backends: dict, days: int = 1, backend_override: str | None = None,
               concurrency: int | None = None, verbose: bool = True) -> Path:
        """Continue a run from its last checkpoint for `days` more days, appending to the same logs.
        Only load checkpoints you made yourself: pickle files can run code when loaded."""
        run_dir = Path(run_dir)
        with open(run_dir / "checkpoint.pkl", "rb") as f:
            sim: IslandSimulation = pickle.load(f)
        sim.backends = backends
        sim.run_dir = run_dir
        if not hasattr(sim, "energy"):  # checkpoints from before mental energy existed
            sim.energy = MentalEnergy({"enabled": False}, list(sim.isl.v))
            sim.isl.energy, sim.habits = sim.energy, {}
        sim.verbose = verbose
        if backend_override:
            sim.override = backend_override
        if concurrency:
            sim.concurrency = concurrency
        sim.failures = 0
        # Rebuild system prompts from the saved specs with the current prompt code.
        sim._build_systems()
        sim._open_log("a")
        sim.log("resume", from_day=sim.isl.day + 1, days=days, time=time.time())
        try:
            sim._run_days(days)
        finally:
            sim.hour = None
            sim.log("run_end", time=time.time(), calls=sim.calls)
            sim._raw_log.close()
        return run_dir

    def _build_systems(self) -> None:
        """System prompts: persona, the villager's own self-description (if reflection is on),
        island rules and goals. Rebuilt after every nightly reflection."""
        sc = self.sc
        on = bool(sc.reflection.get("enabled"))
        selfdoc = lambda n: self.isl.v[n].self_doc if on else None  # noqa: E731
        extra = self.space.rules_text() if hasattr(self.space, "rules_text") else ""
        self.systems = {n: system_prompt(s, sc.world, len(sc.villagers), sc.goals[n], sc.brief, selfdoc(n), extra)
                        for n, s in sc.villagers.items()}
        self.light_systems = {n: light_system_prompt(s, sc.goals[n], selfdoc(n)) for n, s in sc.villagers.items()}

    def _reflect(self, d: int) -> None:
        """Nightly: each villager privately rewrites who they think they are and rates fixed items."""
        cfg = self.sc.reflection
        if not cfg.get("enabled"):
            return
        isl = self.isl
        items = list(cfg.get("items") or [])
        max_words = int(cfg.get("max_words", 150))
        who = [n for n in isl.v if isl.arrived[n] is not None]

        def reflect(name):
            user = reflect_prompt(isl, isl.v[name], items, max_words)
            if not self.energy.can(name, "thinking", self._need(name, user, self.light_systems[name])):
                return TIRED
            return self._call(name, user, f"d{d:03d}_reflect", system=self.light_systems[name], json_reply=True,
                              max_tokens=max_words * 3 + 200, kind="thinking", what="reflect")

        results = self._parallel(reflect, who)
        for name in who:
            if results[name] is TIRED:
                self.energy.skip(name, "thinking")
                self.log("reflection", day=d, agent=name, self_doc=isl.v[name].self_doc, ratings=[], items=items,
                         thoughts="", error=None, raw=None, skipped="no thinking energy left")
                continue
            reply, obj, err = results[name]
            text = str((obj or {}).get("self") or "").strip()
            words = text.split()
            if len(words) > max_words * 1.3:
                text = " ".join(words[: int(max_words * 1.3)])
            if text:
                isl.v[name].self_doc = text
            ratings = (obj or {}).get("ratings")
            clean = []
            if isinstance(ratings, list):
                for r in ratings[: len(items)]:
                    try:
                        clean.append(max(1, min(5, int(round(float(r))))))
                    except (TypeError, ValueError):
                        clean.append(None)
            self.log("reflection", day=d, agent=name, self_doc=isl.v[name].self_doc, ratings=clean,
                     items=items, thoughts=_thoughts(obj), error=err, raw=reply.text if reply else None)
        self._build_systems()

    def _open_log(self, mode: str) -> None:
        self._raw_log = EventLog(self.run_dir / "events.jsonl", mode)
        self.hour = None
        # Every event carries the in-game hour it happened at.
        self.log = lambda type_, **k: self._raw_log(type_, **{"hour": self.hour, **k})

    # --- llm plumbing ------------------------------------------------------
    def _need(self, name: str, user: str, system: str | None = None) -> float:
        """Points of mental energy it takes to read this prompt."""
        return self.energy.points(system or self.systems[name], user)

    def _call(self, name: str, user: str, tag: str, system: str | None = None, backend_name: str | None = None,
              max_tokens: int | None = None, json_reply: bool = False, kind: str | None = None, what: str = "other"):
        if backend_name is None:
            backend_name = self.override or self.sc.villagers[name].model
        backend = self.backends[backend_name]
        msgs = [{"role": "system", "content": system or self.systems[name]}, {"role": "user", "content": user}]
        if self.log_prompts:
            d = self.run_dir / "prompts"
            d.mkdir(exist_ok=True)
            (d / f"{tag}_{name}.txt").write_text(f"=== SYSTEM ===\n{msgs[0]['content']}\n\n=== USER ===\n{user}\n")
        try:
            reply = backend.complete(msgs, self.sc.temperature, max_tokens or self.sc.max_tokens)
        except LLMError as e:
            self.failures += 1
            if self.failures >= self.max_failures:
                raise RunAborted(
                    f"{self.failures} LLM calls in a row failed (last: {e}). Likely a rate or daily cap. "
                    "Rerun later with the same seed: cached replies replay the finished part for free.") from e
            return None, None, str(e)
        self.failures = 0
        self.calls += 1
        if system is not None and not json_reply:  # free-text calls (Isabelle) are not JSON
            return reply, None, None
        obj, err = parse_action_obj(reply.text)
        if name in self.isl.v:
            thought = " ".join(str((obj or {}).get(k) or "") for k in ("thoughts", "note"))
            if obj and not self.energy.has_thinking(name):  # no energy left to think: nothing private is kept
                obj["thoughts"], obj["note"] = "", ""
            self.energy.record(name, kind, what, self.energy.points(msgs[0]["content"], user),
                               self.energy.points(reply.text), self.energy.points(thought),
                               immediate=threading.current_thread() is threading.main_thread())
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
            self.log("run_end", time=time.time(), calls=self.calls)
            self._raw_log.close()
        return self.run_dir

    def _run_days(self, days: int) -> None:
        for _ in range(days):
            self._day()
            self._checkpoint()

    def _day(self) -> None:
        isl, log = self.isl, self.log
        cal = self.sc.world["calendar"]
        isl.new_day()
        self.energy.new_day()
        self.space.new_day(isl, self.rng)
        self.space.sync(isl)
        self.bodies.new_day()
        d = isl.day
        self.hour = cal["day_start"]
        self.first_plan = {n: True for n in isl.v}
        self.used = {n: 0 for n in isl.v}
        self.schedule: dict[int, list[tuple[str, dict]]] = {}
        self.speak = list(isl.v)
        self.noticed = {n: [] for n in isl.v}
        self.chats_today = {n: 0 for n in isl.v}
        self.decisions_today = {n: 0 for n in isl.v}
        log("day_start", day=d, money_rock=isl.money_rock + 1, cranny=isl.cranny_stock,
            cranny_recipe=isl.cranny_recipe, able=isl.able_stock, pools=isl.pools(),
            ordinance=isl.ordinance, news=isl.yesterday_news, announcement=isl.announcement,
            month=isl.month, season=isl.season, weather=isl.weather, date=isl.date_text(),
            awake={n: list(isl.awake_window(n)) for n in isl.v})
        for i, (a, b) in enumerate(cal["plans"]):
            self.hour = a
            self.window_end = b
            self._plan(d, i, a, b)
            for h in range(a, b):
                self.hour = h
                self._run_hour(d, h)
        for n, vil in isl.v.items():
            self.yesterday[n] = list(vil.today)
            vil.notes = vil.notes[-self.sc.memory_size:]
        self.hour = cal["day_start"] + cal.get("day_length", 24)
        snap = isl.snapshot()
        if self.sc.space == "grid":
            snap["objects"] = self.space.objects_snapshot(isl)
        log("snapshot", **snap)
        self._reflect(d)
        log("energy", day=d, enabled=self.energy.enabled, villagers=self.energy.day_record())
        self._isabelle(d)
        if self.verbose:
            trades = sum(1 for o in self.offers.values() if o.day == d and o.status == "accepted")
            print(f"day {d:>3}: museum {sum(len(m) for m in isl.museum.values())}, "
                  f"bridge {isl.bridge_fund}, trades {trades}, calls so far {self.calls}", flush=True)

    # --- arrivals ----------------------------------------------------------
    def _arrive(self, d: int, h: int) -> None:
        """Villagers landing this hour pick a homesite and plan the rest of the window.
        Same-hour arrivals choose one after another, so later ones see earlier picks."""
        isl, log = self.isl, self.log
        names = [n for n, hh in self.arrival_hour.items() if hh == h and isl.arrived[n] is None]
        self.rng.shuffle(names)
        budget = self.sc.world["calendar"]["actions_per_day"]
        for name in names:
            isl.arrived[name] = h
            vil = isl.v[name]
            awake = [x for x in range(h + 1, self.window_end) if isl.is_awake(name, x)]
            user = arrival_prompt(isl, vil, h, awake, min(budget, len(awake)))
            reply, obj, err = self._call(name, user, f"d{d:03d}_arrival", kind="planning", what="plan")
            self.energy.resplit(name, (obj or {}).get("energy"))
            site, msg = isl.settle(name, (obj or {}).get("homesite"))
            self.bodies.place_at_home(name)
            vil.today.append(f"{clock(h)} arrived on the island and {msg}")
            log("arrival", day=d, agent=name, wanted=(obj or {}).get("homesite"), homesite=site, thoughts=_thoughts(obj),
                area=isl.sites[site]["area"] if site else None, result=msg, error=err,
                raw=reply.text if reply else None)
            acts = [x for x in (obj or {}).get("actions", []) if isinstance(x, dict)] if obj else []
            if awake:
                scheduled, dropped = self._assign_hours(name, acts, awake[0], awake[-1] + 1)
                for hh, act in scheduled:
                    self.schedule.setdefault(hh, []).append((name, act))
                log("plan", day=d, phase="arrival", agent=name, window=[h, self.window_end],
                    scheduled=[{"at": hh, **act} for hh, act in scheduled], dropped=dropped,
                    note="", error=err, raw=None, cached=reply.cached if reply else False)
            note = str((obj or {}).get("note") or "").strip()
            if note:
                vil.notes.append((d, note))

    # --- encounters --------------------------------------------------------
    def _radius(self) -> float:
        cfg = self.sc.world.get("encounters") or {}
        return float(cfg.get("grid_radius", 6) if self.sc.space == "grid" else cfg.get("radius", 0.5))

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
            if not self.energy.can(name, "talking", self._need(name, user, self.light_systems[name])):
                return TIRED
            return self._call(name, user, f"d{d:03d}_h{h % 24:02d}_notice", system=self.light_systems[name],
                              json_reply=True, max_tokens=250, kind="talking", what="notice")

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
                self._chat(d, h, self.bodies.b[name].place, name, target, int(cfg.get("turns", 2)),
                           opener={"speaker": name, "text": opener, "thoughts": _thoughts(obj), "error": err,
                                   "_obj": obj or {}})

    def _chat(self, d: int, h: int, place: str, a: str, b: str, n_turns: int, opener: dict | None = None,
              visit: bool = False) -> None:
        """A one-on-one conversation. `a` speaks first. On a visit, `a` is the guest and `b` the host.
        Each turn can also hand something over ("give") or ask for something ("ask")."""
        isl = self.isl
        turns: list[dict] = []
        if opener:
            obj0 = opener.pop("_obj", None) or {}
            self._chat_acts(d, a, b, obj0, opener)
            turns.append(opener)
        for i in range(len(turns), max(1, min(4, n_turns))):
            speaker, other = (a, b) if i % 2 == 0 else (b, a)
            role = None if not visit else ("guest" if speaker == a else "host")
            user = encounter_prompt(isl, isl.v[speaker], other, place, h, turns, opener=(i == 0), visit=role)
            if not self.energy.can(speaker, "talking", self._need(speaker, user)):
                self.energy.skip(speaker, "talking")
                turns.append({"speaker": speaker, "text": "", "thoughts": "", "error": None,
                              "act": "just nodded (too tired to talk)"})
                break
            reply, obj, err = self._call(speaker, user, f"d{d:03d}_h{h % 24:02d}_chat", kind="talking", what="chat")
            say = str((obj or {}).get("say") or "").strip()[:500]
            turn = {"speaker": speaker, "text": say, "thoughts": _thoughts(obj), "error": err}
            self._chat_acts(d, speaker, other, obj or {}, turn)
            turns.append(turn)
            note = str((obj or {}).get("note") or "").strip()
            if note:
                isl.v[speaker].notes.append((d, note))
            if not say and not turn.get("act"):
                break
        self.chats_today[a] = self.chats_today.get(a, 0) + 1
        self.chats_today[b] = self.chats_today.get(b, 0) + 1
        spoken = [t for t in turns if t["text"] or t.get("act")]
        for me, other in ((a, b), (b, a)):
            where = (f"visited {other} at home" if me == a else f"{other} visited you at home") if visit \
                else f"ran into {other} at the {place}"
            if spoken:
                lines = "; ".join(f'{"you" if t["speaker"] == me else other} said "{t["text"][:220]}"'
                                  + (f" [{t['act']}]" if t.get("act") else "") for t in spoken)
                isl.v[me].today.append(f"{clock(h)} {where}: {lines}")
            else:
                isl.v[me].today.append(f"{clock(h)} {where}; " + ("nobody said much" if visit else "you just waved"))
        self.log("encounter", day=d, location=place, villagers=[a, b], turns=turns, visit=visit)

    def _chat_acts(self, d: int, speaker: str, other: str, obj: dict, turn: dict) -> None:
        """In-person gifts and requests made during a conversation."""
        isl = self.isl
        acts = []
        give = obj.get("give")
        if isinstance(give, dict) and _clean_side(give):
            ok, what = isl.transfer(speaker, other, _clean_side(give))
            self.log("gift", day=d, giver=speaker, receiver=other, items=_clean_side(give), ok=ok, how="in person",
                     result=what)
            acts.append(f"gave {what}" if ok else f"tried to give something but {what}")
        ask = obj.get("ask")
        if isinstance(ask, dict) and _clean_side(ask):
            f = isl.ask_favor(speaker, other, _clean_side(ask))
            if f:
                self.log("favor_asked", day=d, **{k: v for k, v in f.items() if k != "day"}, asked_day=f["day"])
                acts.append("asked for " + ", ".join(f"{n} {k}" for k, n in f["items"].items()))
        for f in isl.favors_done_today:
            self.log("favor_done", day=d, **{k: v for k, v in f.items() if k != "day"}, asked_day=f["day"])
        isl.favors_done_today = []
        if acts:
            turn["act"] = "; ".join(acts)

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
                self._chat(d, h, f"{host}'s home", g, host, int(self.sc.world["encounters"].get("turns", 2)), visit=True)
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
                                       system=ISABELLE_SYSTEM, backend_name=backend_name, max_tokens=700)
            text = (reply.text.strip() if reply else "") or f"(no summary: {err})"
            days_dir = self.run_dir / "days"
            days_dir.mkdir(exist_ok=True)
            (days_dir / f"day_{d:03d}_isabelle.md").write_text(f"# Isabelle's report, day {d}\n\n{text}\n")
            self.log("isabelle_summary", day=d, text=text, error=err, digest_chars=len(digest))
        if cfg.get("announce"):
            pub = public_digest(self.isl, events)
            reply, _, err = self._call("isabelle", isabelle_announce_prompt(d + 1, pub), f"d{d:03d}_announce",
                                       system=ISABELLE_SYSTEM, backend_name=backend_name, max_tokens=300)
            self.isl.announcement = reply.text.strip() if reply else None
            self.log("isabelle_announcement", day=d, for_day=d + 1, text=self.isl.announcement, error=err)

    def _plan(self, d: int, phase: int, a: int, b: int) -> None:
        """One LLM call per awake villager to schedule actions in hours [a, b).
        Villagers asleep for the whole window, or out of energy, are not called."""
        isl, log = self.isl, self.log
        budget = self.sc.world["calendar"]["actions_per_day"]
        who = []
        for n in isl.v:
            awake = [h for h in range(a, b) if isl.is_awake(n, h)]
            if awake and self.used[n] < budget:
                who.append(n)

        for n in who:
            isl.where_notes[n] = self.bodies.where_note(n)

        def plan(name):
            vil = isl.v[name]
            awake = [h for h in range(a, b) if isl.is_awake(name, h)]
            left = budget - self.used[name]
            user = plan_prompt(isl, vil, awake[0], awake[-1] + 1, min(left, len(awake)), self.yesterday[name],
                               announcement=self.first_plan[name], noticed=self.noticed[name][-8:])
            if not self.energy.can(name, "planning", self._need(name, user)):
                return TIRED
            self.first_plan[name] = False
            vil.inbox = []
            return self._call(name, user, f"d{d:03d}_p{phase}", kind="planning", what="plan")

        results = self._parallel(plan, who)
        for name in who:
            awake = [h for h in range(a, b) if isl.is_awake(name, h)]
            if results[name] is TIRED:  # no planning energy: do what they did yesterday at this time of day
                self.energy.skip(name, "planning")
                acts = [dict(x) for x in self.habits.get((name, phase), [])]
                scheduled, dropped = self._assign_hours(name, acts, awake[0], awake[-1] + 1)
                for h, act in scheduled:
                    self.schedule.setdefault(h, []).append((name, act))
                isl.v[name].today.append(f"{clock(a)} too tired to plan, so you "
                                         + ("did what you did yesterday" if scheduled else "took it easy"))
                log("plan", day=d, phase=phase, agent=name, window=[a, b], thoughts="", habit=True,
                    scheduled=[{"at": h, **act} for h, act in scheduled], dropped=dropped, note="", error=None,
                    raw=None, cached=False)
                continue
            reply, obj, err = results[name]
            acts = [x for x in (obj or {}).get("actions", []) if isinstance(x, dict)] if obj else []
            scheduled, dropped = self._assign_hours(name, acts, awake[0], awake[-1] + 1)
            for h, act in scheduled:
                self.schedule.setdefault(h, []).append((name, act))
            self.habits[(name, phase)] = [{"hour": h % 24, **act} for h, act in scheduled]
            split = self.energy.resplit(name, (obj or {}).get("energy"))
            note = str((obj or {}).get("note") or "").strip()
            if note:
                isl.v[name].notes.append((d, note))
            log("plan", day=d, phase=phase, agent=name, window=[a, b], thoughts=_thoughts(obj),
                scheduled=[{"at": h, **act} for h, act in scheduled], dropped=dropped, note=note, error=err,
                energy_split=split, raw=reply.text if reply else None, cached=reply.cached if reply else False)

    def _assign_hours(self, name: str, acts: list[dict], a: int, b: int):
        """Place each action at its requested hour, or the next free hour in the window.
        One action per hour; stops when the day's action budget runs out."""
        budget = self.sc.world["calendar"]["actions_per_day"]
        taken = {h for h, lst in self.schedule.items() for n, _ in lst if n == name}
        out, dropped, cursor = [], [], a
        for act in acts:
            # Keys the body fills in (targets, what was seen) can't come from the mind.
            act = {k: v for k, v in act.items() if not str(k).startswith("_") and k not in ("none_seen", "none_out", "habitat")}
            if self.used[name] >= budget:
                dropped.append({**act, "why": "no actions left today"})
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
            self.used[name] += 1
            cursor = h + 1
            out.append((h, act))
        return sorted(out, key=lambda x: x[0]), dropped

    def _run_hour(self, d: int, h: int) -> None:
        isl, log = self.isl, self.log
        cal = self.sc.world["calendar"]
        social = cal.get("social_hours", [])
        if d == 0:
            self._arrive(d, h)
        spoke = set()
        if h in social:
            spoke = self._social_round(d, social.index(h))
        self.space.hour_tick(isl, h, self.rng)  # bugs and fish shadows come and go
        doing: dict[str, dict] = {}
        ok_by: dict[str, bool] = {}
        # Same hour: whoever has the shorter walk from where they are gets there first; ties are luck.
        batch = self.bodies.order(list(self.schedule.get(h, [])), self.rng)
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
        # Free time: wander (random walk) or stay home; asleep villagers are home.
        wandering = set()
        for n in isl.v:
            if n in doing or isl.arrived[n] is None:
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
        out = [n for n in doing if self.bodies.b[n].place != "home"] + sorted(wandering)
        near = self.bodies.nearby(out, self._radius())
        for n in out:
            if n in doing and near[n]:
                isl.v[n].today[-1] += f" (also nearby: {', '.join(near[n])})"
        for n in doing:
            place = self.bodies.b[n].place
            if place != "home":
                for m in isl.neighbors(n):
                    if isl.is_awake(m, h) and m not in doing:
                        self.noticed[m].append(f"{clock(h)} your neighbor {n} headed to the {place}")
        pairs = sorted({tuple(sorted((a, b))) for a, ms in near.items() for b in ms})
        if pairs:
            log("seen", day=d, pairs=[list(p) for p in pairs],
                places={n: self.bodies.b[n].place for p in pairs for n in p})
        busy: set[str] = set()
        self._visits(d, h, doing, ok_by, busy)
        self._encounters(d, h, [n for n in out if n not in busy], near)
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
        speak = [n for n in self.speak if isl.is_awake(n, self.hour)]
        if not speak:
            return set()
        before = set(self.offers)

        def turn(name):
            vil = isl.v[name]
            waiting = [o for o in self.offers.values() if o.recipient == name and o.status == "pending"]
            user = evening_prompt(isl, vil, r, self.hour, waiting, isl.board[-8:])
            if not self.energy.can(name, "talking", self._need(name, user)):
                return TIRED
            vil.inbox = []
            return self._call(name, user, f"d{d:03d}_h{self.hour % 24:02d}", kind="talking", what="evening")

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

        for resp in obj.get("responses") or []:
            if not isinstance(resp, dict):
                continue
            o = self.offers.get(str(resp.get("offer", "")).strip())
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

        for m in obj.get("messages") or []:
            if not isinstance(m, dict):
                continue
            to, content = who(m.get("to")), str(m.get("content") or "").strip()
            if not to or to == name or not content:
                log("invalid", day=d, agent=name, what="message", detail=m)
                continue
            isl.v[to].inbox.append(f"(day {d}, {clock(self.hour)}) from {name}: {content}")
            log("message", day=d, round=r, sender=name, recipient=to, content=content)

        post = str(obj.get("board") or "").strip()
        if post:
            isl.board.append((d, name, post))
            log("board", day=d, round=r, author=name, content=post)

        for o in obj.get("offers") or []:
            if not isinstance(o, dict):
                continue
            to = who(o.get("to"))
            give, want = _clean_side(o.get("give")), _clean_side(o.get("want"))
            if not to or to == name or not (give or want):
                log("invalid", day=d, agent=name, what="offer", detail=o)
                continue
            self._new_offer(d, name, to, give, want, str(o.get("note") or "")[:300])

        for t in obj.get("teach") or []:
            if not isinstance(t, dict):
                continue
            to, rec = who(t.get("to")), str(t.get("recipe") or "").strip().lower()
            if not to or to == name or rec not in vil.recipes:
                log("invalid", day=d, agent=name, what="teach", detail=t)
                continue
            new = rec not in isl.v[to].recipes
            isl.v[to].recipes.add(rec)
            isl.v[to].inbox.append(f"(day {d}) {name} taught you the {rec} recipe")
            log("teach", day=d, round=r, teacher=name, learner=to, recipe=rec, new=new)

        note = str(obj.get("note") or "").strip()
        if note:
            vil.notes.append((d, note))

    def _new_offer(self, d, sender, recipient, give, want, note, parent=None):
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
        for src, dst, side in ((a, b, o.give), (b, a, o.want)):
            for k, n in side.items():
                if k == "bells":
                    src.bells -= n
                    dst.bells += n
                else:
                    src.inventory[k] -= n
                    dst.inventory[k] += n
        o.status = "accepted"
        if not o.want:  # a gift by mail can fulfil an open request
            isl._settle_favors(o.sender, o.recipient, o.give)
            for f in isl.favors_done_today:
                self.log("favor_done", day=d, **{k: v for k, v in f.items() if k != "day"}, asked_day=f["day"])
            isl.favors_done_today = []
        value = lambda side: sum(n if k == "bells" else n * isl.prices.get(k, 0) for k, n in side.items())  # noqa: E731
        self.log("offer_update", **asdict(o))
        self.log("trade", day=d, offer=o.id, seller=o.sender, buyer=o.recipient, give=o.give, want=o.want,
                 give_cranny_value=value(o.give), want_cranny_value=value(o.want))
        a.inbox.append(f"(day {d}) {b.name} accepted your offer [{o.id}]")

    def _write_manifest(self, days: int) -> None:
        m = {
            "type": "island",
            "scenario": self.sc.name,
            "description": self.sc.description,
            "seed": self.seed,
            "days": days,
            "world_facts": self.sc.world_facts,
            "space": self.sc.space,
            "free_time": self.sc.free_time,
            "backend_override": self.override,
            "villagers": {n: {"persona": s.persona, "traits": s.traits, "model": self.override or s.model,
                              "goals": self.sc.goals[n], "source": s.source} for n, s in self.sc.villagers.items()},
            "world": self.sc.world,
            "scenario_config": self.sc.raw,
            "arrivals": {n: clock(h) for n, h in self.arrival_hour.items()},
            "system_prompts": self.systems,
        }
        (self.run_dir / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False))


TIRED = ("tired",)  # marker: the villager had no energy for this call, so it wasn't made


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
