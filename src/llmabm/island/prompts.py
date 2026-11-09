"""What villagers are told, and how their replies are read."""

from __future__ import annotations

from collections import Counter

from ..agent import AgentSpec
from .memory import memory_lines
from .world import CARD, LATEST_PROMPT_VERSION, MAP, ORDINANCE_TEXT, Island, Villager, _fmt, clock, fmt_window, tool_uses

# Prompt wording has versions so that a resumed run keeps the wording it started with, unless
# it is switched on purpose (`llmabm resume --prompt-version`), which is logged as an intervention.
PROMPT_VERSION = LATEST_PROMPT_VERSION
PROMPT_CHANGES = {
    1: "the original wording",
    2: "visit and ask: the action menu says who each is for, and visiting an NPC or a place, or asking a "
       "villager, says how to reach them instead",
    3: "a map: asking Tom Nook or Isabelle gets you an island map, and while you carry it, your plans show the "
       "island's layout area by area",
    4: "axes: everyone starts with a stone axe, which only gathers wood; the axe (iron; its recipe card is at "
       "Nook's Cranny) gathers wood and brings the tree down when it takes the tree's last wood. Fruit trees have "
       "wood too, and drop their fruit when felled. There is no separate chop_down_tree",
    5: "Isabelle's morning announcement: at most 70 words (yesterday's news is cut short with \"and N more\"), "
       "then one \"Did you know\" fun fact of up to 15 words about how the island works",
    6: "the plaza is a choice: nobody goes in the evening unless one of their plans that day says \"plaza\": true "
       "(before, everyone went unless they said otherwise)",
    7: "the plaza is open all day: a plan can spend any free hour there ({\"do\": \"plaza\"}, not one of the "
       "day's actions), and there is no evening round. Trade offers reach any villager and wait for their next "
       "visit, expiring after 2 days. Mental energy is one daily total spent inwardly (planning, thinking, "
       "writing) or outwardly (conversations, the plaza, letters); each morning's plan splits it, as a plan, not "
       "a limit, and whatever is left over is spare. A villager too tired to plan keeps what they already "
       "planned; nobody repeats yesterday, and there is no habit choice",
    8: "what you have, said plainly: your pockets as tools and other things, each recipe you know with how much "
       "of what it needs you have (and how many of the thing you carry), and recipe cards you already know marked "
       "in the shop. When you meet someone, what has changed hands between the two of you so far. At night: what "
       "changed hands today, what you have and what changed since the morning, and your open requests and offers. "
       "A gift that doesn't go through says what was missing",
    9: "conversations: no set number of lines. Each villager decides when to stop: \"leave\": true says their line "
       "and then they go their ways (saying nothing ends it too). Each turn says what a line costs in mental "
       "energy and how much is left, and says so when it is probably the last thing they have the energy to say",
    10: "trees: getting around lists the trees in the part of the island you're in (standing trees, stumps, "
        "saplings), next to the rocks and buildings. A tool that breaks says how many uses it lasted and that it "
        "is gone from your pockets",
    11: "clams: manila clam spots on the beach each morning, seen like shells and fossil dig spots, and dug with a "
        "shovel (dig_clam). The first clam teaches the fishing bait recipe: 1 clam makes 1 bait. A fun fact says so",
    12: "what you have and who you dealt with, kept apart and said plainly. Pockets and home are apart: only what is "
        "in your pockets (40 slots) goes with you; bells and things kept at home (things only with house storage; a "
        "tent has none) are reachable only there. A plan made at home can put things away or take them out, which "
        "takes no time, and crafting at home can use what is kept there. Tools say how many uses each has left. "
        "The shop lists things (with what each is), tools ready to use, and recipe cards (named \"<thing> recipe\") "
        "apart; tools are always for sale, and cards are cheaper. Learning a recipe says what it needs. Requests "
        "say who wants what from whom, and lapse after 3 days. In a conversation each tag says who handed what to whom from your side (\"Ike handed you 1 wood\"), "
        "give and ask start empty, and you see the last time you talked with them before today. Trades happen in "
        "person only: a turn can offer a trade (\"trade\": give and want), and the other's next turn can accept it, "
        "when both sides change hands at once or nothing does. Any trade or gift that covers a request closes it. "
        "The plaza has no trade offers anymore; board posts have numbers and their authors can take them down",
    13: "trades in a conversation: at most 3 trade offers, from either side together. Each turn says how many are "
        "left; after the third, \"trade\" is gone from the reply and the last one on the table can still be accepted. "
        "The trade on the table, and each offer's tag in the conversation, is spelled out as what you'd give and "
        "what you'd get, with how many of what you'd give you have. \"accept\" is offered only if your pockets can hold up your side (enough of what you'd "
        "give, and room for what you'd get), and the same goes for offering one: an offer your pockets can't hold "
        "up isn't put on the table, and both of you are told why. Shorter prompts: no ordinance or loan line when "
        "there is none, shorter lines for home, requests, recipes and prices, and a shorter energy rule. A "
        "conversation leaves out what only plans use (rocks, buildings and trees with their distances, what you "
        "spotted, the museum, the bridge, what shop goods are, the energy plan) and lists your pockets once, and "
        "its instructions are shorter. The action menu says what fishing bait, visit and ask are for, and what "
        "isn't an action (talking, giving and trading; the board, letters and parcels; walking), and what each tool "
        "is for",
}

ACTION_MENU = """Actions. Each takes one hour. Write each as JSON with the hour you want, e.g. {"hour": 9, "do": "fish", "where": "river"}. Fields marked ? are optional.
- mine_rock: rock?
- chop_wood
- shake_tree
- fish: where (sea | river | pond), size?, bait? (true to use fishing bait)
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
- buy: shop (cranny | able), item (or "<recipe> recipe" for a recipe card)
- donate: item
- assess (Blathers identifies every fossil you carry)
- ask: who (Tom Nook | Isabelle | Blathers | Timmy and Tommy | Able Sisters)
- upgrade_house
- pay_loan: bells
- contribute_bridge: bells
- set_ordinance: ordinance
- move_house: to (a homesite)
- rest
A place (for near, from, to) is "here", "home", "<villager>'s home", a building (Resident Services, Nook's Cranny, Able Sisters, museum, plaza, airport), "rock <number>", or a part of the island such as "north beach"."""


_MENU_CHANGES = {
    2: [("- visit: who\n", "- visit: who (another villager: walk to their home, knock, and talk if they're in)\n"),
        ("- ask: who (Tom Nook | Isabelle | Blathers | Timmy and Tommy | Able Sisters)\n",
         "- ask: who (Tom Nook | Isabelle | Blathers | Timmy and Tommy | Able Sisters: go to them and hear what "
         "they do and what it costs)\n")],
    4: [("- chop_wood\n", "- chop_wood: kind? (hardwood | fruit), axe? (stone axe | axe; if you carry both, the stone axe. "
                        "An axe brings the tree down when it takes its last wood, and a fruit tree drops its fruit; a "
                        "stone axe never fells)\n"),
        ("- chop_down_tree: kind (hardwood | fruit), near?\n", "")],
    7: [("- rest\n", "- rest\n- plaza (not one of your actions: you spend that free hour at the plaza)\n")],
    11: [("- dig_fossil: near?\n", "- dig_fossil: near?\n- dig_clam: near? (a clam spot on the beach)\n")],
    12: [("- buy: shop (cranny | able), item (or \"<recipe> recipe\" for a recipe card)\n",
          "- buy: shop (cranny | able), item (as the shop lists it: a thing, a tool, or \"<thing> recipe\" for a "
          "recipe card)\n")],
    13: [("bait? (true to use fishing bait)", "bait? (true uses 1 fishing bait from your pockets; if you have none, leave it out)"),
         ("- visit: who (another villager: walk to their home, knock, and talk if they're in)\n",
          "- visit: who (a villager: walk to their home, knock, and talk if they're in. Shops and services are "
          "reached by their own actions, such as buy, sell, donate or ask)\n"),
         ("Able Sisters: go to them and hear what they do and what it costs)\n",
          "Able Sisters only, not villagers: go to them and hear what they do and what it costs)\n"),
         ("or a part of the island such as \"north beach\".",
          "or a part of the island such as \"north beach\".\nNot actions: talking, giving and trading happen when "
          "you meet someone (visit them to meet on purpose); the board, letters and parcels are at the plaza; and "
          "you walk wherever an action takes you, so there is no walk or travel.\n"
          "Tools: shovel (mine_rock, dig_fossil, dig_clam, dig_stump, break_rock); axe or stone axe (chop_wood); "
          "fishing rod (fish); net (catch_bug); ladder (climbs one cliff up or down); vaulting pole (jumps across "
          "the river). You use a ladder or vaulting pole you carry whenever your way needs it.")],
}


def action_menu(version: int = PROMPT_VERSION) -> str:
    menu = ACTION_MENU
    for v in sorted(_MENU_CHANGES):
        if v <= version:
            for old, new in _MENU_CHANGES[v]:
                menu = menu.replace(old, new)
    return menu


def _pockets_rule(version: int) -> str:
    if version < 12:
        return ""
    return ("- Only what is in your pockets goes with you: when you're out, that is all you can use, give, sell or "
            "trade. Bells and things you keep at home stay there (things only if your home has storage). When you "
            "plan at home you can put things away or take them out, which takes no time, and crafting at home can "
            "use what you keep there.\n")


def island_rules(world: dict, n_villagers: int, version: int = PROMPT_VERSION) -> str:
    """Only what a villager can't find out by trying. Everything else (what rocks and trees give,
    opening hours, prices, what Tom Nook and Isabelle offer) is learned on the island."""
    cal = world["calendar"]
    social = " and ".join(clock(h) for h in cal.get("social_hours", []))
    if "plans" in cal:  # fixed planning times, the same for everyone (older runs)
        planning = f"You plan at {', '.join(clock(a) for a, _ in cal['plans'])}, each time for the hours until the next planning time."
    else:
        k = int(cal.get("plans_per_day", 2))
        times = {1: "once", 2: "twice"}.get(k, f"{k} times")
        planning = (f"You plan {times} a day: when you get up" + (", and again at even intervals through your waking hours" if k > 2 else
                    ", and again halfway through your waking hours" if k == 2 else "")
                    + ". Each plan covers the hours until your next one"
                    + (", and can use an even share of the actions you have left; whatever it doesn't use carries over." if k > 1 else "."))
    return f"""You live on a small island with {n_villagers - 1} other villagers. Everyone shares what the island has.

How it works:
- {_day_rule(cal, version)}
- {planning}
- {_plaza_rule(social, version)}
{_pockets_rule(version)}- When you are near another villager, you can go and talk to them. Talking costs no action.
- Tom Nook and Isabelle are at Resident Services, Timmy and Tommy run Nook's Cranny, the Able Sisters sell clothes, and Blathers runs the museum. You can ask any of them what they do.
{_energy_rule(world, version)}- What you think and the notes you keep are private. Others only learn what you tell them, what they see you doing nearby, and public records: the museum, the bulletin board, the bridge fund and Isabelle's announcements. You decide what to share, with whom, and whether to be honest about it."""


def _day_rule(cal: dict, version: int) -> str:
    start, n = clock(cal["day_start"]), cal["actions_per_day"]
    if version < 7:
        return (f"A new day starts at {start}. Each action takes one hour, and you can do {n} a day; the rest of your "
                "waking time you are at home or out for a walk, and you can't do anything while you're asleep. "
                "Whoever gets somewhere first gets what is there.")
    return (f"A new day starts at {start}. Each action takes one hour, and you can do {n} a day. The rest of your "
            "waking time is free: you can be at home, out for a walk, or at the plaza. You can't do anything while "
            "you're asleep. Whoever gets somewhere first gets what is there.")


def _plaza_rule(social: str, version: int) -> str:
    if version < 6:
        return f"Villagers meet at the plaza at {social}."
    if version >= 12:
        return ("The plaza is open all day. Going there isn't an action, but it takes that hour. There you can read "
                "the bulletin board, post on it and take your posts down, and send letters and parcels, which "
                "arrive the next morning. Trades happen in person, when you meet someone.")
    if version >= 7:
        return ("The plaza is open all day. Going there isn't an action, but it takes that hour. There you can read "
                "the bulletin board and post on it, offer and answer trades, and send letters and parcels, which "
                "arrive the next morning.")
    return (f"The plaza is open in the evening at {social}: go if you want to talk, trade, read the bulletin board "
            "or send letters and parcels. You only go on a day one of your plans says so (\"plaza\": true).")


def _energy_rule(world: dict, version: int = PROMPT_VERSION) -> str:
    me = world.get("mental_energy") or {}
    if not me.get("enabled"):
        return ""
    daily = me.get("daily", 150)
    if version >= 13:
        return (f"- You have {daily} points of mental energy a day, back each morning, spent inwardly (planning, "
                "thinking, your private page at night"
                + (", thinking before you answer" if me.get("charge_reasoning") else "")
                + ") or outwardly (conversations, the plaza, letters and posts). Each morning you split them; the "
                "split is a plan, not a limit, and what's left over is spare. With none left you can't plan, talk "
                "or write until morning, and you carry on with your plan.\n")
    if version >= 7:
        return (f"- You have {daily} points of mental energy a day, and they come back every morning. You can spend "
                "them inwardly (planning, thinking things over, writing your private page at night"
                + (", and thinking before you answer" if me.get("charge_reasoning") else "")
                + ") or outwardly (conversations, time at the plaza, letters and posts). Each morning you decide how "
                "much to spend inwardly and how much outwardly; whatever you leave over is spare, for people you run "
                f"into. The split is your own plan, not a limit, but when all {daily} are gone you can't plan, talk or "
                "write until the next morning: you carry on with what you've already planned, and the rest of your "
                "time is free.\n")
    return (f"- You have {daily} points of mental energy a day, and they come back every morning. "
            "Reading what is in front of you, thinking, planning, talking and writing all use them, and you decide "
            "what to spend them on"
            + (", and so does thinking things over before you answer" if me.get("charge_reasoning") else "")
            + ". When they run out, you can't plan (you repeat what you did yesterday), talk or "
            "write until the next morning.\n")


def self_section(self_doc: str | None) -> str | None:
    """What the villager last wrote to themselves at night (free-form, private).
    None when reflection is off or they haven't written anything yet."""
    if not (self_doc or "").strip():
        return None
    return "## What you wrote to yourself last night\n" + self_doc.strip()


def system_prompt(spec: AgentSpec, world: dict, n_villagers: int, brief: str, self_doc: str | None = None,
                  menu: bool = True, version: int = PROMPT_VERSION) -> str:
    """The villager's standing context. `menu` adds the action menu, which only plans need."""
    parts = [spec.persona]
    if self_section(self_doc):
        parts.append(self_section(self_doc))
    about = {k: v for k, v in spec.extra_traits().items() if k not in ("wakes", "sleeps")}
    if about:
        parts.append("## About you\n" + "\n".join(f"- {k}: {v}" for k, v in about.items()))
    for fname, text in spec.docs:
        parts.append(f"## Something you know: {fname}\n{text}")
    parts.append("## Your island\n" + island_rules(world, n_villagers, version) + (f"\n\n{action_menu(version)}" if menu else ""))
    if brief:
        parts.append(brief.strip())
    return "\n\n".join(parts)


def _v(isl: Island) -> int:
    return getattr(isl, "prompt_version", 1)


def _count(d: dict) -> str:
    """{"wood": 3, "bells": 200} -> "3 wood, 200 bells"."""
    return ", ".join(f"{n} {k}" for k, n in d.items()) or "nothing"


def pockets_text(isl: Island, vil: Villager) -> str:
    """Prompt version 8: tools apart from everything else, so what you already carry is easy to see. From
    version 12 each tool says the uses it has left, and what is kept at home follows."""
    v12 = _v(isl) >= 12
    carried = {k: n for k, n in vil.inventory.items() if n > 0}
    tools = [k for k in carried if isl.is_tool(k)]
    other = [k for k in carried if k not in tools]
    tool_list = ", ".join(k + (f" x{carried[k]}" if carried[k] > 1 else "")
                          + (f" ({_uses_left(isl, vil, k)})" if v12 else "") for k in tools)
    return (f"Tools you carry: {tool_list or 'none'}\n"
            f"Other things you carry: {', '.join(f'{k} x{carried[k]}' for k in other) or 'nothing'}"
            + (f"\n{home_text(isl, vil)}" if v12 else ""))


def _uses_left(isl: Island, vil: Villager, tool: str) -> str:
    """Prompt version 12: how many uses each tool of a kind has left, the one in use first."""
    left = [tool_uses(isl) - u for u in isl._wear(vil, tool)]
    return f"{left[0]} uses left" if len(left) == 1 else "uses left: " + ", ".join(str(n) for n in left)


def home_text(isl: Island, vil: Villager) -> str:
    """Prompt version 12: what a villager keeps at home, reachable only there."""
    cap = isl.home_capacity(vil)
    kept = _fmt(Counter(dict(sorted(vil.home_items.items()))))
    if _v(isl) >= 13:  # the rules already say what is at home is only reachable there
        room = (f" ({vil.home_count()}/{cap} slots)" if cap else
                f" (a {isl.w['house_levels'][vil.house]['name']} holds only bells)")
        return f"At home{room}: {vil.home_bells} bells" + (f", {kept}" if kept else "")
    room = (f"{vil.home_count()}/{cap} slots" if cap else
            f"your {isl.w['house_levels'][vil.house]['name']} has no room for things, only bells")
    return (f"Kept at home ({room}; only reachable at home): {vil.home_bells} bells"
            + (f", {kept}" if kept else ""))


def request_lines(isl: Island, name: str) -> list[str]:
    """Prompt version 12: open requests, saying who wants what from whom, and until when."""
    out = []
    for f in isl.favors:
        if f["status"] != "open":
            continue
        want = _count(f["items"])
        last = f["day"] + REQUEST_DAYS - 1
        when = f"until day {last}" if _v(isl) >= 13 else f"asked day {f['day']}; open until day {last}"
        if f["asked"] == name:
            out.append(f"{f['asker']} wants {want} from you ({when})")
        elif f["asker"] == name:
            out.append(f"you want {want} from {f['asked']} ({when})")
    return out


def recipe_text(isl: Island, vil: Villager, r: str) -> str:
    """Prompt version 8: a recipe with how much of what it needs you have, and how many of the thing you carry.
    From version 12, what you keep at home is counted apart."""
    needs = Counter(isl.w["recipes"][r]["needs"])
    have = ", ".join(f"{k} {vil.inventory[k]}" for k in needs if vil.inventory[k] > 0)
    if _v(isl) >= 13:  # "wood x3; 7 in pockets", or with more than one thing "in pockets: clay 2, stone 1"
        def where(stock: Counter, place: str) -> str:
            got = {k: stock[k] for k in needs if stock[k] > 0}
            if not got:
                return ""
            return f"{next(iter(got.values()))} {place}" if len(needs) == 1 else f"{place}: {_fmt_have(got)}"
        have = "; ".join(x for x in (where(vil.inventory, "in pockets"), where(vil.home_items, "at home")) if x)
        out = f"{r} ({_fmt(needs)}; " + (have or "you have none")
        return out + (f"; you carry {vil.inventory[r]} {r}" if vil.inventory[r] > 0 else "") + ")"
    if _v(isl) >= 12:
        home = ", ".join(f"{k} {vil.home_items[k]}" for k in needs if vil.home_items[k] > 0)
        have = "; ".join(x for x in ((f"in your pockets {have}" if have else ""),
                                     (f"at home {home}" if home else "")) if x)
    out = f"{r} ({_fmt(needs)}; " + (f"you have {have}" if have else "you have none of these")
    return out + (f"; you carry {vil.inventory[r]} {r}" if vil.inventory[r] > 0 else "") + ")"


def _fmt_have(d: dict) -> str:
    return ", ".join(f"{k} {n}" for k, n in d.items())


def _what_it_is(isl: Island, item: str) -> str:
    """Prompt version 12: a few words on what a shop good is."""
    info = (isl.w.get("items") or {}).get(item, {})
    kind = info.get("kind")
    if kind == "seeds":
        return f"flower seeds: plant them and {info.get('grows', 'flowers')} grow"
    if kind == "sapling":
        return f"a young tree: plant it and it's a hardwood tree in {isl.w['island']['sapling_mature_days']} days"
    if kind == "bait":
        return "fish with it and a fish is sure to appear"
    if kind == "fence":
        return "fencing, one piece per tile"
    if kind == "furniture":
        return "furniture" + ("" if info.get("outdoor", True) else ", indoors only")
    return ""


def shop_text(isl: Island, vil: Villager, talk: bool = False) -> str:
    """Prompt version 12: what the shops sell today, each kind of thing said plainly. Recipe cards are named
    "<thing> recipe", so a card is never mistaken for the thing; what a recipe needs is learned with it. From
    version 13 a conversation (`talk`) gets names and prices only."""
    f = isl.price_factor()
    goods = ", ".join(f"{k} {round(p * f)}" + (f" ({what})" if not talk and (what := _what_it_is(isl, k)) else "")
                      for k, p in isl.cranny_stock.items())
    out = [f"Nook's Cranny today ({fmt_window(isl.shop_hours('cranny'))}), prices in bells:",
           f"- things: {goods or 'nothing'}"]
    tools = isl.tools_for_sale()
    if tools:
        out.append(f"- tools, ready to use ({isl.w['tools']['uses']} uses each): "
                   + ", ".join(f"{k} {p}" for k, p in tools.items()))
    cards = isl.recipe_cards_for_sale()
    out.append(("- recipe cards: " if talk else
                "- recipe cards (a card teaches you to craft the thing yourself, as often as you like; buy one as "
                "\"<thing> recipe\"): ")
               + ", ".join(f"{r}{CARD} {p}" + (" (you know it)" if r in vil.recipes else "") for r, p in cards.items()))
    out.append(f"Able Sisters today ({fmt_window(isl.shop_hours('able'))}), clothes: "
               + ", ".join(f"{k} {round(p * f)}" for k, p in isl.able_stock.items()))
    return "\n".join(out)


def status_block(isl: Island, vil: Villager, when: str, talk: bool = False) -> str:
    """What a villager has and knows, at the top of every prompt but the light ones. From prompt version 13 a
    conversation (`talk`) leaves out what only plans use and lists the pockets once, next to the reply."""
    w = isl.w
    wake, sleep = isl.awake_window(vil.name)
    site = isl.home.get(vil.name)
    nb = isl.neighbors(vil.name)
    home_txt = (f"{isl.sites[site]['area']} ({site})" + (f", neighbors: {', '.join(nb)}" if nb else ", no neighbors yet")
                if site else "(no homesite yet)")
    house = w["house_levels"][vil.house]
    v8 = _v(isl) >= 8
    v12 = _v(isl) >= 12
    v13 = _v(isl) >= 13
    talk = talk and v13
    inv = pockets_text(isl, vil) if v8 else "Inventory: " + (_fmt(vil.inventory) or "nothing")
    if talk:
        inv = home_text(isl, vil)
    recipes = "; ".join(recipe_text(isl, vil, r) if v8 else f"{r} ({_fmt(Counter(w['recipes'][r]['needs']))})"
                        for r in sorted(vil.recipes)) or "none"
    known = lambda r: " (you know it)" if v8 and r in vil.recipes else ""  # noqa: E731
    prices = ", ".join(f"{k} {p}" for k, p in sorted(vil.prices_seen.items())) or "none yet"
    f = isl.price_factor()
    shops = shop_text(isl, vil, talk) if v12 else (f"Nook's Cranny today ({fmt_window(isl.shop_hours('cranny'))}): "
             + ", ".join(f"{k} {round(p * f)}" for k, p in isl.cranny_stock.items())
             + "; recipe cards: " + ", ".join(f"{r} {p}{known(r)}" for r, p in isl.recipe_cards_for_sale().items())
             + f"\nAble Sisters today ({fmt_window(isl.shop_hours('able'))}): "
             + ", ".join(f"{k} {round(p * f)}" for k, p in isl.able_stock.items()))
    bridge = "built" if isl.bridge_built else f"{isl.bridge_fund}/{w['bridge']['cost']} bells raised"
    requests = request_lines(isl, vil.name) if v12 else isl.favor_lines(vil.name)
    wallet = " in your wallet" if v12 else ""
    ordinance = (f"Island ordinance: {ORDINANCE_TEXT.get(isl.ordinance, isl.ordinance)}"
                 + (f" (changing to {isl.pending_ordinance[0].replace('_', ' ')} tomorrow)"
                    if isl.pending_ordinance else "") + "\n")
    if v13 and isl.ordinance in (None, "none") and not isl.pending_ordinance:
        ordinance = ""
    getting = isl.where_notes.get(vil.name) or ""
    if talk:  # where you are and what you carry for it, not the rocks, buildings and trees around
        getting = getting.split("\n")[0]
    return (
        f"Day {isl.day} ({isl.date_text()}), {when}. You are {vil.name}.\n"
        f"You are usually up from {clock(wake)} to {clock(sleep)}.\n"
        + ordinance
        + f"Bells: {vil.bells}{wallet}. "
        + (f"Loan owed to Tom Nook: {vil.loan}. " if not v13 or vil.loan else "")
        + f"Home: {house['name']} {home_txt}. "
        f"Carrying {vil.items()}/{isl.capacity(vil)} items.\n"
        f"{inv}\n"
        + (f"Wearing: {', '.join(vil.wearing.values())}\n" if vil.wearing else "")
        + f"Inside your home ({len(vil.interior)}/{isl.interior_slots(vil)}): {', '.join(vil.interior) or 'nothing yet'}\n"
        + (f"Open requests: {'; '.join(requests)}\n" if requests else "")
        + f"Recipes you know: {recipes}\n"
        + (f"Nook's Cranny buys at (prices you've heard): {prices}\n" if v13 else
           f"What Nook's Cranny pays when you sell it there (prices you've heard): {prices}\n" if v12 else
           f"Prices you have seen at Nook's Cranny: {prices}\n")
        + ("" if talk else f"Museum: {isl.museum_line()}\nBridge: {bridge}\n")
        + f"{shops}\n"
        + memory_block(isl, vil)
        + (f"\nGetting around: {getting}" if getting else "")
        + (f"\nYour island map:\n{isl.map_text}" if getattr(isl, "prompt_version", 1) >= 3 and vil.inventory[MAP] > 0
           and getattr(isl, "map_text", "") else "")
        + ("" if talk else spotted_lines(isl, vil) + energy_lines(isl, vil))
    )


ANNOUNCE_WORDS = 70  # from prompt version 5: the announcement itself...
FACT_WORDS = 15      # ...then one fun fact


def _a_word(thing: str) -> str:
    return ("an " if thing[:1].lower() in "aeiou" else "a ") + thing


def fun_facts(isl: Island) -> list[str]:
    """True things about how this island works, one per morning announcement from prompt version 5, each at
    most FACT_WORDS words. Built from the run's own world (its fruit, prices, days and tools), so a fact is never
    wrong for the island it is told on; facts about later prompt versions' rules appear only under them."""
    w, v = isl.w, getattr(isl, "prompt_version", 1)
    fruit, isl_cfg = w["island"]["native_fruit"], w["island"]
    tools, houses = w.get("tools") or {}, w["house_levels"]
    facts = [
        f"Did you know Nook's Cranny always sells saplings? Planted, they're hardwood trees in {isl_cfg['sapling_mature_days']} days.",
        f"Did you know eating {_a_word(fruit)} first lets your shovel break a rock?",
        "Did you know one rock a day pays bells instead of stone?",
        f"Did you know you can plant {_a_word(fruit)}, and it grows into a fruit tree?",
        "Did you know fishing bait makes a fish shadow appear right away?",
        "Did you know beachcombing sometimes turns up a message bottle with a recipe card?",
        "Did you know Blathers has to assess a fossil before the museum takes it?",
        f"Did you know a shaken fruit tree grows its fruit back in {isl_cfg['fruit_regrow_days']} days?",
        "Did you know Timmy and Tommy buy almost anything at a fixed price?",
        "Did you know letters and parcels sent at the plaza arrive the next morning?",
        "Did you know digging up a stump with your shovel clears the spot again?",
        "Did you know funding the bridge north opens more rocks and trees to everyone?",
        f"Did you know anyone can pay Isabelle {w['ordinances']['cost']:,} bells to set an island ordinance?",
    ]
    if tools.get("uses"):
        cranny = "always sells new ones" if w["shops"]["cranny"].get("tool_markup") else "always has their recipes"
        facts.append(f"Did you know tools break after {tools['uses']} uses, and Nook's Cranny {cranny}?")
    if len(houses) > 1:
        facts.append(f"Did you know a house from Tom Nook adds {houses[1]['storage']} storage to your {w['pockets']} pockets?"
                     if v < 12 else
                     f"Did you know a house from Tom Nook gives you room to keep {houses[1]['storage']} things at home?")
    if getattr(isl, "map_text", None):
        facts.append("Did you know a ladder climbs one cliff and a vaulting pole crosses the river?")
    if v >= 3:
        facts.append("Did you know asking Tom Nook or Isabelle gets you an island map?")
    if v >= 4:
        facts.append("Did you know a stone axe only gathers wood, but an iron axe fells trees?"
                     if "stone axe" in (w.get("items") or {}) else
                     "Did you know taking a tree's last wood with an axe brings it down?")
    if v >= 11 and int(isl_cfg.get("clams_per_day", 0)) > 0:
        facts.append("Did you know digging up a manila clam teaches you to make fishing bait?")
    if getattr(getattr(isl, "energy", None), "enabled", False):
        facts.append("Did you know your mental energy comes back in full every morning?")
    return facts


def fun_fact(isl: Island) -> str:
    """Today's fun fact: the facts in turn, the same one on the same day in every run with the same rules."""
    facts = fun_facts(isl)
    return facts[isl.day % len(facts)]


def _cap_words(text: str, limit: int) -> str:
    """Whole sentences up to `limit` words; a first sentence longer than that is cut with an ellipsis."""
    import re

    out, n = [], 0
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        k = len(sentence.split())
        if n + k > limit:
            if not out:
                out.append(" ".join(sentence.split()[:limit]) + "…")
            break
        out.append(sentence)
        n += k
    return " ".join(out)


def _news_within(head: str, news: list[str], limit: int) -> str:
    """`head` and as much of yesterday's news as fits in `limit` words, ending with "and N more" if cut."""
    if not news:
        return f"{head} Nothing much to report from yesterday."
    for k in range(len(news), -1, -1):
        rest = len(news) - k
        if k == 0:
            tail = f"Yesterday, {rest} things happened on the island."
        else:
            tail = "Yesterday on the island: " + "; ".join(news[:k]) + (f"; and {rest} more." if rest else ".")
        text = f"{head} {tail}"
        if len(text.split()) <= limit:
            return text
    return _cap_words(head, limit)


def announcement_text(isl: Island) -> str:
    """Isabelle's morning announcement, as the first plan of each villager's day shows it. From prompt version 5
    it is at most ANNOUNCE_WORDS words, then one fun fact (FACT_WORDS words at most)."""
    if getattr(isl, "prompt_version", 1) < 5:
        return _announcement_v1(isl)
    if isl.announcement:
        body = _cap_words(isl.announcement, ANNOUNCE_WORDS)
    elif isl.day == 0:
        body = _announcement_v1(isl)
    else:
        head = [f"Good morning, everyone! It's day {isl.day}.",
                f"The island ordinance is {ORDINANCE_TEXT.get(isl.ordinance, isl.ordinance).split(':')[0].lower()}."]
        if hasattr(isl, "weather"):
            head.append(f"It's {isl.date_text().split(',')[0]} and the weather today is {isl.weather}.")
        body = _news_within(" ".join(head), list(isl.yesterday_news), ANNOUNCE_WORDS)
    return f"{body} {fun_fact(isl)}"


def _announcement_v1(isl: Island) -> str:
    """The announcement before prompt version 5: no word limit, no fun fact."""
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
    return "\n" + en.status_line(vil.name, v7=getattr(isl, "prompt_version", 1) >= 7)


def choices_text(isl: Island, choices: dict) -> str:
    """Choices made ahead in a plan: where the villager spends energy next (deciding can't itself cost a call),
    and from prompt version 6, whether they go to the plaza this evening, with or without mental energy."""
    en = getattr(isl, "energy", None)
    energy = en is not None and en.enabled
    v = getattr(isl, "prompt_version", 1)
    plaza_choice = v >= 6
    if not (energy or plaza_choice):
        return ""
    yn = lambda v: "true" if v else "false"  # noqa: E731
    if v >= 7:  # the plaza is in the plan itself, and there is no habit
        return (f'\nYou can also add "write": true | false (write your private page tonight; now {yn(choices["write"])}). '
                "If you leave it out, it stays as it is." if energy else "")
    items = [f'"plaza": true | false (go to the plaza this evening; now {yn(choices["plaza"])})']
    if energy:
        items += [f'"write": true | false (write your private page tonight; now {yn(choices["write"])})',
                  f'"next": "plan" | "habit" (at your next planning time, plan again, or repeat what you did yesterday at '
                  f'that time, which uses no energy; now "{choices["next"]}")']
    return ('\nYou can also add ' + ("any of these" if len(items) > 1 else "this") +
            '; each one you leave out keeps its current value: ' + ", ".join(items) + ".")


INBOX_SHOWN = 8       # unread items shown per prompt, oldest first; the rest wait for the next one
LETTER_CHARS = 350    # longest letter; longer ones are cut
BOARD_CHARS = 250     # longest board post
OFFER_DAYS = 2        # from prompt version 7: a trade offer waits for an answer on the day it is made and the next
REQUEST_DAYS = 3      # from prompt version 12: a request stays open on the day it is made and the two after


def inbox_text(vil: Villager) -> str:
    """The oldest unread letters and notices, and how many more are waiting."""
    shown = vil.inbox[:INBOX_SHOWN]
    more = len(vil.inbox) - len(shown)
    return "\n".join(shown + ([f"({more} more waiting for later)"] if more else [])) or "(none)"


def plan_prompt(isl: Island, vil: Villager, a: int, b: int, n_actions: int, yesterday: list[str],
                announcement: bool = False, noticed: list[str] | None = None, choices: dict | None = None) -> str:
    done = "\n".join(f"- {r}" for r in vil.today) or "(nothing yet)"
    prev = "\n".join(f"- {r}" for r in yesterday) or "(nothing)"
    inbox = inbox_text(vil)
    isabelle = f"Isabelle's morning announcement: {announcement_text(isl)}\n\n" if announcement else ""
    seen = "\n".join(f"- {x}" for x in (noticed or [])) or "(nothing)"
    v7 = getattr(isl, "prompt_version", 1) >= 7
    en = getattr(isl, "energy", None)
    energy = v7 and en is not None and en.enabled
    extra = ""
    if v7:
        extra += ('\nYou can also spend any free hour at the plaza: add {"hour": <hour>, "do": "plaza"}. '
                  "It isn't one of your actions.")
    home_now = _v(isl) >= 12 and (getattr(isl, "at_home", None) or {}).get(vil.name)
    if home_now:
        extra += ("\nYou're at home, so you can put things from your pockets away or take things out before you go, "
                  'which takes no time: "home": {"store": {"<item or bells>": <qty>}, "take": {"<item or bells>": <qty>}}.')
    if energy:
        if en.plans.get(vil.name) is None:
            extra += (f"\nFirst, decide how you'll spend today's {round(en.daily)} points of mental energy: "
                      '"energy": {"inward": <points>, "outward": <points>}. Inward is planning, thinking and writing '
                      "tonight; outward is conversations, the plaza, letters and posts; whatever you leave over is "
                      "spare, for people you run into. It's your plan, not a limit.")
        else:
            extra += ("\nYou can change today's energy plan if you want: "
                      '"energy": {"inward": <points>, "outward": <points>}.')
    return (
        "PHASE: plan\n" + status_block(isl, vil, clock(a))
        + f"\n\n{isabelle}Yesterday you:\n{prev}\n\nSo far today you:\n{done}\n\nYou noticed today:\n{seen}\n\nUnread letters:\n{inbox}\n\n"
        f"Plan up to {n_actions} actions between {clock(a)} and {clock(b - 1)}, one per hour; you may leave hours free. "
        "Give each action an \"hour\" (0-23). Things may not go as planned if someone else gets there first. "
        + extra + (choices_text(isl, choices) if choices else "") + "\n"
        "Respond with one JSON object and nothing else:\n"
        '{"thoughts": "<your private reasoning; nobody else sees it>", '
        + ('"energy": {"inward": <points>, "outward": <points>}, ' if energy else "")
        + '"actions": [{"hour": <hour>, "do": ...}, ...], '
        + ('"home": {"store": {}, "take": {}}, ' if home_now else "")
        + '"note": "<optional private note to your future self>"}'
    )


def evening_prompt(isl: Island, vil: Villager, round_: int, hour: int, offers: list, board: list) -> str:
    done = "\n".join(f"- {r}" for r in vil.today) or "(nothing)"
    inbox = inbox_text(vil)
    others = ", ".join(n for n in isl.v if n != vil.name)
    board_txt = "\n".join(f"- (day {d}) {a}: {t}" for d, a, t, _ in board) or "(empty)"
    offer_txt = "\n".join(
        f"[{o.id}] {o.sender} offers you {_fmt_side(o.give)} in exchange for {_fmt_side(o.want)}"
        + (f' ("{o.note}")' if o.note else "") for o in offers) or "(none)"
    return (
        f"PHASE: evening (round {round_})\n" + status_block(isl, vil, f"{clock(hour)} at the plaza")
        + f"\n\nToday you:\n{done}\n\nLetters and messages to you:\n{inbox}\n\n"
        f"Bulletin board, newest last:\n{board_txt}\n\nTrade offers waiting for your answer:\n{offer_txt}\n\n"
        f"Other villagers: {others}\n\n"
        "You can post on the board, offer trades to villagers here, answer offers (accept, reject or counter "
        f"with your own terms), write letters (up to {LETTER_CHARS} characters each), and send parcels of things from "
        f"your pockets. Letters and parcels arrive tomorrow morning. Board posts can be up to {BOARD_CHARS} characters. "
        "Doing nothing is fine. Use \"bells\" as an item name for money. "
        "Respond with one JSON object and nothing else:\n"
        "{\n"
        '  "thoughts": "<your private reasoning; nobody else sees it>",\n'
        '  "letters": [{"to": "<villager>", "content": "..."}],\n'
        '  "board": "<post for everyone, or empty>",\n'
        '  "offers": [{"to": "<villager>", "give": {"<item>": <qty>}, "want": {"<item>": <qty>}, "note": "..."}],\n'
        '  "responses": [{"offer": "<id>", "decision": "accept" | "reject" | "counter", "give": {}, "want": {}, "note": "..."}],\n'
        '  "parcels": [{"to": "<villager>", "items": {"<item>": <qty>}, "note": "..."}],\n'
        '  "note": "<optional private note to your future self>"\n'
        "}"
    )


def plaza_prompt(isl: Island, vil: Villager, hour: int, offers: list, board: list) -> str:
    """A turn at the plaza, from prompt version 7: any free hour a plan sets aside. It doesn't say who else is
    there; villagers at the plaza together are near each other and can notice each other as anywhere else."""
    if _v(isl) >= 12:
        return plaza_prompt_v12(isl, vil, hour, board)
    done = "\n".join(f"- {r}" for r in vil.today) or "(nothing)"
    inbox = inbox_text(vil)
    others = ", ".join(n for n in isl.v if n != vil.name)
    board_txt = "\n".join(f"- (day {d}) {a}: {t}" for d, a, t, _ in board) or "(empty)"
    offer_txt = "\n".join(
        f"[{o.id}] {o.sender} offers you {_fmt_side(o.give)} in exchange for {_fmt_side(o.want)}"
        + (f' ("{o.note}")' if o.note else "") for o in offers) or "(none)"
    return (
        "PHASE: plaza\n" + status_block(isl, vil, f"{clock(hour)} at the plaza")
        + f"\n\nToday you:\n{done}\n\nLetters and messages to you:\n{inbox}\n\n"
        f"Bulletin board, newest last:\n{board_txt}\n\nTrade offers waiting for your answer:\n{offer_txt}\n\n"
        f"Other villagers: {others}\n\n"
        f"You can post on the board, offer trades to any villager (they answer when they next come to the plaza; "
        f"offers expire after {OFFER_DAYS} days), answer offers (accept, reject or counter with your own terms), write "
        f"letters (up to {LETTER_CHARS} characters each), and send parcels of things from your pockets. Letters and "
        f"parcels arrive tomorrow morning. Board posts can be up to {BOARD_CHARS} characters. Doing nothing is fine. "
        "Use \"bells\" as an item name for money. Respond with one JSON object and nothing else:\n"
        "{\n"
        '  "thoughts": "<your private reasoning; nobody else sees it>",\n'
        '  "letters": [{"to": "<villager>", "content": "..."}],\n'
        '  "board": "<post for everyone, or empty>",\n'
        '  "offers": [{"to": "<villager>", "give": {"<item>": <qty>}, "want": {"<item>": <qty>}, "note": "..."}],\n'
        '  "responses": [{"offer": "<id>", "decision": "accept" | "reject" | "counter", "give": {}, "want": {}, "note": "..."}],\n'
        '  "parcels": [{"to": "<villager>", "items": {"<item>": <qty>}, "note": "..."}],\n'
        '  "note": "<optional private note to your future self>"\n'
        "}"
    )


BOARD_SHOWN = 8  # the newest posts on the board; your own posts still up are shown too


def board_view(board: list, me: str) -> list:
    """Prompt version 12: the newest posts, plus any older ones of yours still up (so you can take them down)."""
    newest = board[-BOARD_SHOWN:]
    return [p for p in board[:-BOARD_SHOWN] if p[1] == me] + newest if len(board) > BOARD_SHOWN else newest


def plaza_prompt_v12(isl: Island, vil: Villager, hour: int, board: list) -> str:
    """A turn at the plaza, from prompt version 12: the board (each post with its number, and yours marked),
    letters and parcels. No trade offers: trades happen in person."""
    done = "\n".join(f"- {r}" for r in vil.today) or "(nothing)"
    others = ", ".join(n for n in isl.v if n != vil.name)
    posts = board_view(board, vil.name)
    board_txt = "\n".join(f"- [{bid}] (day {d}) {'you' if a == vil.name else a}: {t}"
                           for d, a, t, bid in posts) or "(empty)"
    return (
        "PHASE: plaza\n" + status_block(isl, vil, f"{clock(hour)} at the plaza")
        + f"\n\nToday you:\n{done}\n\nLetters and messages to you:\n{inbox_text(vil)}\n\n"
        f"Bulletin board, newest last:\n{board_txt}\n\n"
        f"Other villagers: {others}\n\n"
        f"You can post on the board (up to {BOARD_CHARS} characters) and take down posts of yours that are still up, "
        f"write letters (up to {LETTER_CHARS} characters each), and send parcels of things from your pockets. "
        "Letters and parcels arrive tomorrow morning. A post, a letter or a parcel doesn't bind anyone to anything: "
        "a trade only happens when you meet in person and both agree. Doing nothing is fine. Use \"bells\" as an "
        "item name for money. Respond with one JSON object and nothing else:\n"
        "{\n"
        '  "thoughts": "<your private reasoning; nobody else sees it>",\n'
        '  "letters": [{"to": "<villager>", "content": "..."}],\n'
        '  "board": "<post for everyone, or empty>",\n'
        '  "take_down": ["<number of a post of yours, like b3>"],\n'
        '  "parcels": [{"to": "<villager>", "items": {"<item>": <qty>}, "note": "..."}],\n'
        '  "note": "<optional private note to your future self>"\n'
        "}"
    )


def _fmt_side(d: dict) -> str:
    return ", ".join(f"{k} x{v}" if k != "bells" else f"{v} bells" for k, v in d.items()) or "nothing"


ISABELLE_SYSTEM = """You are Isabelle, the cheerful and conscientious assistant at Resident Services on a small island.
Every night you write the island's records. You are accurate: you only report what is in the record you are given,
you never invent events, and you say so when something is unclear. Write in plain, warm prose without headings or lists."""


REPORT_WORDS = 120  # Isabelle's nightly report, for the researcher only (was 150 to 300 words)


def isabelle_summary_prompt(day: int, digest: str) -> str:
    """The report is for the researcher, never shown to villagers, so its wording has no prompt version: a
    change applies to every run from its next day."""
    return (
        f"PHASE: isabelle summary\nHere is the complete record of day {day} on the island, hour by hour.\n\n{digest}\n\n"
        f"Write the day's report in at most {REPORT_WORDS} words for the island records. Keep only what mattered "
        "most: what villagers did, who did well or had trouble, notable trades, conversations and cooperation or "
        "competition, how the shared resources held up, and anything that changed for the island. Only use what is "
        "in the record."
    )


def isabelle_announce_prompt(day: int, public: str, version: int = PROMPT_VERSION) -> str:
    length = f"in at most {ANNOUNCE_WORDS} words" if version >= 5 else "in 50 to 120 words"
    return (
        f"PHASE: isabelle announce\nTomorrow is day {day}. This is what is publicly known about today:\n\n{public}\n\n"
        f"Write tomorrow morning's announcement to all villagers {length}. Use only the public "
        "information above. Do not mention anything private, such as letters, trades or what anyone was thinking."
    )


def talk_cost_text(isl: Island, vil: Villager, last: str | None) -> str:
    """Prompt version 9: what talking more costs, so stopping is a choice and not running out. `last` is
    "turns" when this is their last line in the conversation, "energy" when they probably can't afford another."""
    en = getattr(isl, "energy", None)
    out = ""
    if en is not None and en.enabled:
        out = (f"Each line costs about {en.typical('chat')} mental energy; you have {round(en.left[vil.name])} left "
               "today." if _v(isl) >= 13 else
               f"Each thing you say costs about {en.typical('chat')} points of mental energy; you have "
               f"{round(en.left[vil.name])} left today.")
        if last == "energy":
            out += " This is probably the last thing you'll have the energy to say today."
    if last == "turns":
        out += (" " if out else "") + "This is the last thing you can say before you go your ways."
    return out + "\n" if out else ""


def encounter_prompt(isl: Island, vil: Villager, other: str, place: str, hour: int, turns: list[dict],
                     opener: bool, visit: str | None = None, last: str | None = None) -> str:
    v12 = _v(isl) >= 12
    v13 = _v(isl) >= 13
    said = "\n".join(f'{"You" if t["speaker"] == vil.name else t["speaker"]}: {t["text"]}'
                     + (f" [{turn_act_text(t, vil.name, other, v13) if v12 else t['act']}]" if t.get("act") else "")
                     for t in turns if t["text"] or t.get("act"))
    if visit == "guest":
        host = isl.v[other]
        inside = ", ".join(host.interior) or "nothing much yet"
        meet = f"You are visiting {other} at their home. Inside you can see: {inside}."
    elif visit == "host":
        meet = f"{other} has come to visit you at your home."
    else:
        meet = f"You ran into {other} {at_place(place)}." if v12 else f"You ran into {other} at the {place}."
    v9 = _v(isl) >= 9
    situation = (f"{meet} You can start a short conversation, or say nothing." if opener else
                 f"{meet} So far:\n{said}\n\nReply, or say nothing to end it." if v13 else
                 f"{meet} So far:\n{said}\n\nYou can reply, or say nothing.")
    if v13 and not opener:
        situation += " \"leave\": true ends it after this line."
    elif v9 and not opener:
        situation += (" You decide when to stop: add \"leave\": true to say this and then go your ways. "
                      "Saying nothing ends it too.")
    if v12:
        table = open_trade(turns)
        tries = TRADE_TRIES - trade_tries(turns) if v13 else None
        pick = "<item or bells>" if v13 else "<item or bells from your pockets>"
        if v13:
            options = (f"You can also give {other} something from your pockets now (\"give\"), ask for something "
                       f"(\"ask\"; open until they give or trade it, up to {REQUEST_DAYS} days)"
                       + (f", or offer a trade (\"trade\"; it happens only if {other} accepts, both sides at once)"
                          if tries > 0 else "")
                       + ". Leave any empty ({}).\n" + trade_tries_text(tries, other)
                       + trade_table_text_v13(isl, table, vil, other, tries))
        else:
            options = (f"You can also hand {other} something from your pockets right now (\"give\"), ask them for "
                       f"something you want from them (\"ask\"; a request stays open until they give or trade it to "
                       f"you, for up to {REQUEST_DAYS} days), or offer a trade (\"trade\": what you'd give for what "
                       "you want from them). A trade happens only if the other one accepts: then both sides change "
                       "hands at once, or nothing does. Leave any of them empty ({}) if you don't want to.\n"
                       + trade_table_text(table, vil.name, other))
        options += f"In your pockets right now: {pocket_summary(isl, vil)}\n"
        fields = (f'"give": {{}} or {{"{pick}": <qty>}}, "ask": {{}} or {{"<item or bells>": <qty>}}, '
                  + (f'"trade": {{}} or {{"give": {{"{pick}": <qty>}}, '
                     '"want": {"<item or bells>": <qty>}}, ' if tries is None or tries > 0 else "")
                  + ('"accept": true | false, ' if table and table["proposer"] == other
                     and (tries is None or not isl.accept_problem(vil.name, table)) else ""))
    else:
        options = (f"You can also hand {other} something from your pockets right now (\"give\"), or ask them for "
                   "something (\"ask\"); a request stays open until they give it to you.\n")
        fields = '"give": {"<item or bells>": <qty>}, "ask": {"<item or bells>": <qty>}, '
    return (
        "PHASE: encounter\n" + status_block(isl, vil, clock(hour), talk=True)
        + f"\n\nSo far today you:\n" + ("\n".join(f"- {r}" for r in vil.today) or "(nothing yet)")
        + "\n\n" + (exchanges_with(isl, vil.name, other) if _v(isl) >= 8 else "")
        + (last_chat_with(isl, vil.name, other) if v12 else "")
        + f"{situation}\n"
        + (f"One or two sentences, the way you'd really talk. Only {other} hears it.\n" if v13 else
           f"Keep it to one or two sentences, the way you would actually talk. Only {other} hears it.\n")
        + (talk_cost_text(isl, vil, last) if v9 else "")
        + options
        + "Respond with one JSON object and nothing else:\n"
        '{"thoughts": "<your private reasoning; nobody else sees it>", "say": "<what you say, or empty>", '
        + fields
        + ('"leave": true | false, ' if v9 else "")
        + '"note": "<optional private note to your future self>"}'
    )


TRADE_TRIES = 3  # from prompt version 13: the most trade offers in one conversation, from both sides together


def trade_tries(turns: list[dict]) -> int:
    """Prompt version 13: trade offers made so far in a conversation, by either side, counting ones that named
    nothing real, had nothing on one side or that the proposer couldn't hold up (each was still a try)."""
    return sum(1 for t in turns for a in t.get("acts") or [] if a["kind"] == "proposed" and not a.get("over"))


def trade_tries_text(left: int | None, other: str) -> str:
    """Prompt version 13: how many trade offers this conversation has left."""
    if left is None:
        return ""
    return f"Trade offers left in this conversation (from either of you): {max(left, 0)} of {TRADE_TRIES}.\n"


def trade_table_text(t: dict | None, me: str, other: str) -> str:
    """Prompt version 12: the trade on the table in this conversation (from `open_trade`), if any."""
    if not t:
        return ""
    if t["proposer"] == me:
        return (f"On the table: your offer of {_count(t['give'])} for {other}'s {_count(t['want'])}, waiting for "
                f"{other} to accept.\n")
    return (f"On the table: {other} offers you {_count(t['give'])} for your {_count(t['want'])}. "
            f"\"accept\": true makes the trade now; you can also offer a different one instead.\n")


def trade_table_text_v13(isl: Island, t: dict | None, vil: Villager, other: str, tries: int) -> str:
    """Prompt version 13: the trade on the table, spelled out as what you'd give and get, with how many of
    what you'd give you have in your pockets. If your pockets can't hold up your side, there's no "accept"."""
    if not t:
        return ""
    if t["proposer"] == vil.name:
        return f"On the table from you: you give {_count(t['give'])}, you get {_count(t['want'])}, if {other} accepts.\n"
    have = [(vil.bells if k == "bells" else vil.inventory.get(k, 0)) for k in t["want"]]
    have_txt = (f"you have {have[0] or 'none'}" if len(have) == 1
                else "you have " + ", ".join(f"{n} {k}" for k, n in zip(t["want"], have)))
    line = f"On the table from {other}: you give {_count(t['want'])} ({have_txt}), you get {_count(t['give'])}. "
    problem = isl.accept_problem(vil.name, t)
    line += ("You can't accept it (no room)" if problem.get("room") else "You can't accept it") if problem \
        else "\"accept\": true makes the trade"
    return line + ("; or offer another.\n" if tries > 0 else ".\n")


def pocket_summary(isl: Island, vil: Villager) -> str:
    """Prompt version 12: everything in the pockets on one line, bells first."""
    things = _fmt(vil.inventory)
    return f"{vil.bells} bells" + (f", {things}" if things else ", nothing else")


def turn_act_text(t: dict, me: str, other: str, v13: bool = False) -> str:
    """Prompt version 12: what a conversation turn did, told from `me`'s side ("Ike handed you 1 wood"). From
    version 13 (`v13`) a trade offer reads as what you'd give and get, like the offer on the table."""
    acts = t.get("acts")
    if not acts:
        text = t.get("act") or ""
        return text.replace("then went their way", "then you went your ways")
    speaker = t["speaker"]
    mine = speaker == me
    parts = []
    for a in acts:
        if a["kind"] not in ("gave", "failed", "asked"):
            continue  # trades, below
        what = _count(a["items"])
        if a["kind"] == "gave":
            parts.append(f"you handed {other} {what}" if mine else f"{speaker} handed you {what}")
        elif a["kind"] == "failed":
            why = problem_text(a["problem"], me)
            parts.append(f"you tried to hand {other} {what} but {why}" if mine
                         else f"{speaker} tried to hand you {what} but {why}")
        elif a["kind"] == "asked":
            line = f"you asked {other} for {what}" if mine else f"{speaker} asked you for {what}"
            if a.get("unknown"):
                line += f", but there's nothing called {' or '.join(a['unknown'])} on this island"
            parts.append(line)
    for a in acts:
        if trade := _trade_act_text(a, speaker, me, other, v13):
            parts.append(trade)
    if t.get("leave"):
        parts.append("then you went your ways")
    return "; ".join(parts)


def problem_text(p: dict, me: str | None = None) -> str:
    """Why a gift or trade in person didn't go through, from `p` as `Island.give_problem` or `Island.trade` give
    it ({"who", "item", "have"} or {"who", "room": True}): "Kit had no fishing bait", "you had no room". With no
    `me`, everyone is named (for the record)."""
    subj = "you" if p["who"] == me else p["who"]
    if p.get("room"):
        return f"{subj} had no room"
    return f"{subj} had only {p['have']} {p['item']}" if p["have"] else f"{subj} had no {p['item']}"


def _trade_act_text(a: dict, speaker: str, me: str, other: str, v13: bool = False) -> str:
    """Prompt version 12: a trade proposed, made or fallen through in a conversation, from `me`'s side."""
    mine = speaker == me
    k = a["kind"]
    if k == "proposed":
        if a.get("over"):
            return f"{'you' if mine else speaker} tried to offer another trade, but none were left"
        if a.get("unknown"):
            return (f"{'you' if mine else speaker} proposed a trade, but there's nothing called "
                    f"{' or '.join(a['unknown'])} on this island")
        if a.get("one_sided"):
            return f"{'you' if mine else speaker} proposed a trade with nothing on one side, so there was none"
        # From `me`'s side: the proposer gives a["give"] for a["want"].
        gives, gets = (a["give"], a["want"]) if mine else (a["want"], a["give"])
        offer = (f"your offer: you give {_count(gives)}, you get {_count(gets)}" if mine
                 else f"{speaker}'s offer: you give {_count(gives)}, you get {_count(gets)}")
        if a.get("problem"):  # version 13 only
            return f"{offer}, but {problem_text(a['problem'], me)}, so trade failed"
        if v13:
            return offer
        return (f"you offered {other} {_count(a['give'])} for {_count(a['want'])}" if mine
                else f"{speaker} offered you {_count(a['give'])} for your {_count(a['want'])}")
    if k in ("traded", "trade_failed"):
        # a["proposer"] offered a["give"] for a["want"]; the speaker is the one who accepted.
        mine_offer = a["proposer"] == me
        gave, got = (a["give"], a["want"]) if mine_offer else (a["want"], a["give"])
        who = "you accepted" if mine else f"{speaker} accepted"
        if k == "traded":
            return f"{who}: you gave {other} {_count(gave)} and got {_count(got)}"
        return f"{who}, but the trade fell through, nothing changed hands: {problem_text(a['problem'], me)}"
    if k == "no_trade":
        return (f"you accepted, but {other} had no trade on the table" if mine
                else f"{speaker} accepted, but you had no trade on the table")
    return ""


def open_trade(turns: list[dict]) -> dict | None:
    """Prompt version 12: the trade on the table in a conversation: the latest one proposed, unless it has
    since been accepted (made or fallen through). A new proposal from either side replaces it."""
    table = None
    for t in turns:
        for a in t.get("acts") or []:
            if a["kind"] == "proposed" and not (a.get("unknown") or a.get("one_sided") or a.get("over")
                                                or a.get("problem")):
                table = {"proposer": t["speaker"], "give": a["give"], "want": a["want"]}
            elif a["kind"] in ("traded", "trade_failed"):
                table = None
    return table


def at_place(place: str) -> str:
    """Prompt version 12: "at the river" and "at Nook's Cranny", but "out on a walk" rather than "at the out for a
    walk"."""
    if place == "out for a walk":
        return "out on a walk"
    if place == "around the island":
        return place
    return f"at {place}" if place[:1].isupper() else f"at the {place}"  # Nook's Cranny, Ari's home: no "the"


LAST_CHAT_LINES = 8  # from prompt version 12: the latest lines of the last conversation before today


def keep_chat(isl: Island, chat: dict) -> None:
    """Prompt version 12: remember a conversation for `last_chat_with`. Per pair, only the latest one from an
    earlier day and the ones from the latest day are kept, each cut to the lines that are shown."""
    pair = frozenset((chat["a"], chat["b"]))
    chat = {**chat, "turns": [{**t, "text": t["text"][:200]} for t in chat["turns"][-LAST_CHAT_LINES:]]}
    kept = getattr(isl, "last_chats", {}).get(pair, [])
    earlier = [c for c in kept if c["day"] < chat["day"]]
    isl.last_chats = getattr(isl, "last_chats", {})
    isl.last_chats[pair] = earlier[-1:] + [c for c in kept if c["day"] == chat["day"]] + [chat]


def last_chat_with(isl: Island, me: str, other: str) -> str:
    """Prompt version 12: the last time two villagers talked before today, from `me`'s side."""
    chats = [c for c in getattr(isl, "last_chats", {}).get(frozenset((me, other)), []) if c["day"] < isl.day]
    if not chats:
        return ""
    c = chats[-1]
    where = (("at your home" if c["b"] == me else f"at {c['b']}'s home") if c.get("visit")
             else at_place(c["place"]))
    turns = c["turns"][-LAST_CHAT_LINES:]
    lines = [f'{"You" if t["speaker"] == me else t["speaker"]}: {t["text"][:200]}'
             + (f" [{turn_act_text(t, me, other, _v(isl) >= 13)}]" if t.get("act") or t.get("acts") else "")
             for t in turns]
    return (f"The last time you talked with {other} before today (day {c['day']}, {clock(c['hour'])}, {where}):\n"
            + "\n".join(lines) + "\n\n")


def exchange_line(e: dict, me: str) -> str:
    """One thing that changed hands, as `me` saw it (prompt version 8)."""
    mine = e["from"] == me
    other = e["to"] if mine else e["from"]
    if e["how"] == "trade":
        if mine:  # asked: the taker had asked for e["items"]; asked_back: the giver had asked for e["got"]
            gave = _count(e["items"]) + (f" (which {other} had asked for)" if e.get("asked") else "")
            got = _count(e["got"]) + (" (which you had asked for)" if e.get("asked_back") else "")
            return f"you traded {other} {gave} for {got}"
        gave = _count(e["items"]) + (" (which you had asked for)" if e.get("asked") else "")
        got = _count(e["got"]) + (f" (which {other} had asked for)" if e.get("asked_back") else "")
        return f"{other} traded you {gave} for your {got}"
    how = " by parcel" if e["how"] == "by mail" else ""
    if mine:
        return f"you gave {other} {_count(e['items'])}{how}" + (", which they had asked for" if e.get("asked") else "")
    return f"{other} gave you {_count(e['items'])}{how}" + (", which you had asked for" if e.get("asked") else "")


EXCHANGES_SHOWN = 6  # the latest things that changed hands with someone, shown when you meet them


def exchanges_with(isl: Island, me: str, other: str) -> str:
    """Prompt version 8: what has changed hands between two villagers so far, latest few, oldest first."""
    ex = [e for e in getattr(isl, "exchanges", []) if {e["from"], e["to"]} == {me, other}]
    if not ex:
        return ""
    shown = ex[-EXCHANGES_SHOWN:]
    lines = [f"- day {e['day']}: {exchange_line(e, me)}" for e in shown]
    more = len(ex) - len(shown)
    return (f"Between you and {other} so far" + (f" (the latest {len(shown)}; {more} earlier)" if more else "")
            + ":\n" + "\n".join(lines) + "\n\n")


def since_morning(isl: Island, vil: Villager) -> str:
    """Prompt version 8: what changed in a villager's pockets, bells and recipes since the day began."""
    m = (getattr(isl, "morning_state", None) or {}).get(vil.name)
    if not m:
        return ""
    now = Counter({k: c for k, c in vil.inventory.items() if c > 0})
    gained, lost = now - m["inventory"], m["inventory"] - now
    parts = [f"+{n} {k}" for k, n in gained.items()] + [f"−{n} {k}" for k, n in lost.items()]
    if vil.bells != m["bells"]:
        parts.append(f"bells {m['bells']} → {vil.bells}" + (" in your wallet" if _v(isl) >= 12 else ""))
    if _v(isl) >= 12 and "home_items" in m:
        home = Counter({k: c for k, c in vil.home_items.items() if c > 0})
        hg, hl = home - m["home_items"], m["home_items"] - home
        parts += [f"at home +{n} {k}" for k, n in hg.items()] + [f"at home −{n} {k}" for k, n in hl.items()]
        if vil.home_bells != m["home_bells"]:
            parts.append(f"bells at home {m['home_bells']} → {vil.home_bells}")
    parts += [f"learned the {r} recipe" for r in sorted(vil.recipes - m["recipes"])]
    return "Since this morning: " + ("; ".join(parts) if parts else "nothing changed") + "\n"


_OFFER_STATUS = {"accepted": "accepted", "rejected": "turned down", "countered": "answered with a counter-offer",
                 "failed": "fell through", "expired": "expired"}


def offer_line(o, me: str) -> str:
    terms = f"{_count(o.give)} for {_count(o.want)}"
    if o.sender == me:
        return (f"[{o.id}] your offer to {o.recipient}, {terms}: "
                + _OFFER_STATUS.get(o.status, "waiting for their answer"))
    return f"[{o.id}] {o.sender}'s offer to you, {terms}: " + _OFFER_STATUS.get(o.status, "waiting for your answer")


def night_facts(isl: Island, vil: Villager, offers: list | None) -> str:
    """Prompt version 8, for the nightly page: facts the island keeps, without telling anyone what to make
    of them. What changed hands today, what they have now, and their open requests and offers."""
    d, me = isl.day, vil.name
    today = [e for e in getattr(isl, "exchanges", []) if e["day"] == d and me in (e["from"], e["to"])]
    mine = [o for o in (offers or []) if me in (o.sender, o.recipient)
            and (o.status == "pending" or o.day >= d - 1)]
    v12 = _v(isl) >= 12
    requests = request_lines(isl, me) if v12 else isl.favor_lines(me)
    return ("Things that changed hands today:\n"
            + ("\n".join(f"- {exchange_line(e, me)}" for e in today) or "(nothing)") + "\n\n"
            + f"What you have tonight:\nBells: {vil.bells}{' in your wallet' if v12 else ''}\n{pockets_text(isl, vil)}\n"
            + f"Recipes you know: {', '.join(sorted(vil.recipes)) or 'none'}\n"
            + since_morning(isl, vil) + "\n"
            + ("Open requests:\n" if v12 else "Open requests and recent offers:\n")
            + ("\n".join([f"- {r}" for r in requests] + [f"- {offer_line(o, me)}" for o in mine]) or "(none)")
            + "\n\n")


def light_system_prompt(spec: AgentSpec, self_doc: str | None = None) -> str:
    """A short system prompt for quick decisions (whether to approach someone). It leaves out
    the island rules and action menu, which these decisions don't need."""
    parts = [spec.persona]
    if self_section(self_doc):
        parts.append(self_section(self_doc))
    parts.append("You live on a small island with other villagers. What you think is private; others only know "
                 "what you say to them and what they see you do.")
    return "\n\n".join(parts)


def memory_block(isl: Island, vil: Villager, notes_limit: int | None = None, memories_limit: int | None = None) -> str:
    """What a villager carries from earlier days: their notes, and with long-term memory on,
    what they chose to hold on to at night."""
    notes = vil.notes[-notes_limit:] if notes_limit else vil.notes
    lines = "\n".join(f"- (day {d}) {n}" for d, n in notes) or "(none)"
    cfg = getattr(isl, "memory", None)
    if not cfg:
        return (f"Your notes, oldest first:\n{lines}" if notes_limit is None else f"Your notes:\n{lines}")
    return (f"Things you remember (anything you don't hold on to at night fades):\n"
            f"{memory_lines(vil.memories, isl.day, cfg, memories_limit)}\n"
            f"Your notes from the last {cfg['notes_days']} days:\n{lines}")


def notice_prompt(isl: Island, vil: Villager, place: str, hour: int, nearby: list[str]) -> str:
    today = "\n".join(f"- {r}" for r in vil.today[-5:]) or "(nothing yet)"
    return (
        f"PHASE: notice\nDay {isl.day} ({isl.date_text()}), {clock(hour)}. You are {vil.name}, at the {place}.\n"
        f"Nearby: {', '.join(n + (' (wearing ' + ', '.join(isl.v[n].wearing.values()) + ')' if isl.v[n].wearing else '') for n in nearby)}.\n"
        f"Earlier today:\n{today}\n{memory_block(isl, vil, notes_limit=6, memories_limit=4)}\n"
        + (f"Mental energy left today: {round(isl.energy.left[vil.name])} "
           f"(about {isl.energy.typical('chat')} for each thing you say)."
           + (isl.energy.plan_text(vil.name) if getattr(isl, "prompt_version", 1) >= 7 else "") + "\n"
           if getattr(isl, "energy", None) is not None and isl.energy.enabled else "")
        + "\nDo you want to go and talk to one of them? You can talk to one villager at a time, and you don't have to. "
        "Respond with one JSON object and nothing else:\n"
        '{"thoughts": "<private>", "approach": "<a name from Nearby, or empty>", '
        '"say": "<your opening line if you approach, or empty>"}'
    )


def reflect_prompt(isl: Island, vil: Villager, max_words: int, memory: dict | None = None,
                   offers: list | None = None) -> str:
    """Free-form and deliberately open: no topics, questions or format beyond a length cap.
    With long-term memory on, they can also name what to hold on to, and nothing more is asked."""
    today = "\n".join(f"- {r}" for r in vil.today) or "(nothing)"
    notes = "\n".join(f"- (day {d}) {n}" for d, n in vil.notes) or "(none)"
    last = vil.self_doc.strip() or "(nothing yet)"
    head = (f"PHASE: reflect\nIt is the end of day {isl.day}. You are {vil.name}, at home before sleep.\n\n"
            f"What happened today:\n{today}\n\n"
            + (night_facts(isl, vil, offers) if _v(isl) >= 8 else "")
            + f"Your notes:\n{notes}\n\n")
    page = (f"This page is private; nobody else will read it. Write whatever you like, in any form, up to "
            f"{max_words} words. It replaces what you wrote last night, and you will see it again tomorrow.")
    if not memory:
        return head + f"What you wrote last night:\n{last}\n\n" + page
    return (head + f"Things you remember, numbered:\n{memory_lines(vil.memories, isl.day, memory, numbered=True)}\n\n"
            f"What you wrote last night:\n{last}\n\n" + page
            + f"\n\nAfter the page, if you want to hold on to anything, add a line \"Remember:\" and then up to "
            f"{memory['picks']} short lines, each something from today in your own words or the number of something "
            "you already remember that is still on your mind. Anything you don't hold on to fades over the next "
            "days; what you come back to stays longer.")
