"""Villagers are defined by markdown files: YAML frontmatter for parameters, body for
the persona prompt. Their 'rules' are the prompt plus whatever documents they
carry, not hand-coded update equations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

import yaml


@dataclass
class AgentSpec:
    """One villager as its markdown file defines it: the persona, the traits and the backend."""

    name: str
    persona: str
    traits: dict = field(default_factory=dict)
    docs: list[tuple[str, str]] = field(default_factory=list)  # (filename, text)
    model: str = "default"
    source: str = ""
    templated: set = field(
        default_factory=set
    )  # traits already written into the persona via $name

    def extra_traits(self) -> dict:
        """The traits the persona text does not already state through a `$name` placeholder."""
        return {k: v for k, v in self.traits.items() if k not in self.templated}


def split_frontmatter(text: str) -> tuple[dict, str]:
    """(YAML frontmatter, body) of a markdown file; the frontmatter is empty when there is none."""
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        return yaml.safe_load(fm) or {}, body.strip()
    return {}, text.strip()


def load_agent_file(
    path: Path, root: Path, name: str | None = None, overrides: dict | None = None
) -> AgentSpec:
    """Read a villager from a markdown file.

    Args:
        path: The markdown file.
        root: The folder the file's `docs:` paths are relative to.
        name: The villager's name; by default the frontmatter's `name`, else the file's name.
        overrides: Frontmatter keys to replace. `trait_values` here is laid over the file's `traits`.

    Returns:
        The villager, with each `$trait` and `$name` in the persona filled in.
    """
    meta, body = split_frontmatter(Path(path).read_text())
    meta = {**meta, **(overrides or {})}
    traits = {**(meta.get("traits") or {}), **(meta.pop("trait_values", None) or {})}
    name = name or meta.get("name") or Path(path).stem
    docs = []
    for d in meta.get("docs") or []:
        p = (root / d).resolve()
        docs.append((p.name, p.read_text().strip()))
    persona = Template(body).safe_substitute(
        {"name": name, **{k: v for k, v in traits.items()}}
    )
    return AgentSpec(
        name=name,
        persona=persona,
        traits=traits,
        docs=docs,
        model=meta.get("model", "default"),
        source=str(path),
        templated={
            k
            for k in traits
            if re.search(rf"\$(?:{re.escape(k)}\b|\{{{re.escape(k)}\}})", body)
        },
    )
