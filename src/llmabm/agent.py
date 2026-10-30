"""Agents are defined by markdown files: YAML frontmatter for parameters, body for
the persona prompt. Their 'rules' are the prompt plus whatever documents they
carry, not hand-coded update equations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

import yaml


@dataclass
class Message:
    id: str
    tick: int  # tick it was sent (for seeds: tick it was injected)
    sender: str
    recipient: str
    content: str
    based_on: list[str] = field(default_factory=list)
    kind: str = "msg"  # "msg" or "seed"


@dataclass
class AgentSpec:
    name: str
    persona: str
    traits: dict = field(default_factory=dict)
    docs: list[tuple[str, str]] = field(default_factory=list)  # (filename, text)
    model: str = "default"
    attention: int = 6  # max messages read per tick; the rest are dropped
    memory_size: int = 12  # private notes kept
    activation: float = 0.0  # chance of acting with an empty inbox
    placement: str | None = None  # "hub" puts the agent on a high-degree node
    source: str = ""
    templated: set = field(default_factory=set)  # traits already written into the persona via $name

    def extra_traits(self) -> dict:
        return {k: v for k, v in self.traits.items() if k not in self.templated}


@dataclass
class AgentState:
    beliefs: str = ""
    notes: list[tuple[int, str]] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)


def split_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        return yaml.safe_load(fm) or {}, body.strip()
    return {}, text.strip()


def load_agent_file(path: Path, root: Path, name: str | None = None, overrides: dict | None = None) -> AgentSpec:
    meta, body = split_frontmatter(Path(path).read_text())
    meta = {**meta, **(overrides or {})}
    traits = {**(meta.get("traits") or {}), **(meta.pop("trait_values", None) or {})}
    name = name or meta.get("name") or Path(path).stem
    docs = []
    for d in meta.get("docs") or []:
        p = (root / d).resolve()
        docs.append((p.name, p.read_text().strip()))
    persona = Template(body).safe_substitute({"name": name, **{k: v for k, v in traits.items()}})
    return AgentSpec(
        name=name,
        persona=persona,
        traits=traits,
        docs=docs,
        model=meta.get("model", "default"),
        attention=int(meta.get("attention", 6)),
        memory_size=int(meta.get("memory_size", 12)),
        activation=float(meta.get("activation", 0.0)),
        placement=meta.get("placement"),
        source=str(path),
        templated={k for k in traits if re.search(rf"\$(?:{re.escape(k)}\b|\{{{re.escape(k)}\}})", body)},
    )


RESPONSE_FORMAT = """Respond with a single JSON object and nothing else:
{
  "beliefs": "1-3 sentences on what you currently believe about the things that matter to you right now",
  "note": "a short private note to your future self, or empty string",
  "messages": [
    {"to": "<one contact's name, or \\"all\\">", "content": "<what you say>", "based_on": ["<ids of the received messages that led you to say this>"]}
  ]
}
"messages" can be an empty list. Sending nothing is a normal choice."""


def system_prompt(spec: AgentSpec, world_brief: str) -> str:
    parts = [spec.persona]
    if spec.extra_traits():
        parts.append("## Your traits\n" + "\n".join(f"- {k}: {v}" for k, v in spec.extra_traits().items()))
    for fname, text in spec.docs:
        parts.append(f"## Document you know: {fname}\n{text}")
    parts.append("## How this works\n" + world_brief.strip())
    parts.append(RESPONSE_FORMAT)
    return "\n\n".join(parts)


def state_block(spec: AgentSpec, state: AgentState, tick: int, contacts: list[str]) -> str:
    notes = "\n".join(f"- (round {t}) {n}" for t, n in state.notes) or "(none)"
    return (
        f"Round {tick}.\n"
        f"You are {spec.name}.\n"
        f"Your contacts: {', '.join(contacts) if contacts else '(none)'}\n"
        f"Your current beliefs: {state.beliefs or '(none yet)'}\n"
        f"Your private notes, oldest first:\n{notes}"
    )


def format_inbox(inbox: list[Message], labels: dict[str, str]) -> str:
    if not inbox:
        return "(no messages)"
    lines = []
    for m in inbox:
        sender = labels.get(m.id, m.sender)
        lines.append(f"[{m.id}] from {sender}: {' '.join(m.content.split())}")
    return "\n".join(lines)


def tick_prompt(spec, state, tick, contacts, inbox, labels) -> str:
    return (
        state_block(spec, state, tick, contacts)
        + "\n\nMessages that reached you this round:\n"
        + format_inbox(inbox, labels)
        + "\n\nDecide what you now believe and whether to tell anyone anything."
    )


def probe_prompt(spec, state, tick, contacts, question: str, options: list[str]) -> str:
    return (
        state_block(spec, state, tick, contacts)
        + "\n\nPROBE. This is a private survey question. It does not affect anything and nobody else sees it.\n"
        + f"{question}\nAnswer with one of: {', '.join(options)}.\nReply with only that word."
    )


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def parse_action(text: str) -> tuple[dict | None, str | None]:
    """Pull the first JSON object out of a model reply. Free models are messy, so
    this tolerates code fences, reasoning blocks and trailing chatter."""
    text = _THINK.sub("", text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None, "no JSON object in reply"
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e}"
    if not isinstance(obj, dict):
        return None, "reply JSON is not an object"
    msgs = obj.get("messages") or []
    if isinstance(msgs, dict):
        msgs = [msgs]
    return {
        "beliefs": str(obj.get("beliefs") or "").strip(),
        "note": str(obj.get("note") or "").strip(),
        "messages": [m for m in msgs if isinstance(m, dict)],
    }, None


def parse_probe(text: str, options: list[str]) -> str | None:
    t = _THINK.sub("", text or "").lower()
    hits = [(t.find(o.lower()), o) for o in options if re.search(rf"\b{re.escape(o.lower())}\b", t)]
    return min(hits)[1] if hits else None
