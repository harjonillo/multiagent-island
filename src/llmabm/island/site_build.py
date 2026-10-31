"""Build the island explorer: a static page from a run's site_data.json.

The page is one self-contained HTML file (the data and the map are inside it), so it can be
opened from disk or served by any static host such as GitHub Pages.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("site_template.html")


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
    page = TEMPLATE.read_text().replace("__TITLE__", title).replace("__DATA__", payload)
    out = out_dir / "index.html"
    out.write_text(page)
    return out
