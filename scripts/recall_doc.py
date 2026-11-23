"""Write docs/recall_examples.md: what retrieval (prompt version 20) brings to mind, and with what scores,
for situations villagers were really in.

It reads a run without changing it. Each villager's memory is filled in from the run's own files, as a
resume with `--prompt-version 20` would do. Then, for every prompt the villagers were given on the last
days of the run, the situation is rebuilt from the saved prompt (the same parts of it the simulation
compares memories with) and scored against what that villager had lived through before that day.

    python scripts/recall_doc.py runs/test1                       # the default local model, beside `hash`
    python scripts/recall_doc.py runs/test1 --embeddings local-base --days 5

Only load checkpoints you made yourself: pickle files can run code when loaded.
"""

from __future__ import annotations

import argparse
import json
import pickle
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

from llmabm.island.memory import DEFAULTS, MemoryStore, dot, is_pick, recall, record_key, score_records, strength
from llmabm.island.sim import _last_chat_skip, _query
from llmabm.island.villager_files import prompt_dir
from llmabm.llm import load_embedder

ROOT = Path(__file__).resolve().parent.parent
FILE = re.compile(r"d(\d+)_(?:h(\d+)_)?(p|chat|notice|spot|plaza|reflect)(?:_?\d+)?\.txt$")
KINDS = {  # kind -> (what it is, which setting says how many records, what the situation is built from)
    "plan": ("A plan", "k", "where they are and what they can reach, the last 5 things that happened today, who they "
                            "noticed, unread letters, open requests, and the morning announcement in the first plan"),
    "chat": ("One line in a conversation", "k", "who they are talking with and where, and the last 4 things said"),
    "notice": ("Noticing someone nearby", "k_quick", "where they are, who is nearby, and the last 5 things that happened today"),
    "spot": ("Spotting a bug or a fish", "k_quick", "what they spotted, where they are, and what they had planned for that hour"),
    "plaza": ("A turn at the plaza", "k", "the last 8 posts on the board, letters, and the last 5 things that happened today"),
    "reflect": ("Writing at night", "shown", "everything that happened today"),
}


def user_part(path: Path) -> str:
    return path.read_text().split("=== USER ===", 1)[-1].split("\n=== REASONING", 1)[0]


def block(user: str, heading: str) -> list[str]:
    """The lines under the line that starts with `heading`, up to the next empty line."""
    lines = user.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith(heading):
            out = []
            for nxt in lines[i + 1:]:
                if not nxt.strip():
                    break
                out.append(nxt)
            return out
    return []


def bullets(user: str, heading: str) -> list[str]:
    return [ln[2:] for ln in block(user, heading) if ln.startswith("- ")]


def line(user: str, start: str) -> str:
    return next((ln for ln in user.splitlines() if ln.startswith(start)), "")


def situation(kind: str, user: str) -> tuple[list[tuple[str, list[str]]], str | None]:
    """(the parts of the query, each with a name and its lines; the villager they are talking to if any),
    from a saved prompt. The query is these parts in this order, joined into one text."""
    at = re.search(r"You are \w+, at (.+?)\.\n", user)
    here = [f"at {at.group(1)}"] if at else []
    one = lambda start: [ln for ln in [line(user, start)] if ln]
    if kind == "plan":
        parts = [("where they are", one("Getting around:")),
                 ("the last 5 things today", bullets(user, "So far today you:")[-5:]),
                 ("who they noticed", bullets(user, "You noticed today:")[-8:]),
                 ("unread letters", [ln for ln in block(user, "Unread letters:") if ln.strip() != "(none)"]),
                 ("open requests", one("Open requests:")),
                 ("the morning announcement", one("Isabelle's morning announcement:"))]
        return parts, None
    if kind == "notice":
        return [("where they are", here), ("who is nearby", one("Nearby:")),
                ("the last 5 things today", bullets(user, "Earlier today:")[-5:])], None
    if kind == "spot":
        planned = re.search(r"^At \d\d:\d\d you planned: (.*)$", user, re.M)
        return [("what they spotted", [re.sub(r"^\d+\. ", "", ln) for ln in block(user, "You just spotted:")]),
                ("where they are", here), ("what they had planned", [planned.group(1)] if planned else [])], None
    if kind == "chat":
        other = re.search(r"Only (\w+) hears it", user)
        other = other.group(1) if other else None
        lines = user.splitlines()
        said, meeting = [], ""
        for i, ln in enumerate(lines):
            if ln.endswith("So far:"):
                meeting = ln[: -len(" So far:")]
                said = block(user, ln)
                break
            if "hears it" in ln and i:  # the first line of a conversation: the sentence that says how they met
                meeting = lines[i - 1].split(". You can start", 1)[0]
        return [("who they are talking with", [f"talking with {other}"]), ("how they met", [meeting] if meeting else []),
                ("the last 4 things said", said[-4:])], other
    if kind == "plaza":
        return [("the last 8 posts on the board", bullets(user, "Bulletin board, newest last:")[-8:]),
                ("letters", [ln for ln in block(user, "Letters and messages to you:") if ln.strip() != "(none)"]),
                ("the last 5 things today", bullets(user, "So far today you:")[-5:])], None
    return [("what happened today", bullets(user, "What happened today:"))], None


def query_lines(parts, limit: int) -> list[str]:
    """The query as it is shown here: one line for each part, in order, up to where the query is cut."""
    out, room = [], limit
    for label, lines in parts:
        text = " ".join(" ".join(lines).split())
        if not text:
            out.append(f"- *{label}:* (nothing)")
            continue
        if room <= 0:
            out.append(f"- *{label}:* (past the {limit:,} characters; not in the query)")
            continue
        kept = text[:room]
        room -= len(text) + 1
        out.append(f"- *{label}:* {kept.replace('|', chr(92) + '|')}" + (" **[cut here]**" if len(kept) < len(text) else ""))
    return out


def load(run_dir: Path):
    """The run's last checkpoint with every lived day in memory, as switching it to version 20 would leave it.
    Nothing is written back."""
    with open(run_dir / "checkpoint.pkl", "rb") as f:
        sim = pickle.load(f)
    sim.run_dir = run_dir
    sim.isl.memory = {**DEFAULTS, **(sim.isl.memory or {})}
    sim.prompt_version = sim.isl.prompt_version = max(20, getattr(sim, "prompt_version", 1))
    sim._link()
    sim._backfill_memory()
    return sim


def rehearsals(run_dir: Path) -> dict[str, list[tuple[int, str]]]:
    """For each villager, (night, text) of every pick they named again, from the run's log."""
    out: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for ln in (run_dir / "events.jsonl").read_text().splitlines():
        e = json.loads(ln) if '"reflection"' in ln else {}
        for text in e.get("rehearsed") or []:
            out[e["agent"]].append((int(e["day"]), text))
    return out


def as_of(vil, day: int, cfg: dict, named_again: list[tuple[int, str]]):
    """The villager's memory as it stood on the morning of `day`: nothing from that day on, and each pick as
    fresh as it was then (the nights it was named again since are undone)."""
    picks = []
    for m in vil.memories:
        if m["day"] >= day:
            continue
        m = {**m, "stability": float(cfg["stability"]), "last": m["day"], "times": 1}
        for night, text in named_again:
            if m["day"] <= night < day and text == m["text"]:
                m.update(stability=m["stability"] * 2, last=night, times=m["times"] + 1)
        picks.append(m)
    return MemoryStore(SimpleNamespace(memories=picks, experience=[r for r in vil.experience if r["day"] < day]))


def scored(cands, qvec, vecs, day, cfg, k):
    """Every candidate with the parts of its score, best first, and the keys of the ones a prompt would show."""
    sims = [dot(qvec, vecs[r["text"]]) for r in cands]
    lo, hi = min(sims), max(sims)
    raw = {record_key(r): s for r, s in zip(cands, sims)}
    rows = []
    for score, r in score_records(cands, sims, day, cfg):
        s = raw[record_key(r)]
        rel = max(cfg["relevance_floor"], (s - lo) / (hi - lo)) if hi > lo else 1.0
        rec = max(cfg["recency_floor"], strength(r, day))
        rows.append({"score": score, "cos": s, "rel": rel, "rec": rec, "pick": cfg["pick_weight"] if is_pick(r) else 1.0, "r": r})
    shown = {record_key(r) for _, r in recall(cands, qvec, {r["text"]: vecs[r["text"]] for r in cands}, day, cfg, k)}
    return rows, shown


def what(r: dict) -> str:
    return {None: "held on to", "event": "happened", "chat": "conversation", "note": "note", "page": "nightly page"}[r.get("kind")]


def cell(text: str, n: int = 150) -> str:
    text = " ".join(str(text).split()).replace("|", "\\|")
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def med(xs) -> str:
    return f"{statistics.median(xs):.2f}" if xs else "–"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--embeddings", default=None, help="the embedder to show (default: the models file's default)")
    ap.add_argument("--beside", default="hash", help="a second embedder to compare what is shown with")
    ap.add_argument("--days", type=int, default=3, help="how many of the run's last days to take prompts from")
    ap.add_argument("--examples", type=int, default=2, help="worked examples for each kind of prompt")
    ap.add_argument("--config", type=Path, default=ROOT / "config/models.yaml")
    ap.add_argument("--out", type=Path, default=ROOT / "docs/recall_examples.md")
    args = ap.parse_args()

    emb = load_embedder(args.config, ROOT / ".cache/embed", args.embeddings)
    other = load_embedder(args.config, ROOT / ".cache/embed", args.beside)
    sim = load(args.run_dir)
    isl, cfg = sim.isl, sim.isl.memory
    last = isl.day
    days = range(max(1, last - args.days + 1), last + 1)

    # Every prompt of those days, with its situation and the records it could draw on.
    found, in_run, named_again = [], Counter(), rehearsals(args.run_dir)
    for name in sorted(isl.v):
        for path in sorted(prompt_dir(args.run_dir, name).glob("d*.txt")):
            m = FILE.search(path.name)
            if m:
                kind = "plan" if m.group(3) == "p" else m.group(3)
                in_run[kind] += 1
                found.append((name, int(m.group(1)), kind, path))
    # A kind with no prompt in those days (nobody went to the plaza, say) is taken from the last day it has.
    latest = {k: max(d for _, d, kind, _ in found if kind == k) for k in in_run}
    cases, earlier = [], {}
    for name, day, kind, path in found:
        if day not in days:
            if latest[kind] in days or day != latest[kind]:
                continue
            earlier[kind] = day
        parts, talking_to = situation(kind, user_part(path))
        query = " ".join(_query(*[lines for _, lines in parts]).split())[: int(cfg["query_chars"])]
        skip = _last_chat_skip(SimpleNamespace(last_chats=getattr(isl, "last_chats", {}), day=day), name, talking_to) \
            if talking_to else None
        cands = as_of(isl.v[name], day, cfg, named_again[name]).candidates(
            day, cfg, skip_yesterday=kind == "plan", night=kind == "reflect", skip=skip)
        if query and cands:
            cases.append({"name": name, "day": day, "kind": kind, "file": path.name, "query": query, "parts": parts, "cands": cands})

    texts = list(dict.fromkeys(r["text"] for c in cases for r in c["cands"]))
    queries = list(dict.fromkeys(c["query"] for c in cases))
    for e in (emb, other):
        e.p = dict(zip(texts, e.embed(texts, "passage")))
        e.q = dict(zip(queries, e.embed(queries, "query")))
    for c in cases:
        k = int(cfg[KINDS[c["kind"]][1]])
        c["rows"], c["shown"] = scored(c["cands"], emb.q[c["query"]], emb.p, c["day"], cfg, k)
        _, c["other"] = scored(c["cands"], other.q[c["query"]], other.p, c["day"], cfg, k)
        for row in c["rows"]:
            assert abs(row["rel"] * row["rec"] * row["pick"] - row["score"]) < 1e-5  # the columns are the score

    by_kind = defaultdict(list)
    for c in cases:
        by_kind[c["kind"]].append(c)
    order = sorted(by_kind, key=lambda k: -in_run[k])
    kept = Counter(what(r) for v in isl.v.values() for r in v.memory.corpus())
    span = f"day {days[0]}" if len(days) == 1 else f"days {days[0]} to {days[-1]}"

    out = [
        "# What comes to mind, and how it is scored",
        "",
        f"Written by `python scripts/recall_doc.py {args.run_dir}`; rerun it after changing the embedding model, "
        "the memory settings or the scoring, so this page can't drift. Nothing here is made up: the situations "
        f"are the prompts the {len(isl.v)} villagers of `{args.run_dir}` were really given on {span}, and the memories "
        f"are what each of them had lived through before that day ({sum(kept.values()):,} records in all: "
        + ", ".join(f"{n:,} {k}" for k, n in kept.most_common()) + ").",
        "",
        "That run was played with the older memory (a short list of picks), so this shows what retrieval *would* "
        "have brought to mind in those moments, not what the villagers saw. See "
        "[memory.md](memory.md#5-from-prompt-version-20-memory-by-retrieval) for the rules and a flowchart of "
        "the steps.",
        "",
        f"Embedding model: `{emb.model_id}`. Compared with: `{other.model_id}`.",
        "",
        "## What the query is",
        "",
        "Retrieval has no question written for it. The query is text taken from the prompt the villager is about to "
        "read: the parts listed for each kind of prompt below, in that order, joined with spaces into one text and cut "
        f"at {cfg['query_chars']:,} characters. That one text is embedded and compared with each record. Under each "
        "example the query is written out in full, one line for each part; the names in italics are not part of it.",
        "",
        "| Prompt | The query, in order |",
        "|---|---|",
        *[f"| {KINDS[k][0]} | {KINDS[k][2]} |" for k in order],
        "",
        "## How to read the numbers",
        "",
        "    score = relevance x recency x pick",
        "",
        "| Column | What it is |",
        "|---|---|",
        "| similarity | The cosine between the query's vector and the record's, straight from the embedding model. "
        "It is not used as it is. |",
        "| relevance | The similarity rescaled over that villager's candidates for that prompt: the closest record is "
        f"1.00, the farthest {cfg['relevance_floor']}. So it says how a record ranks for this prompt, not how good the match is. |",
        f"| recency | exp(-days since / stability), never below {cfg['recency_floor']}. Stability is {cfg['stability']:g} days, "
        "doubled each time a pick is named again at night. |",
        f"| pick | {cfg['pick_weight']:g} if the villager chose to hold on to it, else 1. |",
        "",
        f"A prompt shows the top records by score, at most {cfg['per_line']} pieces of any one line.",
        "",
        "## Across every prompt",
        "",
        f"All {len(cases)} prompts of {span} that had something to draw on (a kind nobody was given in those days is taken "
        f"from the last day it has, named in its row). \"In the run\" counts every prompt of that kind "
        f"over the whole run, days 0 to {last}; kinds are listed most common first. Similarities are medians over the prompts.",
        "",
        "| Prompt | In the run | Scored here | Records to draw on | Shown | Similarity: best shown | weakest shown | "
        f"all candidates | Recency of what is shown | Also shown by `{other.name}` |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for kind in order:
        cs = by_kind[kind]
        label = KINDS[kind][0] + (f" (day {earlier[kind]})" if kind in earlier else "")
        shown = [[r for r in c["rows"] if record_key(r["r"]) in c["shown"]] for c in cs]
        same = sum(len(c["shown"] & c["other"]) for c in cs) / max(1, sum(len(c["shown"]) for c in cs))
        out.append(f"| {label} | {in_run[kind]} | {len(cs)} | {statistics.mean(len(c['cands']) for c in cs):.0f} "
                   f"| {int(cfg[KINDS[kind][1]])} | {med([max(r['cos'] for r in s) for s in shown])} "
                   f"| {med([min(r['cos'] for r in s) for s in shown])} "
                   f"| {med([statistics.median(r['cos'] for r in c['rows']) for c in cs])} "
                   f"| {med([r['rec'] for s in shown for r in s])} | {same:.0%} |")
    mix = Counter(what(r["r"]) for c in cases for r in c["rows"] if record_key(r["r"]) in c["shown"])
    days_back = [c["day"] - r["r"]["day"] for c in cases for r in c["rows"] if record_key(r["r"]) in c["shown"]]
    out += ["",
            "What the shown records are: " + ", ".join(f"{k} {n / sum(mix.values()):.0%}" for k, n in mix.most_common())
            + f". Half of them are from {statistics.median(days_back):g} days back or less; the oldest is from {max(days_back)} days back.",
            "",
            "## Worked examples",
            "",
            "For each kind of prompt, the latest ones in the run. Rows marked ✓ go in the prompt; the rows after them "
            "are the next best that don't."]
    for kind in order:
        cs = sorted(by_kind[kind], key=lambda c: (-c["day"], c["name"], c["file"]))
        picked, seen = [], set()
        for c in cs:  # one from each villager, the ones with a full list first
            if c["name"] not in seen and len(c["cands"]) > len(c["shown"]):
                seen.add(c["name"])
                picked.append(c)
        out += ["", f"### {KINDS[kind][0]}"]
        for c in picked[: args.examples]:
            out += ["", f"**{c['name']}, day {c['day']}** (`{c['file']}`; {len(c['cands'])} records to draw on)", "",
                    f"Query ({len(c['query']):,} characters):", "", *query_lines(c["parts"], int(cfg["query_chars"])), "",
                    "| | score | similarity | relevance | recency | pick | from | text |", "|---|---|---|---|---|---|---|---|"]
            rest = 0
            for row in c["rows"]:
                on = record_key(row["r"]) in c["shown"]
                if not on:
                    rest += 1
                    if rest > 3:
                        continue
                out.append(f"| {'✓' if on else ''} | {row['score']:.3f} | {row['cos']:.3f} | {row['rel']:.2f} | {row['rec']:.2f} "
                           f"| {row['pick']:g} | day {row['r']['day']}, {what(row['r'])} | {cell(row['r']['text'])} |")
                if rest >= 3 and not (c["shown"] - {record_key(x["r"]) for x in c["rows"][: c["rows"].index(row) + 1]}):
                    break
            only = [r for r in c["cands"] if record_key(r) in c["other"] - c["shown"]]
            out += ["", f"`{other.name}` would show {len(c['shown'] & c['other'])} of the same {len(c['shown'])}"
                    + ("." if not only else "; in place of the others: " + "; ".join(f"\"{cell(r['text'], 90)}\"" for r in only[:3]) + ".")]
    out += ["",
            "## Limits of this page",
            "",
            "- Situations are rebuilt from the saved prompts, so they are close to, not exactly, what a version 20 run "
            "would compare with (a plan's \"what they can reach\" is the first line of that part only).",
            "- Picks are the ones the checkpoint still holds, each as fresh as it was on that day. What a villager had "
            "already let fade under the older rule is gone; in a run that started on version 20 it would still be there.",
            "- Records are in the villagers' and the game's own words, so the similarity numbers are specific to this "
            "run and this embedding model. Compare models by rerunning with `--embeddings <name>`.",
            ""]
    args.out.write_text("\n".join(out))
    print(f"wrote {args.out}: {len(cases)} prompts of {span}, {len(texts)} records and {len(queries)} situations embedded "
          f"with {emb.model_id}")


if __name__ == "__main__":
    main()
