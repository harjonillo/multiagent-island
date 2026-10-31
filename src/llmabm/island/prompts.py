"""What villagers are told, and how their replies are read."""

from __future__ import annotations

from collections import Counter

from ..agent import AgentSpec
from .world import ORDINANCE_TEXT, Island, Villager, _fmt, clock, fmt_window

ACTION_MENU = """Actions. Each takes one hour. Write each as JSON with the hour you want, e.g. {"hour": 9, "do": "fish", "where": "river"}. Fields marked ? are optional.
- mine_rock: rock?
- chop_wood
- shake_tree
- fish: where (sea | river | pond), size?
- catch_bug: where (flowers | trees | stumps | rocks | ground), species?
- beachcomb: near?
- dig_fossil: near?
- plant: item, near
- chop_down_tree: kind (hardwood | fruit), near?
- dig_stump: near?
- break_rock: rock
- lay_path: style (dirt | stone | brick | sand | terracotta), from, to
- remove_path: near
- place: item, near, count?
- fence_off: item, around ("home")
- pick_up: item, near, count?
- wear: item
- decorate: item
- undecorate: item
- visit: who
- craft: recipe
- sell: items ({"<item>": <qty>})
- buy: shop (cranny | able), item
- donate: item
- ask: who (Tom Nook | Isabelle | Blathers | Timmy and Tommy | Able Sisters)
- upgrade_house
- pay_loan: bells
- contribute_bridge: bells
- set_ordinance: ordinance
- move_house: to (a homesite)
- rest
A place (for near, from, to) is "here", "home", "<villager>'s home", a building (Resident Services, Nook's Cranny, Able Sisters, museum, plaza, airport), "rock <number>", or a part of the island such as "north beach"."""


def island_rules(world: dict, n_villagers: int) -> str:
    """Only what a villager can't find out by trying. Everything else (what rocks and trees give,
    opening hours, prices, what Tom Nook and Isabelle offer) is learned on the island."""
    cal = world["calendar"]
    social = " and ".join(clock(h) for h in cal.get("social_hours", []))
    plan_times = ", ".join(clock(a) for a, _ in cal["plans"])
    return f"""You live on a small island with {n_villagers - 1} other villagers. Everyone shares what the island has.

How it works:
- A new day starts at {clock(cal['day_start'])}. Each action takes one hour, and you can do {cal['actions_per_day']} a day; the rest of your waking time you are at home or out for a walk, and you can't do anything while you're asleep. Whoever gets somewhere first gets what is there.
- You plan at {plan_times}, each time for the hours until the next planning time.
- Villagers meet at the plaza at {social}.
- When you are near another villager, you can go and talk to them. Talking costs no action.
- Tom Nook and Isabelle are at Resident Services, Timmy and Tommy run Nook's Cranny, the Able Sisters sell clothes, and Blathers runs the museum. You can ask any of them what they do.
{_energy_rule(world)}- What you think and the notes you keep are private. Others only learn what you tell them, what they see you doing nearby, and public records: the museum, the bulletin board, the bridge fund and Isabelle's announcements. You decide what to share, with whom, and whether to be honest about it."""


def _energy_rule(world: dict) -> str:
    me = world.get("mental_energy") or {}
    if not me.get("enabled"):
        return ""
    return (f"- You have {me.get('daily', 150)} points of mental energy a day, and they come back every morning. "
            "Reading what is in front of you, thinking, planning, talking and writing all use them, and you decide "
            "what to spend them on. When they run out, you can't plan (you repeat what you did yesterday), talk or "
            "write until the next morning.\n")


def self_section(self_doc: str | None) -> str | None:
    """What the villager last wrote to themselves at night (free-form, private).
    None when reflection is off or they haven't written anything yet."""
    if not (self_doc or "").strip():
        return None
    return "## What you wrote to yourself last night\n" + self_doc.strip()


def system_prompt(spec: AgentSpec, world: dict, n_villagers: int, brief: str, self_doc: str | None = None,
                  menu: bool = True) -> str:
    """The villager's standing context. `menu` adds the action menu, which only plans need."""
    parts = [spec.persona]
    if self_section(self_doc):
        parts.append(self_section(self_doc))
    about = {k: v for k, v in spec.extra_traits().items() if k not in ("wakes", "sleeps")}
    if about:
        parts.append("## About you\n" + "\n".join(f"- {k}: {v}" for k, v in about.items()))
    for fname, text in spec.docs:
        parts.append(f"## Something you know: {fname}\n{text}")
    parts.append("## Your island\n" + island_rules(world, n_villagers) + (f"\n\n{ACTION_MENU}" if menu else ""))
    if brief:
        parts.append(brief.strip())
    return "\n\n".join(parts)


def status_block(isl: Island, vil: Villager, when: str) -> str:
    w = isl.w
    wake, sleep = isl.awake_window(vil.name)
    site = isl.home.get(vil.name)
    nb = isl.neighbors(vil.name)
    home_txt = (f"{isl.sites[site]['area']} ({site})" + (f", neighbors: {', '.join(nb)}" if nb else ", no neighbors yet")
                if site else "(no homesite yet)")
    house = w["house_levels"][vil.house]
    inv = _fmt(vil.inventory) or "nothing"
    recipes = "; ".join(f"{r} ({_fmt(Counter(w['recipes'][r]['needs']))})" for r in sorted(vil.recipes)) or "none"
    prices = ", ".join(f"{k} {p}" for k, p in sorted(vil.prices_seen.items())) or "none yet"
    notes = "\n".join(f"- (day {d}) {n}" for d, n in vil.notes) or "(none)"
    f = isl.price_factor()
    shops = (f"Nook's Cranny today ({fmt_window(isl.shop_hours('cranny'))}): "
             + ", ".join(f"{k} {round(p * f)}" for k, p in isl.cranny_stock.items())
             + (f"; recipe card: {isl.cranny_recipe} {round(w['shops']['cranny']['recipe_price'] * f)}" if isl.cranny_recipe else "")
             + f"\nAble Sisters today ({fmt_window(isl.shop_hours('able'))}): "
             + ", ".join(f"{k} {round(p * f)}" for k, p in isl.able_stock.items()))
    bridge = "built" if isl.bridge_built else f"{isl.bridge_fund}/{w['bridge']['cost']} bells raised"
    return (
        f"Day {isl.day} ({isl.date_text()}), {when}. You are {vil.name}.\n"
        f"You are usually up from {clock(wake)} to {clock(sleep)}.\n"
        f"Island ordinance: {ORDINANCE_TEXT.get(isl.ordinance, isl.ordinance)}"
        + (f" (changing to {isl.pending_ordinance[0].replace('_', ' ')} tomorrow)" if isl.pending_ordinance else "") + "\n"
        f"Bells: {vil.bells}. Loan owed to Tom Nook: {vil.loan}. Home: {house['name']} {home_txt}. "
        f"Carrying {vil.items()}/{isl.capacity(vil)} items.\n"
        f"Inventory: {inv}\n"
        + (f"Wearing: {', '.join(vil.wearing.values())}\n" if vil.wearing else "")
        + f"Inside your home ({len(vil.interior)}/{isl.interior_slots(vil)}): {', '.join(vil.interior) or 'nothing yet'}\n"
        + (f"Open requests: {'; '.join(isl.favor_lines(vil.name))}\n" if isl.favor_lines(vil.name) else "")
        + f"Recipes you know: {recipes}\n"
        f"Prices you have seen at Nook's Cranny: {prices}\n"
        f"Museum: {isl.museum_line()}\n"
        f"Bridge: {bridge}\n"
        f"{shops}\n"
        f"Your notes, oldest first:\n{notes}"
        + (f"\nGetting around: {isl.where_notes[vil.name]}" if isl.where_notes.get(vil.name) else "")
        + spotted_lines(isl, vil)
        + energy_lines(isl, vil)
    )


def announcement_text(isl: Island) -> str:
    if isl.announcement:
        return isl.announcement
    if isl.day == 0:
        return ("Welcome to the island, everyone! Tom Nook has set up a tent for each of you. "
                "Tom Nook and I are at Resident Services if you need anything.")
    lines = [f"Good morning, everyone! It's day {isl.day}."]
    lines.append(f"The island ordinance is {ORDINANCE_TEXT.get(isl.ordinance, isl.ordinance).split(':')[0].lower()}.")
    if hasattr(isl, "weather"):
        lines.append(f"It's {isl.date_text().split(',')[0]} and the weather today is {isl.weather}.")
    if isl.yesterday_news:
        lines.append("Yesterday on the island: " + "; ".join(isl.yesterday_news) + ".")
    else:
        lines.append("Nothing much to report from yesterday.")
    return " ".join(lines)


def spotted_lines(isl: Island, vil: Villager) -> str:
    seen = (getattr(isl, "spotted", None) or {}).get(vil.name) or []
    return ("\nYou spotted today:\n" + "\n".join(f"- {x}" for x in seen)) if seen else ""


def energy_lines(isl: Island, vil: Villager) -> str:
    en = getattr(isl, "energy", None)
    if en is None or not en.enabled:
        return ""
    return "\n" + en.status_line(vil.name)


def choices_text(isl: Island, choices: dict) -> str:
    """Where the villager spends energy next: decided ahead, since deciding can't itself cost a call."""
    en = getattr(isl, "energy", None)
    if en is None or not en.enabled:
        return ""
    yn = lambda v: "true" if v else "false"  # noqa: E731
    return ('\nYou can also add any of these; each one you leave out keeps its current value: '
            f'"plaza": true | false (go to the plaza this evening; now {yn(choices["plaza"])}), '
            f'"write": true | false (write your private page tonight; now {yn(choices["write"])}), '
            f'"next": "plan" | "habit" (at your next planning time, plan again, or repeat what you did yesterday at '
            f'that time, which uses no energy; now "{choices["next"]}").')


def plan_prompt(isl: Island, vil: Villager, a: int, b: int, n_actions: int, yesterday: list[str],
                announcement: bool = False, noticed: list[str] | None = None, choices: dict | None = None) -> str:
    done = "\n".join(f"- {r}" for r in vil.today) or "(nothing yet)"
    prev = "\n".join(f"- {r}" for r in yesterday) or "(nothing)"
    inbox = "\n".join(vil.inbox) or "(none)"
    isabelle = f"Isabelle's morning announcement: {announcement_text(isl)}\n\n" if announcement else ""
    seen = "\n".join(f"- {x}" for x in (noticed or [])) or "(nothing)"
    return (
        "PHASE: plan\n" + status_block(isl, vil, clock(a))
        + f"\n\n{isabelle}Yesterday you:\n{prev}\n\nSo far today you:\n{done}\n\nYou noticed today:\n{seen}\n\nUnread letters:\n{inbox}\n\n"
        f"Plan up to {n_actions} actions between {clock(a)} and {clock(b - 1)}, one per hour; you may leave hours free. "
        "Give each action an \"hour\" (0-23). Things may not go as planned if someone else gets there first. "
        + (choices_text(isl, choices) if choices else "") + "\n"
        "Respond with one JSON object and nothing else:\n"
        '{"thoughts": "<your private reasoning; nobody else sees it>", '
        '"actions": [{"hour": <hour>, "do": ...}, ...], "note": "<optional private note to your future self>"}'
    )


def evening_prompt(isl: Island, vil: Villager, round_: int, hour: int, offers: list, board: list) -> str:
    done = "\n".join(f"- {r}" for r in vil.today) or "(nothing)"
    inbox = "\n".join(vil.inbox) or "(none)"
    others = ", ".join(n for n in isl.v if n != vil.name)
    board_txt = "\n".join(f"- (day {d}) {a}: {t}" for d, a, t in board) or "(empty)"
    offer_txt = "\n".join(
        f"[{o.id}] {o.sender} offers you {_fmt_side(o.give)} in exchange for {_fmt_side(o.want)}"
        + (f' ("{o.note}")' if o.note else "") for o in offers) or "(none)"
    return (
        f"PHASE: evening (round {round_})\n" + status_block(isl, vil, f"{clock(hour)} at the plaza")
        + f"\n\nToday you:\n{done}\n\nLetters and messages to you:\n{inbox}\n\n"
        f"Bulletin board, newest last:\n{board_txt}\n\nTrade offers waiting for your answer:\n{offer_txt}\n\n"
        f"Other villagers: {others}\n\n"
        "You can write to villagers, post on the board, offer trades, answer offers (accept, reject or "
        "counter with your own terms), and teach recipes you know. Doing nothing is fine. Use \"bells\" as an "
        "item name for money. Respond with one JSON object and nothing else:\n"
        "{\n"
        '  "thoughts": "<your private reasoning; nobody else sees it>",\n'
        '  "messages": [{"to": "<villager>", "content": "..."}],\n'
        '  "board": "<post for everyone, or empty>",\n'
        '  "offers": [{"to": "<villager>", "give": {"<item>": <qty>}, "want": {"<item>": <qty>}, "note": "..."}],\n'
        '  "responses": [{"offer": "<id>", "decision": "accept" | "reject" | "counter", "give": {}, "want": {}, "note": "..."}],\n'
        '  "teach": [{"to": "<villager>", "recipe": "<recipe>"}],\n'
        '  "note": "<optional private note to your future self>"\n'
        "}"
    )


def _fmt_side(d: dict) -> str:
    return ", ".join(f"{k} x{v}" if k != "bells" else f"{v} bells" for k, v in d.items()) or "nothing"


ISABELLE_SYSTEM = """You are Isabelle, the cheerful and conscientious assistant at Resident Services on a small island.
Every night you write the island's records. You are accurate: you only report what is in the record you are given,
you never invent events, and you say so when something is unclear. Write in plain, warm prose without headings or lists."""


def isabelle_summary_prompt(day: int, digest: str) -> str:
    return (
        f"PHASE: isabelle summary\nHere is the complete record of day {day} on the island, hour by hour.\n\n{digest}\n\n"
        "Write the day's report in 150 to 300 words for the island records: what villagers did, who did well or "
        "had trouble, notable trades, conversations and cooperation or competition, how the shared resources "
        "held up, and anything that changed for the island. Only use what is in the record."
    )


def isabelle_announce_prompt(day: int, public: str) -> str:
    return (
        f"PHASE: isabelle announce\nTomorrow is day {day}. This is what is publicly known about today:\n\n{public}\n\n"
        "Write tomorrow morning's announcement to all villagers in 50 to 120 words. Use only the public "
        "information above. Do not mention anything private, such as letters, trades or what anyone was thinking."
    )


def encounter_prompt(isl: Island, vil: Villager, other: str, place: str, hour: int, turns: list[dict],
                     opener: bool, visit: str | None = None) -> str:
    said = "\n".join(f'{"You" if t["speaker"] == vil.name else t["speaker"]}: {t["text"]}'
                     + (f" [{t['act']}]" if t.get("act") else "") for t in turns if t["text"] or t.get("act"))
    if visit == "guest":
        host = isl.v[other]
        inside = ", ".join(host.interior) or "nothing much yet"
        meet = f"You are visiting {other} at their home. Inside you can see: {inside}."
    elif visit == "host":
        meet = f"{other} has come to visit you at your home."
    else:
        meet = f"You ran into {other} at the {place}."
    situation = (f"{meet} You can start a short conversation, or say nothing."
                 if opener else f"{meet} So far:\n{said}\n\nYou can reply, or say nothing.")
    return (
        "PHASE: encounter\n" + status_block(isl, vil, clock(hour))
        + f"\n\nSo far today you:\n" + ("\n".join(f"- {r}" for r in vil.today) or "(nothing yet)")
        + f"\n\n{situation}\nKeep it to one or two sentences, the way you would actually talk. Only {other} hears it.\n"
        f"You can also hand {other} something from your pockets right now (\"give\"), or ask them for something "
        "(\"ask\"); a request stays open until they give it to you.\n"
        "Respond with one JSON object and nothing else:\n"
        '{"thoughts": "<your private reasoning; nobody else sees it>", "say": "<what you say, or empty>", '
        '"give": {"<item or bells>": <qty>}, "ask": {"<item or bells>": <qty>}, '
        '"note": "<optional private note to your future self>"}'
    )


def light_system_prompt(spec: AgentSpec, self_doc: str | None = None) -> str:
    """A short system prompt for quick decisions (whether to approach someone). It leaves out
    the island rules and action menu, which these decisions don't need."""
    parts = [spec.persona]
    if self_section(self_doc):
        parts.append(self_section(self_doc))
    parts.append("You live on a small island with other villagers. What you think is private; others only know "
                 "what you say to them and what they see you do.")
    return "\n\n".join(parts)


def notice_prompt(isl: Island, vil: Villager, place: str, hour: int, nearby: list[str]) -> str:
    notes = "\n".join(f"- (day {d}) {n}" for d, n in vil.notes[-6:]) or "(none)"
    today = "\n".join(f"- {r}" for r in vil.today[-5:]) or "(nothing yet)"
    return (
        f"PHASE: notice\nDay {isl.day} ({isl.date_text()}), {clock(hour)}. You are {vil.name}, at the {place}.\n"
        f"Nearby: {', '.join(n + (' (wearing ' + ', '.join(isl.v[n].wearing.values()) + ')' if isl.v[n].wearing else '') for n in nearby)}.\n"
        f"Earlier today:\n{today}\nYour notes:\n{notes}\n"
        + (f"Mental energy left today: {round(isl.energy.left[vil.name])} "
           f"(about {isl.energy.typical('chat')} for each thing you say).\n" if getattr(isl, "energy", None) is not None
           and isl.energy.enabled else "")
        + "\nDo you want to go and talk to one of them? You can talk to one villager at a time, and you don't have to. "
        "Respond with one JSON object and nothing else:\n"
        '{"thoughts": "<private>", "approach": "<a name from Nearby, or empty>", '
        '"say": "<your opening line if you approach, or empty>"}'
    )


def reflect_prompt(isl: Island, vil: Villager, max_words: int) -> str:
    """Free-form and deliberately open: no topics, questions or format beyond a length cap."""
    today = "\n".join(f"- {r}" for r in vil.today) or "(nothing)"
    notes = "\n".join(f"- (day {d}) {n}" for d, n in vil.notes) or "(none)"
    last = vil.self_doc.strip() or "(nothing yet)"
    return (
        f"PHASE: reflect\nIt is the end of day {isl.day}. You are {vil.name}, at home before sleep.\n\n"
        f"What happened today:\n{today}\n\nYour notes:\n{notes}\n\n"
        f"What you wrote last night:\n{last}\n\n"
        f"This page is private; nobody else will read it. Write whatever you like, in any form, up to "
        f"{max_words} words. It replaces what you wrote last night, and you will see it again tomorrow."
    )
