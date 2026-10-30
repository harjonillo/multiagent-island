"""Reading a run back from disk, plus the shared plot palette."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]


@dataclass
class Run:
    dir: Path
    manifest: dict
    events: list[dict]

    def of(self, type_: str) -> list[dict]:
        return [e for e in self.events if e["type"] == type_]


def load_run(run_dir: Path) -> Run:
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines() if line.strip()]
    return Run(run_dir, manifest, events)
