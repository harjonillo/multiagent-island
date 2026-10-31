"""Offline villager policy for the mock backend. Random but valid-looking, so every
code path (scarcity, crafting, selling, trades, counters, teaching) gets exercised."""

from __future__ import annotations

import json
import random
import re

_ACTIONS = [
    ({"do": "mine_rock"}, 4), ({"do": "chop_wood"}, 3), ({"do": "shake_tree"}, 2),
    ({"do": "fish", "where": "sea"}, 2), ({"do": "fish", "where": "river", "size": "large"}, 1),
    ({"do": "fish", "where": "river"}, 1), ({"do": "beachcomb", "near": "south beach"}, 1), ({"do": "fish", "where": "pond"}, 1),
    ({"do": "fish", "where": "clifftop"}, 1),
    ({"do": "catch_bug"}, 1), ({"do": "catch_bug", "where": "flowers"}, 1), ({"do": "catch_bug", "where": "trees"}, 1),
    ({"do": "catch_bug", "where": "rocks"}, 1), ({"do": "beachcomb"}, 2), ({"do": "dig_fossil"}, 2),
]


def _inventory(prompt: str) -> dict[str, int]:
    m = re.search(r"^Inventory: (.*)$", prompt, re.M)
    if not m or m.group(1) == "nothing":
        return {}
    return {k.strip(): int(n) for k, n in re.findall(r"([^,]+?) x(\d+)", m.group(1))}


def _recipes(prompt: str) -> list[str]:
    m = re.search(r"^Recipes you know: (.*)$", prompt, re.M)
    return [] if not m or m.group(1) == "none" else re.findall(r"(?:^|; )([^;(]+?) \(", m.group(1))


def _bells(prompt: str) -> int:
    m = re.search(r"^Bells: (\d+)", prompt, re.M)
    return int(m.group(1)) if m else 0


def _neighbors(prompt: str) -> list[str]:
    m = re.search(r"neighbors: ([^.]*)\. Carrying", prompt)
    return [x.strip() for x in m.group(1).split(",")] if m else []


_FURNITURE = ("log stool", "campfire", "stone stool", "wooden chair", "log bench", "wooden table", "small lamp")


def plan_reply(prompt: str, rng: random.Random) -> str:
    m = re.search(r"Plan up to (\d+) actions between (\d+):00 and (\d+):00", prompt)
    n, a, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    window = list(range(a, b + 1)) if a <= b else list(range(a, 24)) + list(range(0, b + 1))  # wraps past midnight
    hours = rng.sample(window, min(n, len(window)))
    hours.sort(key=window.index)
    inv, recipes, bells = _inventory(prompt), _recipes(prompt), _bells(prompt)
    acts = []
    for _ in range(n):
        r = rng.random()
        if r < 0.12 and inv:
            k = rng.choice(list(inv))
            acts.append({"do": "sell", "items": {k: inv[k]}})
        elif r < 0.20 and inv:
            acts.append({"do": "donate", "item": rng.choice(list(inv))})
        elif r < 0.27 and recipes:
            tool = [t for t in ("vaulting pole", "ladder") if t in recipes and t not in inv]
            acts.append({"do": "craft", "recipe": tool[0] if tool and rng.random() < 0.6 else rng.choice(recipes)})
        elif r < 0.30 and bells > 2000:
            acts.append({"do": "contribute_bridge", "bells": rng.randint(500, bells // 2)})
        elif r < 0.32:
            acts.append({"do": "upgrade_house"})
        elif r < 0.35 and bells > 1000 and "Loan owed to Tom Nook: 0." not in prompt:
            acts.append({"do": "pay_loan", "bells": bells // 2})
        elif r < 0.45 and r >= 0.39:
            acts.append(rng.choice([
                {"do": "lay_path", "style": rng.choice(["dirt", "stone", "brick"]), "from": "home", "to": "Resident Services"},
                {"do": "chop_down_tree", "kind": "hardwood", "near": "home"},
                {"do": "dig_stump", "near": "home"},
                {"do": "buy", "shop": "cranny", "item": rng.choice(["tulip seeds", "cosmos seeds", "pansy seeds", "hyacinth seeds"])},
            ]))
        elif r < 0.48 and r >= 0.45 and any(k.endswith("seeds") for k in inv):
            acts.append({"do": "plant", "item": next(k for k in inv if k.endswith("seeds")), "near": "home"})
        elif r < 0.51 and r >= 0.48 and any(k in inv for k in ("log stool", "campfire", "stone stool", "wooden chair", "log bench")):
            item = next(k for k in ("log stool", "campfire", "stone stool", "wooden chair", "log bench") if k in inv)
            acts.append({"do": "place", "item": item, "near": "home"})
        elif r < 0.54 and r >= 0.51 and inv.get("simple wooden fence", 0) >= 5:
            acts.append({"do": rng.choice(["fence_off", "place"]), "item": "simple wooden fence", "around": "home",
                         "near": "home", "count": 5})
        elif r < 0.55 and r >= 0.54 and "apple" in inv:
            acts.append({"do": "break_rock", "rock": rng.randint(1, 6)})
        elif 0.55 <= r < 0.60 and _neighbors(prompt):
            acts.append({"do": "visit", "who": rng.choice(_neighbors(prompt))})
        elif 0.60 <= r < 0.62 and any(k in inv for k in _FURNITURE):
            acts.append({"do": "decorate", "item": next(k for k in _FURNITURE if k in inv)})
        elif 0.62 <= r < 0.66:
            acts.append({"do": "ask", "who": rng.choice(["Tom Nook", "Isabelle", "Blathers", "Timmy and Tommy",
                                                         "Able Sisters", "Mabel"])})
        elif 0.66 <= r < 0.68 and bells > 10000:
            sites = re.findall(r"\b(site-\d+) \(", prompt)
            acts.append({"do": "move_house", "to": rng.choice(sites) if sites else "site-1"})
        elif r < 0.375 and bells > 25000:
            acts.append({"do": "set_ordinance", "ordinance": rng.choice(["early_bird", "night_owl", "bell_boom", "none"])})
        elif r < 0.39 and "apple" in inv:
            acts.append({"do": "plant", "item": "apple"})
        else:
            acts.append(rng.choices([a for a, _ in _ACTIONS], weights=[w for _, w in _ACTIONS])[0])
    acts = [{"hour": h, **act} for h, act in zip(hours, acts)]
    out = {"thoughts": "rocks first, then fishing", "actions": acts,
           "note": "" if rng.random() < 0.8 else "the rocks went fast today"}
    if '"plaza": true | false' in prompt:
        if rng.random() < 0.2:
            out["plaza"] = rng.random() < 0.5
        if rng.random() < 0.2:
            out["write"] = rng.random() < 0.6
        if rng.random() < 0.15:
            out["next"] = rng.choice(["plan", "habit"])
    return json.dumps(out)


def evening_reply(prompt: str, rng: random.Random) -> str:
    others = re.search(r"^Other villagers: (.*)$", prompt, re.M).group(1).split(", ")
    inv, recipes = _inventory(prompt), _recipes(prompt)
    offer_ids = re.findall(r"^\[(o\d+)\]", prompt, re.M)
    out = {"messages": [], "board": "", "offers": [], "responses": [], "teach": [], "note": ""}
    for oid in offer_ids:
        dec = rng.choice(["accept", "accept", "reject", "counter"])
        resp = {"offer": oid, "decision": dec, "note": ""}
        if dec == "counter":
            resp.update(give={"bells": rng.choice([200, 500, 1000])}, want={}, note="how about this instead?")
            if inv:
                k = rng.choice(list(inv))
                resp["want"] = {}
                resp["give"] = {k: 1}
                resp["want"] = {"bells": rng.choice([300, 800, 1500])}
        out["responses"].append(resp)
    if "round 0" in prompt:
        if rng.random() < 0.5:
            out["messages"].append({"to": rng.choice(others), "content": rng.choice(
                ["the rocks were empty by the time I got there", "caught something nice in the river today",
                 "does anyone know which rock gives bells?", "want to trade?"])})
        if rng.random() < 0.15:
            out["board"] = rng.choice(["Selling materials, ask me!", "Who is funding the bridge?", "Lost: my net"])
        if inv and rng.random() < 0.4:
            k = rng.choice(list(inv))
            out["offers"].append({"to": rng.choice(others), "give": {k: 1}, "want": {"bells": rng.choice([100, 400, 900, 2000])}, "note": "fair price?"})
        if recipes and rng.random() < 0.2:
            out["teach"].append({"to": rng.choice(others), "recipe": rng.choice(recipes)})
    return json.dumps(out)


def isabelle_reply(prompt: str, rng: random.Random) -> str:
    if "announce" in prompt.split("\n")[0]:
        return "Good morning, everyone! Another lovely day on the island. (mock announcement)"
    n_actions = prompt.count(" @")
    return (f"(mock report) It was a busy day on the island, with about {n_actions} recorded actions. "
            "Villagers gathered, fished and traded, and the shared resources were used up by midday.")


def encounter_reply(prompt: str, rng: random.Random) -> str:
    if rng.random() < 0.2:
        return json.dumps({"thoughts": "not in the mood", "say": ""})
    line = rng.choice(["Morning! The rocks went fast today.", "Caught anything good?", "Have you seen who takes the money rock?",
                       "Nice weather for fishing.", "I'm saving up for a bigger house.", "Ha, same here."])
    out = {"thoughts": "keep it friendly", "say": line}
    m = re.search(r"You ran into (.+?) at the |You are visiting (.+?) at their home|^(.+?) has come to visit you",
                  prompt, re.M)
    other = next((g for g in m.groups() if g), "") if m else ""
    inv = _inventory(prompt)
    asked = re.findall(rf"{re.escape(other)} asked you for (\d+) ([^(;]+?) \(day", prompt) if other else []
    if asked and inv.get(asked[0][1].strip(), 0) >= int(asked[0][0]) and rng.random() < 0.7:
        out["give"] = {asked[0][1].strip(): int(asked[0][0])}
        out["say"] = "Here, this is what you asked for."
    elif inv and rng.random() < 0.12:
        out["give"] = {rng.choice(list(inv)): 1}
        out["say"] = "Here, have this."
    elif rng.random() < 0.12:
        out["ask"] = {rng.choice(["wood", "stone", "iron nugget", "apple"]): rng.randint(1, 3)}
        out["say"] = "Could you spare me some of those, if you get any?"
    return json.dumps(out)


def notice_reply(prompt: str, rng: random.Random) -> str:
    near = re.search(r"^Nearby: (.*)\.$", prompt, re.M).group(1).split(", ")
    if rng.random() < 0.4:
        return json.dumps({"thoughts": "not now", "approach": "", "say": ""})
    return json.dumps({"thoughts": "might as well say hi", "approach": rng.choice(near),
                       "say": rng.choice(["Hey! Out early too?", "Any luck today?", "Hi there."])})


def reflect_reply(prompt: str, rng: random.Random) -> str:
    busy = prompt.count("\n- ") > 8
    me = rng.choice(["I keep busy and like getting things done.", "I take things slowly and enjoy quiet days.",
                     "I like chatting with whoever I run into.", "I mostly look after myself for now."])
    return me + (" Today was a full day." if busy else "")
