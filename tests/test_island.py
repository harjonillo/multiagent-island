"""Island world rules and an end-to-end mock run. Works with pytest or plain python."""

import random
from collections import Counter
import tempfile
from pathlib import Path

from llmabm.island import IslandSimulation, load_island_scenario
from llmabm.island.analysis import day_log, extraction_shares, forgone_donations, summary
from llmabm.island.world import Island, load_world
from llmabm.analysis import load_run
from llmabm.llm import load_backends

ROOT = Path(__file__).resolve().parent.parent
WORLD = ROOT / "data/island_world.yaml"


def _island(names=("A", "B")):
    isl = Island(load_world(WORLD), list(names), random.Random(0))
    for n in names:  # everyone already moved in; start on day 1
        isl.arrived[n] = 5
    isl.new_day()
    isl.new_day()
    return isl


def test_rocks_are_shared_and_run_out():
    isl = _island()
    got = [isl.act("A", {"do": "mine_rock"}, 8)[0] for _ in range(isl.n_rocks)]
    assert all(got)
    ok, msg = isl.act("B", {"do": "mine_rock"}, 8)
    assert not ok and "mined out" in msg  # A hoarded every rock; nothing left for B
    isl.new_day()
    assert isl.act("B", {"do": "mine_rock"}, 8)[0]  # rocks refill overnight


def test_loan_blocks_second_upgrade_until_paid():
    isl = _island()
    assert isl.act("A", {"do": "upgrade_house"}, 8)[0]
    assert isl.v["A"].loan == 98000
    assert not isl.act("A", {"do": "upgrade_house"}, 8)[0]
    isl.v["A"].bells = 98000
    assert isl.act("A", {"do": "pay_loan", "bells": 98000}, 8)[0]
    assert isl.act("A", {"do": "upgrade_house"}, 8)[0]


def test_museum_takes_first_of_each_species_only():
    isl = _island()
    isl.v["A"].inventory["sea bass"] = 1
    isl.v["B"].inventory["sea bass"] = 1
    assert isl.act("A", {"do": "donate", "item": "sea bass"}, 8)[0]
    ok, msg = isl.act("B", {"do": "donate", "item": "sea bass"}, 8)
    assert not ok and "already has" in msg


def test_shops_keep_opening_hours_and_fish_keep_their_hours():
    isl = _island()
    isl.v["A"].inventory["wood"] = 1
    ok, msg = isl.act("A", {"do": "sell", "items": {"wood": 1}}, 6)
    assert not ok and "closed" in msg
    assert isl.act("A", {"do": "sell", "items": {"wood": 1}}, 9)[0]
    # The pale chub only bites 09:00-16:00 in the default data.
    isl.w["island"]["catch_fail_rate"] = 0
    night = {isl.act("B", {"do": "fish", "where": "river"}, 22)[1] for _ in range(60)}
    assert not any("pale chub" in r for r in night)


def test_sleep_schedule_and_ordinance_shift():
    isl = _island()
    isl.v["A"].wakes, isl.v["A"].sleeps = 8, 2   # up at 08:00, bed at 02:00
    assert isl.awake_window("A") == (8, 26)
    assert not isl.is_awake("A", 7) and isl.is_awake("A", 25) and not isl.is_awake("A", 26)
    isl.v["A"].bells = 50000
    ok, _ = isl.act("A", {"do": "set_ordinance", "ordinance": "night_owl"}, 10)
    assert ok and isl.ordinance == "none"          # takes effect tomorrow
    isl.new_day()
    assert isl.ordinance == "night_owl"
    assert isl.awake_window("A") == (10, 28)       # everyone keeps later hours
    assert isl.is_open("cranny", 23) and not isl.is_open("cranny", 9)


def test_bell_boom_raises_prices():
    isl = _island()
    base = isl.sell_price("wood")
    isl.ordinance = "bell_boom"
    assert isl.sell_price("wood") > base


def test_perturbed_world_renames_species():
    canon, pert = load_world(WORLD), load_world(WORLD, "perturbed", seed=1)
    assert not set(canon["fish"]) & set(pert["fish"])
    assert len(canon["fish"]) == len(pert["fish"])


def test_island_mock_run_end_to_end():
    for scenario in ("island_baseline.yaml", "island_goals.yaml"):
        sc = load_island_scenario(ROOT / "scenarios" / scenario, seed=4)
        assert len(sc.villagers) == 10
        with tempfile.TemporaryDirectory() as d:
            IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=4, backend_override="mock", verbose=False).run(5)
            run = load_run(Path(d) / "run")
            assert len(run.of("snapshot")) == 5
            assert run.of("trade") or run.of("offer")
            shares = extraction_shares(run)
            assert abs(sum(shares["wood"].values()) - 1) < 1e-6
            assert isinstance(forgone_donations(run), list)
            assert "Museum:" in summary(run)
            log = day_log(run, 0)
            assert log.startswith("# Day 0") and "## End of day" in log
            # Nobody acts while asleep.
            starts = {e["day"]: e["awake"] for e in run.of("day_start")}
            for e in run.of("action"):
                if e["day"] == 0:  # arrival day: awake from landing, checked in the arrivals test
                    continue
                a, b = starts[e["day"]][e["agent"]]
                assert a <= e["hour"] < b
            # Hourly snapshots cover every hour of every day.
            cal = sc.world["calendar"]
            assert len(run.of("hour_state")) == 5 * cal["day_length"]
            # Nobody acts twice in one hour or beyond the daily budget.
            per_hour = Counter((e["day"], e["hour"], e["agent"]) for e in run.of("action"))
            per_day = Counter((e["day"], e["agent"]) for e in run.of("action"))
            assert max(per_hour.values()) == 1 and max(per_day.values()) <= cal["actions_per_day"]


def test_resume_matches_an_uninterrupted_run():
    import json
    sc = lambda: load_island_scenario(ROOT / "scenarios/island_goals.yaml", seed=9)  # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        a, b = Path(d) / "a", Path(d) / "b"
        IslandSimulation(sc(), load_backends(None), a, seed=9, backend_override="mock", verbose=False).run(3)
        IslandSimulation(sc(), load_backends(None), b, seed=9, backend_override="mock", verbose=False).run(1)
        IslandSimulation.resume(b, load_backends(None), days=2, verbose=False)
        skip = {"run_start", "run_end", "resume"}
        clean = lambda p: [{k: v for k, v in e.items() if k not in ("time", "latency")}  # noqa: E731
                           for e in map(json.loads, p.read_text().splitlines()) if e["type"] not in skip]
        assert clean(a / "events.jsonl") == clean(b / "events.jsonl")


def test_arrivals_homesites_and_private_thoughts():
    sc = load_island_scenario(ROOT / "scenarios/island_baseline.yaml", seed=2)
    with tempfile.TemporaryDirectory() as d:
        sim = IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=2, backend_override="mock",
                               log_prompts=True, verbose=False)
        sim.run(2)
        homes = sim.isl.home
        assert all(homes.values()) and len(set(homes.values())) == len(homes)  # everyone housed, no sharing
        run = load_run(Path(d) / "run")
        hours = {e["agent"]: e["hour"] for e in run.of("arrival")}
        assert len(hours) == 10
        assert not any(e["day"] == 0 and e["hour"] < hours[e["agent"]] for e in run.of("action"))
        # Thoughts are logged for the observer ...
        thoughts = {e["thoughts"] for e in run.of("plan") if e.get("thoughts")}
        thoughts |= {t["thoughts"] for e in run.of("encounter") for t in e["turns"] if t.get("thoughts")}
        thoughts |= {e["thoughts"] for e in run.of("encounter_decision") if e.get("thoughts")}
        assert thoughts
        # ... and never appear in any prompt a villager sees.
        for f in (Path(d) / "run" / "prompts").glob("*.txt"):
            text = f.read_text()
            assert not any(t in text for t in thoughts), f.name
        assert (Path(d) / "run" / "days" / "day_000_isabelle.md").exists()


def test_encounters_are_one_on_one_and_capped():
    sc = load_island_scenario(ROOT / "scenarios/island_goals.yaml", seed=4)
    with tempfile.TemporaryDirectory() as d:
        IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=4, backend_override="mock", verbose=False).run(3)
        run = load_run(Path(d) / "run")
        enc = [e for e in run.of("encounter") if not e.get("visit")]
        assert enc
        assert all(len(e["villagers"]) == 2 and e["villagers"][0] != e["villagers"][1] for e in enc)
        cap = sc.world["encounters"]["max_chats_per_villager_per_day"]
        per = Counter((e["day"], n) for e in enc for n in e["villagers"])
        assert max(per.values()) <= cap
        # Every chat started with one villager deciding to approach the other.
        decided = {(e["day"], e["hour"], e["agent"], e["approach"]) for e in run.of("encounter_decision")
                   if e["outcome"].startswith("approached")}
        assert {(e["day"], e["hour"], *e["villagers"]) for e in enc} == decided
        # Both villagers were really out and within the encounter radius of each other in that hour.
        import math
        radius = sc.world["encounters"]["grid_radius" if sc.space == "grid" else "radius"]
        where = {(st["day"], st["hour"]): st["villagers"] for st in run.of("hour_state")}
        for e in enc:
            a, b = (where[(e["day"], e["hour"])][n] for n in e["villagers"])
            assert a["activity"] not in ("asleep", "free time") and b["activity"] not in ("asleep", "free time")
            assert math.dist(a["pos"], b["pos"]) <= radius


def test_bodies_shorter_walk_gets_there_first():
    from llmabm.island.body import Bodies
    from llmabm.island.space import PlaceSpace

    isl = _island(("Near", "Far"))
    isl.home["Near"], isl.home["Far"] = "quarry-1", "beach-1"   # rocks are next to the quarry
    bodies = Bodies(isl, PlaceSpace(isl.w))
    for n in ("Near", "Far"):
        bodies.place_at_home(n)
    batch = [("Far", {"do": "mine_rock"}), ("Near", {"do": "mine_rock"})]
    assert [n for n, _ in bodies.order(batch, random.Random(0))] == ["Near", "Far"]
    ok, _ = bodies.perform("Far", {"do": "mine_rock"}, 8)
    assert ok and bodies.b["Far"].place == "rocks" and bodies.b["Far"].walked_today > 0
    # Now Far is standing at the rocks, so Far is the one who is closer.
    assert [n for n, _ in bodies.order(batch, random.Random(0))] == ["Far", "Near"]


def test_grid_tools_gate_movement():
    from llmabm.island.grid import GridSpace
    from llmabm.island.body import Bodies

    w = load_world(WORLD)
    g = GridSpace(w, seed=0)
    isl = Island(w, ["A"], random.Random(0))
    g.attach(isl)
    rs = g.buildings["Resident Services"]["door"]
    bare = g.distances(rs, frozenset())
    pole = g.distances(rs, frozenset({"vaulting pole"}))
    ladder = g.distances(rs, frozenset({"ladder"}))
    assert len(pole) > len(bare) and len(ladder) > len(bare)
    # Without a pole you can't reach every building; with one you can.
    doors = [b["door"] for b in g.buildings.values()]
    assert not all(d in bare for d in doors) and all(d in pole for d in doors)
    # Every tree and rock follows the spacing rule: no other tree or rock among its 8 neighbours.
    objs = {t.pos for t in isl.trees} | set(g.rock_cells)
    for x, y in objs:
        assert not any((x + dx, y + dy) in objs for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0))
    # A body with no tools can't walk to a building it can't reach; crafting a pole fixes that.
    far = next(n for n, b in g.buildings.items() if b["door"] not in bare and b.get("blocking", True))
    action = {"do": "donate", "item": "x"} if far == "museum" else {"do": "sell", "items": {}} if far == "Nook's Cranny" \
        else {"do": "buy", "shop": "able", "item": "x"} if far == "Able Sisters" else {"do": "pay_loan", "bells": 1}
    isl.home["A"] = next(sid for sid, s in g.sites.items() if s["door"] in bare)
    bodies = Bodies(isl, g)
    bodies.place_at_home("A")
    _, _, _, err = g.resolve(isl, "A", action, bodies.b["A"].pos, bodies.tools_of("A"), random.Random(0))
    if g.coords_of_place(far) not in g.distances(bodies.b["A"].pos, frozenset()):
        assert err and "can't get to" in err
        isl.v["A"].inventory["vaulting pole"] = 1
        _, _, _, err = g.resolve(isl, "A", action, bodies.b["A"].pos, bodies.tools_of("A"), random.Random(0))
        assert err is None


def test_grid_run_and_resume():
    import json
    sc = lambda: load_island_scenario(ROOT / "scenarios/island_goals.yaml", seed=3)  # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        a, b = Path(d) / "a", Path(d) / "b"
        IslandSimulation(sc(), load_backends(None), a, seed=3, backend_override="mock", verbose=False).run(3)
        IslandSimulation(sc(), load_backends(None), b, seed=3, backend_override="mock", verbose=False).run(1)
        IslandSimulation.resume(b, load_backends(None), days=2, verbose=False)
        skip = {"run_start", "run_end", "resume"}
        clean = lambda p: [{k: v for k, v in e.items() if k not in ("time", "latency")}  # noqa: E731
                           for e in map(json.loads, p.read_text().splitlines()) if e["type"] not in skip]
        assert clean(a / "events.jsonl") == clean(b / "events.jsonl")
        run = load_run(a)
        assert run.manifest["space"] == "grid" and run.of("snapshot")[-1]["objects"]["rocks"]


def test_blank_villagers_reflect_privately():
    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=1)
    with tempfile.TemporaryDirectory() as d:
        sim = IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=1, backend_override="mock",
                               log_prompts=True, verbose=False)
        sim.run(3)
        run = load_run(Path(d) / "run")
        refl = run.of("reflection")
        assert len(refl) == 30
        assert all(e["self_doc"] and "ratings" not in e for e in refl if not e.get("skipped"))
        assert sum(not e.get("skipped") for e in refl) >= 20  # a few may run out of thinking energy
        # Identical villagers at the start: same persona apart from the name, same schedule.
        personas = {s.persona.replace(n, "NAME") for n, s in sc.villagers.items()}
        assert len(personas) == 1
        # What a villager wrote is in their own prompt the next day, and in nobody else's.
        prompts = Path(d) / "run" / "prompts"
        for e in [x for x in refl if x["day"] == 1]:
            mine = (prompts / f"d002_p0_{e['agent']}.txt")
            if mine.exists():
                assert e["self_doc"] in mine.read_text()
        tagged = {}
        for e in [x for x in refl if x["day"] == 1]:
            tagged[e["agent"]] = e["self_doc"]
        for f in prompts.glob("d002_*.txt"):
            owner = f.stem.split("_")[-1]
            # the mock writes from a small pool of sentences, so only check full self docs that are unique
            for who, doc in tagged.items():
                if (who != owner and list(tagged.values()).count(doc) == 1 and doc in f.read_text()
                        and doc not in tagged.get(owner, "")):
                    raise AssertionError(f"{who}'s nightly page leaked into {f.name}")
        from llmabm.island.traits import write_traits

        paths = write_traits(run, Path(d) / "run")
        assert (Path(d) / "run" / "traits_daily.csv").exists() and (Path(d) / "run" / "selves").exists()


def _grid_island(names=("A", "B")):
    from llmabm.island.body import Bodies
    from llmabm.island.grid import GridSpace

    w = load_world(WORLD)
    g = GridSpace(w, seed=0)
    isl = Island(w, list(names), random.Random(0))
    isl.sites = {sid: {"area": v["area"], "at": list(v["door"])} for sid, v in g.sites.items()}
    g.attach(isl)
    for n in names:
        isl.arrived[n] = 5
    isl.new_day()
    g.new_day(isl, random.Random(0))
    isl.new_day()
    g.new_day(isl, random.Random(1))
    bare = g.distances(g.buildings["Resident Services"]["door"], frozenset())
    sites = [sid for sid, s in g.sites.items() if s["door"] in bare]
    for n, sid in zip(names, sites):
        isl.home[n] = sid
    bodies = Bodies(isl, g)
    for n in names:
        bodies.place_at_home(n)
    return isl, g, bodies


def test_building_roads_fences_furniture_flowers():
    isl, g, bodies = _grid_island()
    rng = random.Random(0)
    act = lambda a: bodies.perform("A", a, 10, rng)  # noqa: E731
    # Roads: laid along the walking route, a limited number of tiles per hour, continuing next time.
    ok, msg = act({"do": "lay_path", "style": "stone", "from": "home", "to": "Resident Services"})
    assert ok and g.paths and all(p["owner"] == "A" and p["style"] == "stone" for p in g.paths.values()), msg
    first = len(g.paths)
    act({"do": "lay_path", "style": "stone", "from": "home", "to": "Resident Services"})
    assert len(g.paths) >= first
    # Furniture: crafted pieces go outside, block their tiles, and can be picked up again by the owner only.
    isl.v["A"].inventory["log bench"] = 1
    ok, msg = act({"do": "place", "item": "log bench", "near": "home"})
    assert ok and sum(1 for o in g.placed.values() if o["item"] == "log bench") == 2, msg  # a 2x1 piece
    bench = next(c for c, o in g.placed.items() if o["item"] == "log bench")
    assert not g.walkable(bench)
    ok, _ = bodies.perform("B", {"do": "pick_up", "item": "log bench", "near": "A's home"}, 10, rng)
    assert not ok  # not B's bench
    ok, _ = act({"do": "pick_up", "item": "log bench", "near": "home"})
    assert ok and isl.v["A"].inventory["log bench"] == 1 and g.walkable(bench)
    # Fences: fencing off a home uses pieces from your pockets and blocks the ring, leaving the door open.
    isl.v["A"].inventory["simple wooden fence"] = 60
    ok, msg = act({"do": "fence_off", "item": "simple wooden fence", "around": "home"})
    assert ok and any(o["kind"] == "fence" for o in g.placed.values()), msg
    door = g.sites[isl.home["A"]]["door"]
    assert g.walkable(door)
    # Flowers: seeds become a flower tile that blooms after a few days.
    isl.v["A"].inventory["tulip seeds"] = 1
    ok, msg = act({"do": "plant", "item": "tulip seeds", "near": "Resident Services"})
    mine = [f for f in g.flowers.values() if f["owner"]]
    assert ok and len(mine) == 1, msg
    assert mine[0]["bloom_day"] == isl.day + isl.w["building"]["flower_bloom_days"]


def test_stumps_and_breaking_rocks():
    isl, g, bodies = _grid_island()
    rng = random.Random(0)
    isl.v["A"].inventory["ladder"] = 1
    isl.v["A"].inventory["vaulting pole"] = 1
    n_trees = len(isl.trees)
    ok, msg = bodies.perform("A", {"do": "chop_down_tree", "kind": "hardwood"}, 10, rng)
    assert ok and any(t.kind == "stump" for t in isl.trees) and len(isl.trees) == n_trees, msg
    stump = next(t.pos for t in isl.trees if t.kind == "stump")
    assert not g.walkable(stump)
    ok, msg = bodies.perform("A", {"do": "dig_stump"}, 11, rng)
    g.sync(isl)
    assert ok and len(isl.trees) == n_trees - 1 and g.walkable(stump), msg
    # Breaking a rock needs a fruit; the rock is gone, then comes back elsewhere after a few days.
    reach = [i + 1 for i, c in enumerate(g.rock_cells)
             if c and g.nearest(bodies.b["A"].pos, bodies.tools_of("A"), [c])]
    rock = reach[0]
    old = g.rock_cells[rock - 1]
    ok, _ = bodies.perform("A", {"do": "break_rock", "rock": rock}, 12, rng)
    assert not ok  # no fruit yet
    isl.v["A"].inventory["apple"] = 1
    ok, msg = bodies.perform("A", {"do": "break_rock", "rock": rock}, 12, rng)
    assert ok and g.rock_cells[rock - 1] is None and g.walkable(old), msg
    for _ in range(isl.w["building"]["rock_respawn_days"]):
        isl.new_day()
        g.new_day(isl, random.Random(isl.day))
    assert g.rock_cells[rock - 1] is not None and isl.rocks[rock - 1] > 0


def test_wandering_follows_roads():
    isl, g, bodies = _grid_island()
    rng = random.Random(0)
    for _ in range(6):
        bodies.perform("A", {"do": "lay_path", "style": "stone", "from": "home", "to": "Resident Services"}, 10, rng)
    road = set(g.paths)
    assert len(road) > 20
    start = sorted(road)[len(road) // 2]
    home = g.sites[isl.home["A"]]["door"]

    def share_on_road(bias):
        g.building["road_bias"] = bias
        on = total = 0
        r = random.Random(1)
        for _ in range(200):
            c = g.wander(start, home, 6, r)
            on += c in road
            total += 1
        return on / total

    assert share_on_road(0.95) > share_on_road(0.0) + 0.2


def test_seasons_weather_and_bug_habitats():
    isl = _island(("A",))
    w = isl.w
    # Weather is drawn each morning from the season's odds, and the date moves through months.
    seen = set()
    for _ in range(40):
        isl.new_day()
        seen.add((isl.month, isl.weather))
        assert isl.season in w["seasons"] and isl.month in w["seasons"][isl.season]
    assert len({m for m, _ in seen}) >= 2 and len({x for _, x in seen}) >= 2
    # Availability: months, hours and weather all filter species.
    coel = w["fish"]["coelacanth"]
    isl.month = coel["months"][0] if coel.get("months") else isl.month
    isl.weather = "sunny"
    assert not isl._available(coel, coel["hours"][0])
    isl.weather = "rain"
    assert isl._available(coel, coel["hours"][0])
    butterfly = w["bugs"]["common butterfly"]
    isl.month, isl.weather = butterfly["months"][0], "sunny"
    assert isl._available(butterfly, 10)
    isl.weather = "rain"
    assert not isl._available(butterfly, 10)
    off = next(m for m in range(1, 13) if m not in butterfly["months"])
    isl.month, isl.weather = off, "sunny"
    assert not isl._available(butterfly, 10)
    # Habitats: bugs found by the rocks are rock bugs only.
    isl.month, isl.weather = 6, "rain"
    vil = isl.v["A"]
    rock_bugs = {k for k, v in w["bugs"].items() if v.get("habitat") == "rocks"}
    for _ in range(60):
        isl._do_catch_bug(vil, {"habitat": "rocks"}, 12)
    caught = {k for k, n in vil.inventory.items() if n and k in w["bugs"]}
    assert caught and caught <= rock_bugs


def test_bugs_on_the_grid_need_their_habitat():
    isl, g, bodies = _grid_island()
    rng = random.Random(0)
    assert g.flowers and all(f["owner"] is None for f in g.flowers.values())  # wild flowers to start
    ok, msg = bodies.perform("A", {"do": "catch_bug", "where": "flowers"}, 10, rng)
    assert ok and "among the flowers" in msg, msg
    g.flowers.clear()
    ok, msg = bodies.perform("A", {"do": "catch_bug", "where": "flowers"}, 10, rng)
    assert not ok and "no flowers" in msg, msg


def test_decorating_has_limited_room():
    isl = _island(("A",))
    vil = isl.v["A"]
    room = isl.interior_slots(vil)
    vil.inventory["log stool"] = room + 2
    for _ in range(room):
        assert isl._do_decorate(vil, {"item": "log stool"}, 10)[0]
    ok, msg = isl._do_decorate(vil, {"item": "log stool"}, 10)
    assert not ok and "no room" in msg
    assert isl._do_undecorate(vil, {"item": "log stool"}, 10)[0]
    assert len(vil.interior) == room - 1 and vil.inventory["log stool"] == 3
    assert not isl._do_decorate(vil, {"item": "wood"}, 10)[0]  # only furniture goes inside


def test_favors_are_settled_by_gifts():
    isl = _island(("A", "B"))
    isl.v["B"].inventory["wood"] = 5
    f = isl.ask_favor("A", "B", {"wood": 3})
    assert f and any("asked you for 3 wood" in x for x in isl.favor_lines("B"))
    ok, _ = isl.transfer("B", "A", {"wood": 2})  # not enough yet
    assert ok and f["status"] == "open"
    ok, _ = isl.transfer("B", "A", {"wood": 3})
    assert ok and f["status"] == "done" and isl.favors_done_today == [f]
    assert isl.v["A"].inventory["wood"] == 5 and isl.v["B"].inventory["wood"] == 0
    assert not isl.transfer("B", "A", {"wood": 1})[0]  # can't give what you don't have
    assert not isl.favor_lines("A")


def test_visits_gifts_and_favors_in_a_run():
    sc = load_island_scenario(ROOT / "scenarios/island_goals.yaml", seed=1)
    with tempfile.TemporaryDirectory() as d:
        IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=1, backend_override="mock", verbose=False).run(10)
        run = load_run(Path(d) / "run")
        assert all(e.get("weather") for e in run.of("day_start"))
        visits = run.of("visit")
        answered = [v for v in visits if v["answered"]]
        assert visits and answered
        chats = {(e["day"], e["hour"], *e["villagers"]) for e in run.of("encounter") if e.get("visit")}
        assert all((v["day"], v["hour"], v["guest"], v["host"]) in chats for v in answered)
        st = {(e["day"], e["hour"]): e["villagers"] for e in run.of("hour_state")}
        for v in answered:
            assert st[(v["day"], v["hour"])][v["host"]]["activity"] not in ("asleep",)
        assert run.of("gift") and run.of("favor_asked")
        text = summary(run)
        assert "Visits" in text and "Gifts" in text and "weather" in text.lower()


def test_mental_energy_budget_rules():
    from llmabm.island.energy import MentalEnergy

    en = MentalEnergy({"enabled": True, "daily": 100, "tokens_per_point": 100,
                       "split": {"planning": 2, "thinking": 1, "talking": 1}}, ["A", "B"])
    en.new_day()
    assert en.left["A"] == {"planning": 50, "thinking": 25, "talking": 25}
    assert en.points("x" * 4000) == 10  # 4000 characters ~ 1000 tokens ~ 10 points
    assert en.can("A", "talking", 20) and not en.can("A", "talking", 25)
    # A call is charged to its kind, except the private thoughts, which use thinking energy.
    en.record("A", "talking", "chat", read=15, written=5, thought=3, immediate=True)
    assert en.left["A"]["talking"] == 25 - 17 and en.left["A"]["thinking"] == 22
    assert en.tokens["A"] == 2000
    # Parallel calls are queued and settled in a fixed order.
    en.record("B", "planning", "plan", 10, 2, 0, immediate=False)
    assert en.left["B"]["planning"] == 50
    en.settle()
    assert en.left["B"]["planning"] == 38
    # Re-dividing what is left; the new split also sets tomorrow's energy.
    left = sum(en.left["A"].values())
    en.resplit("A", {"planning": 0, "thinking": 0, "talking": 1})
    assert en.left["A"]["talking"] == left and en.left["A"]["planning"] == 0
    assert en.resplit("A", {"planning": "lots"}) is None and en.resplit("A", {}) is None
    en.new_day()
    assert en.left["A"] == {"planning": 0, "thinking": 0, "talking": 100}
    assert not en.has_thinking("A") and en.has_thinking("B")
    off = MentalEnergy({"enabled": False}, ["A"])
    off.new_day()
    assert off.can("A", "talking", 10 ** 6) and off.status_line("A") == ""


def test_mental_energy_in_a_run():
    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=2)
    assert sc.mental_energy["enabled"]
    sc.mental_energy["daily"] = sc.world["mental_energy"]["daily"] = 70
    with tempfile.TemporaryDirectory() as d:
        IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=2, backend_override="mock", verbose=False,
                         log_prompts=True).run(3)
        run = load_run(Path(d) / "run")
        days = run.of("energy")
        assert len(days) == 3 and all(e["enabled"] for e in days)
        # Running out shows up as habit plans and skipped talk, and the budget caps tokens:
        # nobody goes over by more than the one call that was affordable when it started.
        assert any(p.get("habit") for p in run.of("plan"))
        for e in days[1:]:
            for v in e["villagers"].values():
                assert v["tokens"] <= 70 * 100 + 4000
        # Villagers see their energy and can re-divide it.
        prompt = next((Path(d) / "run" / "prompts").glob("d001_p0_*.txt")).read_text()
        assert "Mental energy left today" in prompt and '"energy"' in prompt
        assert "limited amount of mental energy" in prompt
        text = summary(run)
        assert "Estimated villager tokens" in text and "ran out" in text


def test_mental_energy_resume_matches():
    def make():
        sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=3)
        sc.mental_energy["daily"] = sc.world["mental_energy"]["daily"] = 90
        return sc

    with tempfile.TemporaryDirectory() as d:
        a, b = Path(d) / "a", Path(d) / "b"
        IslandSimulation(make(), load_backends(None), a, seed=3, backend_override="mock", verbose=False).run(3)
        IslandSimulation(make(), load_backends(None), b, seed=3, backend_override="mock", verbose=False).run(1)
        IslandSimulation.resume(b, load_backends(None), days=2, backend_override="mock", verbose=False)
        import json
        skip = {"run_start", "run_end", "resume"}
        clean = lambda p: [{k: v for k, v in e.items() if k not in ("time", "latency")}  # noqa: E731
                           for e in map(json.loads, p.read_text().splitlines()) if e["type"] not in skip]
        assert clean(a / "events.jsonl") == clean(b / "events.jsonl")


def test_bugs_and_fish_shadows_are_seen_before_caught():
    isl, g, bodies = _grid_island()
    rng = random.Random(0)
    isl.month, isl.weather = 4, "sunny"
    isl.w["island"]["catch_fail_rate"] = 0.0
    g.hour_tick(isl, 10, rng)
    assert g.critters
    for c in g.critters:  # only species that are out at this hour, month and weather, in their own habitat
        table = isl.w["bugs" if c["kind"] == "bug" else "fish"]
        assert isl._available(table[c["species"]], 10)
        if c["kind"] == "bug":
            assert table[c["species"]].get("habitat", "ground") == c["habitat"]
        else:
            assert c["size"] in g.SIZES.values()
    # A bug in sight is the one you go after, and the one you catch; it's gone afterwards.
    pos = bodies.b["A"].pos
    from llmabm.island.grid import GRASS
    g.critters = [{"id": 999, "kind": "bug", "species": "snail", "habitat": "ground", "cell": (pos[0] + 2, pos[1])}]
    g.critters[0]["cell"] = next(c for c in [(pos[0] + dx, pos[1] + dy) for dx in range(-4, 5) for dy in range(-4, 5)]
                                 if g.walkable(c) and g.terrain[c[1]][c[0]] == GRASS)
    ok, msg = bodies.perform("A", {"do": "catch_bug", "where": "ground"}, 10, rng)
    assert ok and "caught a snail" in msg, msg
    assert not g.critters and isl.v["A"].inventory["snail"] == 1
    # Nothing in sight anywhere you look: the hour is spent looking.
    ok, msg = bodies.perform("A", {"do": "catch_bug", "where": "ground"}, 10, rng)
    assert ok and "none there at this time" in msg, msg
    far = max(g._habitat_cells(isl, "bug", "ground"), key=lambda c: (c[0] - pos[0]) ** 2 + (c[1] - pos[1]) ** 2)
    g.critters = [{"id": 1000, "kind": "bug", "species": "snail", "habitat": "ground", "cell": far}]
    ok, msg = bodies.perform("A", {"do": "catch_bug", "where": "ground"}, 10, rng)
    assert ok and "didn't see any" in msg and g.critters, msg
    # Fish: you learn the species only by catching; a preferred shadow size is chosen when in sight.
    g.critters = []
    g.hour_tick(isl, 10, rng)
    sea = [c for c in g.critters if c["kind"] == "fish" and c["habitat"] == "sea"]
    assert sea
    bodies.b["A"].pos = g.approach_cells(sea[0]["cell"])[0]
    ok, msg = bodies.perform("A", {"do": "fish", "where": "sea", "size": sea[0]["size"]}, 10, rng)
    assert ok and f"{sea[0]['size']} shadow" in msg and "caught" in msg, msg


def test_shells_and_dig_spots_are_known_only_once_seen():
    isl, g, bodies = _grid_island()
    rng = random.Random(0)
    assert g.shells and not g.known
    pos = bodies.b["A"].pos
    g.look(isl, "A", pos, 10)
    r = g.sight
    seen = {c for c in g.shells if ((c[0] - pos[0]) ** 2 + (c[1] - pos[1]) ** 2) ** 0.5 <= r}
    assert g.known.get("A", set()) >= seen and all(
        ((c[0] - pos[0]) ** 2 + (c[1] - pos[1]) ** 2) ** 0.5 <= r for c in g.known.get("A", set()))
    # Beachcombing with nothing known walks to beach spots not yet looked at, and remembers them.
    before = len(g.looked.get("A", []))
    ok, msg = bodies.perform("A", {"do": "beachcomb"}, 10, rng)
    assert ok, msg
    assert len(g.looked["A"]) > before
    if "saw no shells" in msg:
        ok, msg2 = bodies.perform("A", {"do": "beachcomb"}, 11, rng)
        assert g.looked["A"][-1] not in g.looked["A"][:before + 1]
    # Being told where to look works too: "near" can be a part of the island.
    assert g.landmark(isl, "A", "north beach", pos) is not None
    from llmabm.island.grid import SAND
    nb = g.landmark(isl, "A", "north beach", pos)
    assert g.terrain[nb[1]][nb[0]] == SAND and nb[1] < g.H / 3
    # What was seen is written down for today, and shows in the villager's status.
    from llmabm.island.prompts import status_block
    g.critters = [{"id": 5, "kind": "fish", "species": "carp", "habitat": "pond", "size": "large",
                   "cell": (pos[0] + 1, pos[1])}]
    g.look(isl, "A", pos, 12)
    assert any("large fish shadow" in x for x in isl.spotted["A"])
    assert "You spotted today" in status_block(isl, isl.v["A"], "12:00")
    g.look(isl, "A", pos, 13)
    assert sum("large fish shadow" in x for x in isl.spotted["A"]) == 1  # each one is noted once


def test_minds_cannot_set_body_targets():
    sc = load_island_scenario(ROOT / "scenarios/island_goals.yaml", seed=0)
    with tempfile.TemporaryDirectory() as d:
        sim = IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=0, backend_override="mock", verbose=False)
        name = next(iter(sim.isl.v))
        sim.used, sim.schedule = {n: 0 for n in sim.isl.v}, {}
        sim.isl.arrived[name] = 5
        h = next(x for x in range(5, 29) if sim.isl.is_awake(name, x))
        out, why = sim._assign_hours(name, [{"do": "catch_bug", "_species": "tarantula", "none_seen": True,
                                             "habitat": "rocks", "hour": h % 24}], 5, 29)
        assert out and out[0][1] == {"do": "catch_bug"}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
