"""Reading a run back from disk, plus the shared plot palette."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .island.space import Event

SERIES = [
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#6250d6",
    "#e34948",
]


@dataclass
class Run:
    """A run read back from disk: its folder, its manifest and every event it logged."""

    dir: Path
    manifest: dict
    events: list[Event]

    def of(self, type_: str) -> list[Event]:
        """The events of one type, in the order logged."""
        return [e for e in self.events if e["type"] == type_]


def backend_names(manifest: dict) -> list[str]:
    """The backends a run's villagers answer from, in alphabetical order: the one the run forced on
    everyone, else each villager's own.
    """
    if manifest.get("backend_override"):
        return [manifest["backend_override"]]
    return sorted({v["model"] for v in manifest["villagers"].values()})


def backend_spec(manifest: dict, name: str) -> tuple[str, str, dict]:
    """(url, model, request settings) of a backend as the manifest recorded it ("url|model|{json}").
    A backend recorded by name alone (mock, or an old run) gives ("", name, {}).
    """
    parts = (manifest.get("backends") or {}).get(name, name).split("|")
    if len(parts) == 1:
        return "", parts[0], {}
    try:
        params = json.loads(parts[2]) if len(parts) > 2 else {}
    except ValueError:
        params = {}
    return parts[0], parts[1], params


def read_events(path: Path) -> list[Event]:
    """Every event in an events.jsonl file, in the order logged."""
    with Path(path).open() as f:
        return [json.loads(line) for line in f if line.strip()]


def load_run(run_dir: Path) -> Run:
    """A run's manifest and its whole event log."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    return Run(run_dir, manifest, read_events(run_dir / "events.jsonl"))
