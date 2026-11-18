"""Put the replies kept in a run's prompt files back into the reply cache, so that a resume replays them.

For a day that was run with --no-cache before that flag saved its replies (November 2026): the replies
were made and logged in the prompt files but never cached, so a later resume could not replay them.

    python scripts/seed_cache_from_prompts.py <run_dir> <folder of prompt files> [<events file for plans>]

The folder is searched for d*.txt at any depth. A plan whose prompt file was overwritten by a later
attempt can be taken from an events file instead (its `plan` events keep the raw reply): the prompt is
then read from the run's current file for that plan, which is the same prompt. No LLM calls.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import re
import sys
from pathlib import Path

from llmabm.llm import load_backends

CACHE = Path(".cache/llm")
MAX_TOKENS = {"notice": 500, "spot": 400}  # as in sim.py; everything else uses the scenario's
_FILE = re.compile(r"\A=== SYSTEM ===\n(.*)\n\n=== USER ===\n(.*)\n\n=== REASONING \((\d+) tokens[^)]*\) ===\n(.*)\n\n"
                   r"=== REPLY ===\n(.*)\n\Z", re.S)
_PROMPT = re.compile(r"\A=== SYSTEM ===\n(.*)\n\n=== USER ===\n(.*?)\n(?:\n=== REASONING |\Z)", re.S)


def put(model_id, system, user, temperature, max_tokens, salt, text, reasoning="", reasoning_tokens=0) -> None:
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    key = hashlib.sha256(json.dumps([model_id, msgs, temperature, max_tokens, salt], sort_keys=True).encode()).hexdigest()
    path = CACHE / key[:2] / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model_id, "text": text, "reasoning": reasoning,
                                "reasoning_tokens": reasoning_tokens}))


def main(run_dir: Path, folder: Path, events: Path | None) -> None:
    with open(run_dir / "checkpoint.pkl", "rb") as f:
        sim = pickle.load(f)
    seed = json.loads((run_dir / "manifest.json").read_text())["seed"]
    backends = load_backends(Path("config/models.yaml"))
    model_of = lambda name: backends[sim.override or sim.sc.villagers[name].model].model_id  # noqa: E731
    n = skipped = 0
    for p in sorted(folder.rglob("d*.txt")):
        m = _FILE.match(p.read_text())
        name = p.parent.parent.name
        if not m or name not in sim.sc.villagers:
            skipped += 1
            continue
        system, user, tokens, reasoning, text = m.groups()
        kind = next((k for k in MAX_TOKENS if f"_{k}" in p.stem), None)
        put(model_of(name), system, user, sim.sc.temperature, MAX_TOKENS.get(kind, sim.sc.max_tokens), str(seed), text,
            "" if reasoning == "(none returned)" else reasoning, int(tokens))
        n += 1
    plans = 0
    if events:
        for line in events.read_text().splitlines():
            e = json.loads(line) if line.strip() else {}
            if e.get("type") != "plan" or not e.get("raw"):
                continue
            tag = f"d{e['day']:03d}_p{e['phase']}.txt"
            if (folder / "villagers" / e["agent"] / "prompts" / tag).exists():
                continue  # its own file was kept, and is the better source (it has the reasoning)
            now = run_dir / "villagers" / e["agent"] / "prompts" / tag
            m = _PROMPT.match(now.read_text()) if now.exists() else None
            if not m:
                skipped += 1
                continue
            put(model_of(e["agent"]), m.group(1), m.group(2), sim.sc.temperature, sim.sc.max_tokens, str(seed), e["raw"])
            plans += 1
    print(f"cached {n} replies from prompt files and {plans} plans from events; skipped {skipped}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) > 3 else None)
