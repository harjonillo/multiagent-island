"""Scenario = population + network + exogenous inputs (seeds) + measurements (probes)."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import yaml

from .agent import AgentSpec, load_agent_file


@dataclass
class Seed:
    id: str
    tick: int
    to: list[str]
    content: str
    source: str = "outside source"


@dataclass
class Probe:
    id: str
    question: str
    options: list[str]
    ticks: list[int]


@dataclass
class Scenario:
    name: str
    description: str
    world_brief: str
    steps: int
    agents: dict[str, AgentSpec]
    graph: nx.Graph
    seeds: list[Seed]
    probes: list[Probe]
    temperature: float = 0.7
    max_tokens: int = 600
    raw: dict = field(default_factory=dict)


def _sample(value, rng: random.Random):
    """Trait value spec: scalar, list (uniform choice), {uniform: [a, b]}, {normal: [mu, sd]}."""
    if isinstance(value, list):
        return rng.choice(value)
    if isinstance(value, dict):
        if "uniform" in value:
            return round(rng.uniform(*value["uniform"]), 2)
        if "normal" in value:
            return round(rng.gauss(*value["normal"]), 2)
        if "choice" in value:
            return rng.choice(value["choice"])
    return value


def _build_graph(spec: dict, n: int, rng: random.Random) -> nx.Graph:
    kind = spec.get("type", "watts_strogatz")
    s = rng.randrange(2**31)
    if kind == "watts_strogatz":
        return nx.connected_watts_strogatz_graph(n, spec.get("k", 4), spec.get("p", 0.1), seed=s)
    if kind == "barabasi_albert":
        return nx.barabasi_albert_graph(n, spec.get("m", 2), seed=s)
    if kind == "erdos_renyi":
        return nx.gnp_random_graph(n, spec.get("p", 0.2), seed=s)
    if kind == "complete":
        return nx.complete_graph(n)
    if kind == "explicit":
        return nx.Graph()
    raise ValueError(f"unknown network type: {kind}")


def load_scenario(path: Path, seed: int = 0) -> Scenario:
    path = Path(path)
    root = path.parent.parent if path.parent.name == "scenarios" else path.parent
    cfg = yaml.safe_load(path.read_text())
    rng = random.Random(seed)

    agents: dict[str, AgentSpec] = {}
    for entry in cfg.get("agents", []):
        a = load_agent_file(root / entry["file"], root, name=entry.get("name"), overrides=entry.get("set"))
        agents[a.name] = a
    for pop in cfg.get("population", []):
        names = list(pop.get("names", []))
        for i in range(pop["count"]):
            name = names[i] if i < len(names) else f"{pop.get('prefix', 'agent')}{i}"
            traits = {k: _sample(v, rng) for k, v in (pop.get("traits") or {}).items()}
            overrides = {**(pop.get("set") or {}), "trait_values": traits}
            agents[name] = load_agent_file(root / pop["template"], root, name=name, overrides=overrides)

    names = list(agents)
    net = cfg.get("network", {})
    g = _build_graph(net, len(names), rng)

    # Place agents on nodes: "hub" agents take the highest-degree nodes, the rest at random.
    if net.get("type") == "explicit":
        g.add_nodes_from(names)
    else:
        nodes = sorted(g.nodes, key=lambda v: -g.degree(v))
        hubs = [a for a in names if agents[a].placement == "hub"]
        mapping = {nodes[i]: a for i, a in enumerate(hubs)}
        rest_nodes = nodes[len(hubs):]
        rng.shuffle(rest_nodes)
        mapping.update(zip(rest_nodes, [a for a in names if a not in hubs]))
        g = nx.relabel_nodes(g, mapping)
    for u, v in net.get("edges", []):
        g.add_edge(u, v)
    for u, v in net.get("remove_edges", []):
        if g.has_edge(u, v):
            g.remove_edge(u, v)
    unknown = set(g.nodes) - set(names)
    if unknown:
        raise ValueError(f"network references unknown agents: {sorted(unknown)}")

    steps = int(cfg.get("steps", 10))
    seeds = [
        Seed(
            id=s["id"],
            tick=int(s.get("tick", 0)),
            to=s["to"] if isinstance(s["to"], list) else [s["to"]],
            content=s["content"].strip(),
            source=s.get("source", "outside source"),
        )
        for s in cfg.get("seeds", [])
    ]
    probes = []
    for p in cfg.get("probes", []):
        ticks = p.get("ticks") or sorted(set(range(0, steps, int(p.get("every", 1)))) | {steps - 1})
        # YAML reads bare yes/no as booleans; turn them back into words.
        opts = [("yes" if o else "no") if isinstance(o, bool) else str(o) for o in p.get("options", ["yes", "no", "unsure"])]
        probes.append(Probe(p["id"], p["question"].strip(), opts, ticks))

    llm = cfg.get("llm", {})
    return Scenario(
        name=cfg.get("name", path.stem),
        description=cfg.get("description", ""),
        world_brief=cfg.get("world_brief", ""),
        steps=steps,
        agents=agents,
        graph=g,
        seeds=seeds,
        probes=probes,
        temperature=float(llm.get("temperature", 0.7)),
        max_tokens=int(llm.get("max_tokens", 600)),
        raw=cfg,
    )
