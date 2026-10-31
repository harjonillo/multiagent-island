"""Build the island explorer: a static page from a run's site_data.json.

The page is one self-contained HTML file (the data and the map are inside it), so it can be
opened from disk or served by any static host such as GitHub Pages. Its top bar links back to the
Experiments page of the runs folder it sits in (site_experiments.py).
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from .site_data import write as write_site_data
from .site_experiments import TOKENS, build_experiments, experiment_order, is_run

TEMPLATE = Path(__file__).with_name("site_template.html")

def _crumb(run_dir: Path) -> str:
    """`multiagent-island / experiments / exp NN`, numbered from 00 as on the Experiments page (a mock
    run has no number: it isn't on that page). The logo links back to the Experiments page."""
    runs = [p.resolve() for p in experiment_order(run_dir.parent)]
    n = runs.index(run_dir.resolve()) if run_dir.resolve() in runs else None
    home = "../../site/index.html"
    return (f'<a href="{home}">multiagent-island</a><span class="sep">/ experiments'
            + (f' / exp {n:02d}' if n is not None else "") + "</span>")


def build_site(run_dir: Path, out_dir: Path | None = None) -> Path:
    """Write <out_dir>/index.html (default <run_dir>/site/) from <run_dir>/site_data.json."""
    run_dir = Path(run_dir)
    src = run_dir / "site_data.json"
    if not src.exists():
        raise FileNotFoundError(f"{src} not found; run `llmabm analyze {run_dir}` first")
    data = json.loads(src.read_text())
    out_dir = Path(out_dir) if out_dir else run_dir / "site"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Inside <script>, "</" could end the element early; JSON allows escaping it.
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = html.escape(f"{data['run']['scenario']}, seed {data['run']['seed']}")
    page = (TEMPLATE.read_text().replace("__TOKENS__", TOKENS.read_text()).replace("__CRUMB__", _crumb(run_dir))
            .replace("__TITLE__", title).replace("__DATA__", payload))
    out = out_dir / "index.html"
    out.write_text(page)
    return out


def build_all(runs_dir: Path) -> list[Path]:
    """Every experiment's page in a runs folder (mock runs aside), then the Experiments page. A run without
    site_data.json gets it first (no LLM calls; the same data `llmabm analyze` writes)."""
    runs_dir = Path(runs_dir)
    out = []
    for run_dir in experiment_order(runs_dir):
        if not (run_dir / "site_data.json").exists():
            write_site_data(run_dir)
        out.append(build_site(run_dir))
    out.append(build_experiments(runs_dir))
    return out


__all__ = ["build_all", "build_site", "build_experiments", "is_run"]
