"""Build the island explorer: a static page from a run's site_data.json.

The page is one self-contained HTML file (the data and the map are inside it), so it can be
opened from disk or served by any static host such as GitHub Pages. Its top bar links back to the
Experiments page of the runs folder it sits in (site_experiments.py).
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from .site_data import VERSION as SITE_DATA_VERSION
from .site_data import write as write_site_data
from .site_experiments import TOKENS, build_experiments, experiment_order, is_run

TEMPLATE = Path(__file__).with_name("site_template.html")

def _crumb(run_dir: Path, home: str) -> str:
    """`multiagent-island / experiments / exp NN`, numbered from 00 as on the Experiments page (a mock
    run has no number: it isn't on that page). The logo links back to the Experiments page."""
    runs = [p.resolve() for p in experiment_order(run_dir.parent)]
    n = runs.index(run_dir.resolve()) if run_dir.resolve() in runs else None
    return (f'<a href="{home}">multiagent-island</a><span class="sep">/ experiments'
            + (f' / exp {n:02d}' if n is not None else "") + "</span>")


def fresh_site_data(run_dir: Path) -> Path:
    """<run_dir>/site_data.json, written again (no LLM calls) if it is missing, older than the run's
    events.jsonl or manifest.json (say after a resume that stopped at a daily cap, which skips
    `llmabm analyze`), or from an older version of the site code."""
    run_dir = Path(run_dir)
    out = run_dir / "site_data.json"
    if out.exists():
        newest = max((run_dir / f).stat().st_mtime for f in ("events.jsonl", "manifest.json") if (run_dir / f).exists())
        try:
            current = json.loads(out.read_text()).get("version") == SITE_DATA_VERSION
        except ValueError:
            current = False
        if current and out.stat().st_mtime >= newest:
            return out
    return write_site_data(run_dir)


def build_site(run_dir: Path, out_dir: Path | None = None, home: str = "../../site/index.html") -> Path:
    """Write <out_dir>/index.html (default <run_dir>/site/) from <run_dir>/site_data.json, brought up to
    date first (fresh_site_data). `home` is the link to the Experiments page, relative to the page."""
    run_dir = Path(run_dir)
    data = json.loads(fresh_site_data(run_dir).read_text())
    out_dir = Path(out_dir) if out_dir else run_dir / "site"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Inside <script>, "</" could end the element early; JSON allows escaping it.
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = html.escape(f"{data['run']['scenario']}, seed {data['run']['seed']}")
    page = (TEMPLATE.read_text().replace("__TOKENS__", TOKENS.read_text()).replace("__CRUMB__", _crumb(run_dir, home))
            .replace("__TITLE__", title).replace("__DATA__", payload))
    out = out_dir / "index.html"
    out.write_text(page)
    return out


def build_all(runs_dir: Path, out_dir: Path | None = None) -> list[Path]:
    """Every experiment's page in a runs folder (mock runs aside), then the Experiments page. Each run's
    site_data.json is written first if it is missing or out of date (fresh_site_data; no LLM calls),
    so a run needs only manifest.json and events.jsonl.

    By default the pages go inside the runs folder (<run>/site/, and site/ for the Experiments page).
    With out_dir, the whole site goes there instead, ready to publish: index.html is the Experiments
    page and <run>/index.html each run's."""
    runs_dir = Path(runs_dir)
    out = []
    for run_dir in experiment_order(runs_dir):
        if out_dir:
            out.append(build_site(run_dir, Path(out_dir) / run_dir.name, home="../index.html"))
        else:
            out.append(build_site(run_dir))
    out.append(build_experiments(runs_dir, out_dir))
    return out


__all__ = ["build_all", "build_site", "build_experiments", "fresh_site_data", "is_run"]
