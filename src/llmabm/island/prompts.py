"""What villagers are told, and how their replies are read."""

from __future__ import annotations

from collections import Counter

from ..agent import AgentSpec
from .world import ORDINANCE_TEXT, Island, Villager, _fmt, clock, fmt_window

ACTION_MENU = """Actions. Each takes one hour; add "hour": <0-23> to say when, e.g. {"hour": 7, "do": "mine_rock"}.
- {"do": "mine_rock", "rock": <number, optional>}   hit a rock with your shovel and take what it gives today
- {"do": "chop_wood"}                                take today's wood from one tree
- {"do": "shake_tree"}                               shake a fruit tree for fruit
- {"do": "fish", "where": "sea" | "river" | "pond", "size": "<shadow size you'd rather go for, optional>"}
- {"do": "catch_bug", "where": "flowers" | "trees" | "stumps" | "rocks" | "ground", "species": "<bug you'd rather go for, optional>"}
- {"do": "beachcomb", "near": "<place, optional>"}  collect shells; sometimes a message bottle
- {"do": "dig_fossil", "near": "<place, optional>"} dig up a fossil spot
- {"do": "plant", "item": "<fruit, sapling or flower seeds>", "near": "<place>"}
- {"do": "chop_down_tree", "kind": "hardwood" | "fruit", "near": "<place, optional>"}   leaves a stump
- {"do": "dig_stump", "near": "<place, optional>"}       clears a stump so the spot is free
- {"do": "break_rock", "rock": <number>}            eat a fruit first; the rock is gone and turns up elsewhere in a few days
- {"do": "lay_path", "style": "dirt" | "stone" | "brick" | "sand" | "terracotta", "from": "<place>", "to": "<place>"}   up to 30 tiles an hour; repeat to continue
- {"do": "remove_path", "near": "<place>"}           your own path tiles
- {"do": "place", "item": "<furniture or fence>", "near": "<place>", "count": <n>}   put things outside; they block their tiles
- {"do": "fence_off", "item": "<fence>", "around": "home"}   fence your home plot, leaving the door open
- {"do": "pick_up", "item": "<item>", "near": "<place>", "count": <n>}   take back things you put outside
- {"do": "wear", "item": "<clothing>"}               others see what you wear
- {"do": "decorate", "item": "<furniture>"}           put furniture inside your home; visitors see it
- {"do": "undecorate", "item": "<furniture>"}
- {"do": "visit", "who": "<villager>"}                go to their home; if they're in, you talk and see inside
- {"do": "craft", "recipe": "<recipe you know>"}
- {"do": "sell", "items": {"<item>": <qty>, ...}}   at Nook's Cranny
- {"do": "buy", "shop": "cranny" | "able", "item": "<item or recipe>"}
- {"do": "donate", "item": "<fish, bug or fossil>"} to the museum (Blathers), first of each kind only
- {"do": "upgrade_house"}                            Tom Nook builds it now; you take a loan for the cost
- {"do": "pay_loan", "bells": <amount>}
- {"do": "contribute_bridge", "bells": <amount>}
- {"do": "set_ordinance", "ordinance": "early_bird" | "night_owl" | "bell_boom" | "none"}   pay Isabelle; starts tomorrow
- {"do": "rest"}
A <place> is: "here", "home", "<villager>'s home", a building (Resident Services, Nook's Cranny, Able Sisters, museum, plaza, airport) or "rock <number>"."""


def island_rules(world: dict, n_villagers: int) -> str:
    isl = world["island"]
    levels = "; ".join(f"{h['name']} (storage {h['storage']}, costs {h['cost']})" for h in world["house_levels"])
    br = world["bridge"]
    cal, hrs = world["calendar"], world["hours"]
    social = " and ".join(clock(h) for h in cal.get("social_hours", []))
    plan_times = ", ".join(clock(a) for a, _ in cal["plans"])
    ords = world.get("ordinances", {})
    ord_lines = "; ".join(ORDINANCE_TEXT.get(k, k) for k in ords.get("types", {}) if k != "none")
    return f"""You live on a small island with {n_villagers - 1} other villagers. Everyone shares its resources.

How the island works:
- The island runs on a 24-hour clock, and a new day starts at {clock(cal['day_start'])}, when resources refill. Each action takes one hour, and you have energy for {cal['actions_per_day']} actions a day; the rest of your waking time you relax at home, and you can't do anything while you're asleep. When you do things matters: whoever gets somewhere first gets what is there.
- You plan your day in parts, at {plan_times}. Each time you plan the hours until the next planning time.
- Villagers meet at the plaza at {social} to talk, post on the bulletin board, trade and teach recipes.
- When you are near another villager, you can choose to go and talk to them, one on one. Chatting costs no action. Whoever you approach can answer however they like.
- Usual opening hours: Nook's Cranny {fmt_window(hrs['cranny'])}, Able Sisters {fmt_window(hrs['able'])}, Resident Services (Tom Nook and Isabelle) {fmt_window(hrs['resident_services'])}, museum {fmt_window(hrs['museum'])}.
- There are {isl['rocks']} rocks. Each gives up to {isl['rock_hits']} materials per day (stone, clay, iron nuggets, rarely gold). One rock each day gives bells instead; nobody knows which until someone hits it. Rocks refill every morning. Whoever gets to a rock first takes what it has.
- Trees give a few pieces of wood per day each. Fruit trees regrow fruit a few days after being shaken. Planted fruit or saplings grow into trees after {isl['sapling_mature_days']} days, on one of a limited number of open spots. Chopped-down trees are gone.
- The beach has a limited number of shells per day. There are {isl['fossils_per_day']} fossil spots per day, first come first served.
- Fish and bugs are luck. Different ones come out at different hours, in different months and in different weather, and bugs live in different places (flowers, trees, stumps, rocks, the ground). What is where and when is something you learn by trying, or by asking.
- The island has seasons and weather. Each day has its own weather.
- You carry {world['pockets']} items in your pockets; a bigger house adds storage. House sizes: {levels}.
- Tom Nook runs the island services. He upgrades your house right away on a loan with no interest and no deadline, but you must pay off a loan before the next upgrade.
- Timmy and Tommy run Nook's Cranny. They buy almost anything at a fixed price and sell a small stock each day, sometimes a DIY recipe. The Able Sisters sell clothes.
- Isabelle keeps the island's ordinance. Any villager can pay her {ords.get('cost', 0)} bells to set one for everyone, starting the next morning; only one is in effect at a time. Options: {ord_lines}. Every morning she makes an announcement with island news.
- Blathers runs the museum. Anyone can donate the first of each fish, bug or fossil species. Donated things stay in the museum for everyone; you get no bells for them.
- The {br['name']} costs {br['cost']} bells. Anyone can chip in. When it is fully funded it opens more rocks and trees to everyone.
- DIY recipes let you craft furniture from materials. You only know some recipes. Recipes can be taught to other villagers.
- Other villagers can trade with you in the evening. Prices between villagers are whatever you agree on.
{_energy_rule(world)}- What you think and the notes you keep are private. Other villagers only learn what you tell them, what they see you doing when they are in the same place (or what neighbors notice you heading out to), and public records: the museum, the bulletin board, the bridge fund and Isabelle's announcements. You decide what to share, with whom, and whether to be honest about it.

{ACTION_MENU}"""


def _energy_rule(world: dict) -> str:
    me = world.get("mental_energy") or {}
    if not me.get("enabled"):
        return ""
    return (f"- You have a limited amount of mental energy each day ({me.get('daily', 150)} points), which comes back "
            "every morning. Planning, private thinking and talking to others all use it, and so does reading what "
            "you need to know for each of them. You decide how to divide it between planning, thinking and talking. "
            "When one part runs out: without planning energy you fall back on habit (what you did yesterday at that "
            "time); without talking energy you can only nod at people and skip the plaza; without thinking energy "
            "you can't reflect or keep notes.\n")


def self_section(self_doc: str | None) -> str | None:
    """The villager's own, private description of themselves (from nightly reflection).
    None means reflection is off; an empty string means they haven't written one yet."""
    if self_doc is None:
        return None
    if not self_doc.strip():
        return "## Who you are, in your own words\n(You haven't formed a view of yourself yet.)"
    return "## Who you are, in your own words\n" + self_doc.strip()


def system_prompt(spec: AgentSpec, world: dict, n_villagers: int, goals: list[str] | None, brief: str,
                  self_doc: str | None = None, space_rules: str = "") -> str:
    parts = [spec.persona]
    if self_section(self_doc):
        parts.append(self_section(self_doc))
    about = {k: v for k, v in spec.extra_traits().items() if k not in ("wakes", "sleeps", "routine")}
    if about:
        parts.append("## About you\n" + "\n".join(f"- {k}: {v}" for k, v in about.items()))
    for fname, text in spec.docs:
        parts.append(f"## Something you know: {fname}\n{text}")
    parts.append("## Your island\n" + island_rules(world, n_villagers) + (f"\n{space_rules}" if space_rules else ""))
    if brief:
        parts.append(brief.strip())
    if goals:
        parts.append("## What you want\n" + "\n".join(f"- {g}" for g in goals))
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
        f"You are usually up from {clock(wake)} to {clock(sleep)}. {vil.routine}\n"
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
        return ("Welcome to the island, everyone! Pick a spot for your tent when you arrive. "
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
    out = "\n" + en.status_line(vil.name)
    if not en.has_thinking(vil.name):
        out += '\nYou have no thinking energy left today: leave "thoughts" and "note" empty.'
    return out


def energy_choice(isl: Island) -> str:
    en = getattr(isl, "energy", None)
    if en is None or not en.enabled:
        return ""
    return ('\nYou can also re-divide the mental energy you have left today by adding "energy": '
            '{"planning": <share>, "thinking": <share>, "talking": <share>} (any numbers; they are made to add up). '
            "The split you choose is also how tomorrow's energy is divided. Leave it out to keep your current split.")


def plan_prompt(isl: Island, vil: Villager, a: int, b: int, n_actions: int, yesterday: list[str],
                announcement: bool = False, noticed: list[str] | None = None) -> str:
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
        + energy_choice(isl) + "\n"
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


def arrival_prompt(isl: Island, vil: Villager, hour: int, awake: list[int], n_actions: int) -> str:
    free = isl.free_sites()
    sites = "\n".join(
        f"- {sid}: {isl.sites[sid]['area']}"
        + (f" (neighbors: {', '.join(n for n, h in isl.home.items() if h and isl.sites[h]['area'] == isl.sites[sid]['area'])})"
           if any(h and isl.sites[h]['area'] == isl.sites[sid]['area'] for h in isl.home.values()) else "")
        for sid in free)
    places = isl.space_desc or ", ".join(f"{p} {tuple(xy)}" for p, xy in isl.places.items())
    plan = (f"Then plan up to {n_actions} actions between {clock(awake[0])} and {clock(awake[-1])}, one per hour, "
            "giving each an \"hour\" (0-23); you may leave hours free."
            if awake else "There is no time left to do anything else before your next planning time.")
    return (
        "PHASE: arrival\n" + status_block(isl, vil, clock(hour))
        + f"\n\nIsabelle: {announcement_text(isl)}\n\n"
        f"You have just arrived on the island at {clock(hour)}. Choose where to pitch your tent. "
        "Your home stays there. Living close to a place means you get there before villagers who live "
        "farther away when you go at the same hour, and you will notice your neighbors coming and going.\n"
        f"Places on the island: {places}\n"
        f"Free homesites:\n{sites}\n\n"
        f"{plan}{energy_choice(isl)} Respond with one JSON object and nothing else:\n"
        '{"thoughts": "<your private reasoning; nobody else sees it>", "homesite": "<homesite id>", '
        '"actions": [{"hour": <hour>, "do": ...}, ...], "note": "<optional private note to your future self>"}'
    )


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


def light_system_prompt(spec: AgentSpec, goals: list[str] | None, self_doc: str | None = None) -> str:
    """A short system prompt for quick decisions (whether to approach someone). It leaves out
    the island rules and action menu, which these decisions don't need."""
    parts = [spec.persona]
    if self_section(self_doc):
        parts.append(self_section(self_doc))
    if goals:
        parts.append("What you want: " + " ".join(goals))
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
        + (f"Talking energy left today: {round(isl.energy.left[vil.name]['talking'])} "
           f"(about {isl.energy.typical('chat')} for each thing you say).\n" if getattr(isl, "energy", None) is not None
           and isl.energy.enabled else "")
        + "\nDo you want to go and talk to one of them? You can talk to one villager at a time, and you don't have to. "
        "Respond with one JSON object and nothing else:\n"
        '{"thoughts": "<private>", "approach": "<a name from Nearby, or empty>", '
        '"say": "<your opening line if you approach, or empty>"}'
    )


def reflect_prompt(isl: Island, vil: Villager, items: list[str], max_words: int) -> str:
    today = "\n".join(f"- {r}" for r in vil.today) or "(nothing)"
    notes = "\n".join(f"- (day {d}) {n}" for d, n in vil.notes) or "(none)"
    current = vil.self_doc.strip() or "(none yet)"
    scale = "\n".join(f"{i + 1}. {it}" for i, it in enumerate(items))
    return (
        f"PHASE: reflect\nIt is the end of day {isl.day}. You are {vil.name}, at home before sleep.\n"
        f"Bells: {vil.bells}. Loan: {vil.loan}. Carrying {vil.items()} items.\n\n"
        f"What you did and what happened today:\n{today}\n\nYour notes:\n{notes}\n\n"
        f"How you described yourself until now:\n{current}\n\n"
        f"Write a new description of yourself, in the first person and at most {max_words} words: what kind of "
        "person you are, what you care about, how you like to spend your days, and how you see the other "
        "villagers you know. Base it on what has actually happened to you; it's fine if it is still vague. "
        "Nobody else will read it.\n\n"
        "Then rate how true each statement is of you, from 1 (not at all) to 5 (very much):\n"
        f"{scale}\n\n"
        "Respond with one JSON object and nothing else:\n"
        '{"thoughts": "<private>", "self": "<your description>", "ratings": [<one number per statement, in order>]}'
    )
