"""Command line: run, analyze, trace."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

from .analysis import build_viewer, cascade_text, load_run, plot_all, summary
from .llm import LLMError, load_backends
from .scenario import load_scenario
from .world import Simulation


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def analyze(run_dir: Path) -> None:
    run_dir = Path(run_dir)
    if json.loads((run_dir / "manifest.json").read_text()).get("type") == "island":
        from .island.analysis import analyze_island

        analyze_island(run_dir)
        return
    run = load_run(run_dir)
    text = summary(run)
    (run.dir / "summary.txt").write_text(text + "\n")
    print(text)
    for p in plot_all(run, run.dir):
        print(f"wrote {p}")
    print(f"wrote {build_viewer(run, run.dir / 'viewer.html')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="llmabm", description="LLM agent-based models of information flow")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a scenario")
    r.add_argument("scenario", type=Path)
    r.add_argument("--seed", type=int, default=0, help="controls network, traits, scheduling and the cache key")
    r.add_argument("--steps", "--days", dest="steps", type=int, help="override the scenario's ticks (or days, for island scenarios)")
    r.add_argument("--backend", help="use this backend for every agent (e.g. mock, groq)")
    r.add_argument("--config", type=Path, default=Path("config/models.yaml"))
    r.add_argument("--concurrency", type=int, default=1, help="parallel LLM calls within a tick")
    r.add_argument("--no-cache", action="store_true")
    r.add_argument("--log-prompts", action="store_true", help="save every full prompt under the run dir")
    r.add_argument("--out", type=Path, default=Path("runs"))
    r.add_argument("--run-dir", type=Path, help="exact run directory (default: runs/<timestamp>_<scenario>_s<seed>)")
    r.add_argument("--no-analyze", action="store_true")

    rs = sub.add_parser("resume", help="continue an island run from its last checkpoint")
    rs.add_argument("run_dir", type=Path)
    rs.add_argument("--days", type=int, default=1, help="how many more days to run (default 1)")
    rs.add_argument("--backend", help="switch every villager (and Isabelle) to this backend")
    rs.add_argument("--config", type=Path, default=Path("config/models.yaml"))
    rs.add_argument("--concurrency", type=int)
    rs.add_argument("--no-cache", action="store_true")
    rs.add_argument("--no-analyze", action="store_true")

    a = sub.add_parser("analyze", help="summary, plots and viewer for a run")
    a.add_argument("run_dir", type=Path)

    t = sub.add_parser("trace", help="print the cascade tree from one seed")
    t.add_argument("run_dir", type=Path)
    t.add_argument("seed_id")

    args = ap.parse_args(argv)
    load_dotenv()

    if args.cmd == "run":
        is_island = (yaml.safe_load(args.scenario.read_text()) or {}).get("type") == "island"
        if is_island:
            from .island import IslandSimulation, load_island_scenario

            sc = load_island_scenario(args.scenario, seed=args.seed)
            models = {a.model for a in sc.villagers.values()}
        else:
            sc = load_scenario(args.scenario, seed=args.seed)
            models = {a.model for a in sc.agents.values()}
        cache = None if args.no_cache else Path(".cache/llm")
        try:
            backends = load_backends(args.config, cache, salt=str(args.seed))
        except LLMError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        # Fail fast on missing backends or keys rather than logging an error per call.
        for name in {args.backend} if args.backend else models:
            b = backends.get(name)
            if b is None:
                print(f"error: backend '{name}' is not defined in {args.config}", file=sys.stderr)
                return 2
            env = getattr(getattr(b, "inner", b), "api_key_env", None)
            if env and not os.environ.get(env):
                print(f"error: backend '{name}' needs {env}; add it to .env or your environment", file=sys.stderr)
                return 2
        stamp = time.strftime("%Y%m%d-%H%M%S")
        run_dir = args.run_dir or args.out / f"{stamp}_{args.scenario.stem}_s{args.seed}"
        if (run_dir / "checkpoint.pkl").exists():
            print(f"error: {run_dir} already has a run; use `llmabm resume {run_dir}`", file=sys.stderr)
            return 2
        common = dict(seed=args.seed, backend_override=args.backend, concurrency=args.concurrency, log_prompts=args.log_prompts)
        if is_island:
            print(f"{sc.name}: {len(sc.villagers)} villagers, {args.steps or sc.days} days -> {run_dir}")
            from .island.sim import RunAborted

            try:
                IslandSimulation(sc, backends, run_dir, **common).run(args.steps)
            except RunAborted as e:
                print(f"stopped: {e}", file=sys.stderr)
                return 3
        else:
            print(f"{sc.name}: {len(sc.agents)} agents, {sc.graph.number_of_edges()} edges, "
                  f"{args.steps or sc.steps} ticks -> {run_dir}")
            Simulation(sc, backends, run_dir, **common).run(args.steps)
        if not args.no_analyze:
            analyze(run_dir)
    elif args.cmd == "resume":
        from .island.sim import IslandSimulation, RunAborted

        if not (args.run_dir / "checkpoint.pkl").exists():
            print(f"error: no checkpoint in {args.run_dir}", file=sys.stderr)
            return 2
        seed = json.loads((args.run_dir / "manifest.json").read_text())["seed"]
        cache = None if args.no_cache else Path(".cache/llm")
        backends = load_backends(args.config, cache, salt=str(seed))
        try:
            IslandSimulation.resume(args.run_dir, backends, days=args.days, backend_override=args.backend,
                                    concurrency=args.concurrency)
        except RunAborted as e:
            print(f"stopped: {e}", file=sys.stderr)
            return 3
        if not args.no_analyze:
            analyze(args.run_dir)
    elif args.cmd == "analyze":
        analyze(args.run_dir)
    elif args.cmd == "trace":
        print(cascade_text(load_run(args.run_dir), args.seed_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
