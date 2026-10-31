"""Command line: run, resume, analyze."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

from .island import IslandSimulation, load_island_scenario
from .island.analysis import analyze_island
from .island.sim import RunAborted
from .llm import LLMError, load_backends


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="llmabm", description="LLM agent-based model of an island")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a scenario")
    r.add_argument("scenario", type=Path)
    r.add_argument("--seed", type=int, default=0, help="controls the island, traits, scheduling and the cache key")
    r.add_argument("--steps", "--days", dest="steps", type=int, help="override the scenario's days")
    r.add_argument("--backend", help="use this backend for every agent (e.g. mock, groq)")
    r.add_argument("--config", type=Path, default=Path("config/models.yaml"))
    r.add_argument("--concurrency", type=int, default=1, help="parallel LLM calls within an hour")
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
    rs.add_argument("--prompt-version", help="switch the run's prompt wording from the next day on ('latest' or a "
                                             "number); logged as an intervention. By default a run keeps its wording")

    a = sub.add_parser("analyze", help="summary, plots, logs and the site data for a run")
    a.add_argument("run_dir", type=Path)

    st = sub.add_parser("site", help="build the static explorer page from a run's site_data.json")
    st.add_argument("run_dir", type=Path)
    st.add_argument("--out", type=Path, help="folder for index.html (default <run_dir>/site)")

    args = ap.parse_args(argv)
    load_dotenv()

    if args.cmd == "run":
        if (yaml.safe_load(args.scenario.read_text()) or {}).get("type") != "island":
            print(f"error: {args.scenario} is not an island scenario (missing `type: island`)", file=sys.stderr)
            return 2
        sc = load_island_scenario(args.scenario, seed=args.seed)
        models = {a.model for a in sc.villagers.values()}
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
        print(f"{sc.name}: {len(sc.villagers)} villagers, {args.steps or sc.days} days -> {run_dir}")
        try:
            IslandSimulation(sc, backends, run_dir, **common).run(args.steps)
        except RunAborted as e:
            print(f"stopped: {e}", file=sys.stderr)
            return 3
        if not args.no_analyze:
            analyze_island(run_dir)
    elif args.cmd == "resume":
        if not (args.run_dir / "checkpoint.pkl").exists():
            print(f"error: no checkpoint in {args.run_dir}", file=sys.stderr)
            return 2
        seed = json.loads((args.run_dir / "manifest.json").read_text())["seed"]
        cache = None if args.no_cache else Path(".cache/llm")
        backends = load_backends(args.config, cache, salt=str(seed))
        try:
            IslandSimulation.resume(args.run_dir, backends, days=args.days, backend_override=args.backend,
                                    concurrency=args.concurrency, prompt_version=args.prompt_version)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        except RunAborted as e:
            print(f"stopped: {e}", file=sys.stderr)
            return 3
        if not args.no_analyze:
            analyze_island(args.run_dir)
    elif args.cmd == "analyze":
        analyze_island(args.run_dir)
    elif args.cmd == "site":
        from .island.site_build import build_site

        try:
            print(f"wrote {build_site(args.run_dir, args.out)}")
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
