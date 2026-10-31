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


def _seen(prompt_file: Path) -> str:
    """What the villager was shown: a logged prompt file minus the reasoning and reply after it."""
    return prompt_file.read_text().split("\n=== REASONING")[0]


def _island(names=("A", "B")):
    isl = Island(load_world(WORLD), list(names), random.Random(0))
    for n in names:  # everyone already moved in; start on day 1
        isl.arrived[n] = 5
    isl.new_day()
    isl.new_day()
    return isl


def test_rocks_are_shared_and_run_out():
    isl = _island()
    isl.v["A"].inventory.clear()  # room in A's pockets for everything the rocks give
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


def test_fossils_need_assessing_and_the_museum_keeps_a_tally():
    isl = _island()
    a = isl.v["A"]
    a.inventory.clear()
    assert isl.act("A", {"do": "dig_fossil"}, 8)[0] and isl.act("A", {"do": "dig_fossil"}, 8)[0]
    assert a.inventory["fossil"] == 2 and not set(a.inventory) & set(isl.w["fossils"])
    ok, msg = isl.act("A", {"do": "donate", "item": "fossil"}, 8)
    assert not ok and "assess" in msg
    assert isl.act("A", {"do": "sell", "items": {"fossil": 1}}, 8) == (True, "sold fossil x1 at Nook's Cranny for 400 bells")
    ok, msg = isl.act("A", {"do": "assess"}, 8)
    species = [k for k in a.inventory if a.inventory[k] and k in isl.w["fossils"]]
    assert ok and a.inventory["fossil"] == 0 and len(species) == 1 and species[0] in msg
    assert not isl.act("A", {"do": "assess"}, 8)[0]
    assert isl.act("A", {"do": "donate", "item": species[0]}, 8)[0]
    assert "donated by A 1" in isl.museum_line()
    for seed in range(5):  # assessed fossils are worth 1,000 to 10,000 bells, whatever the perturbation
        assert all(1000 <= p <= 10000 for p in load_world(WORLD, "perturbed", seed=seed)["fossils"].values())


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
    for scenario in ("island_blank.yaml",):
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
                if e["day"] == 0:  # landing day: up from the moment they land at 05:00
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
    sc = lambda: load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=9)  # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        a, b = Path(d) / "a", Path(d) / "b"
        IslandSimulation(sc(), load_backends(None), a, seed=9, backend_override="mock", verbose=False).run(3)
        IslandSimulation(sc(), load_backends(None), b, seed=9, backend_override="mock", verbose=False).run(1)
        IslandSimulation.resume(b, load_backends(None), days=2, verbose=False)
        skip = {"run_start", "run_end", "resume"}
        clean = lambda p: [{k: v for k, v in e.items() if k not in ("time", "latency")}  # noqa: E731
                           for e in map(json.loads, p.read_text().splitlines()) if e["type"] not in skip]
        assert clean(a / "events.jsonl") == clean(b / "events.jsonl")


def test_day0_random_homes_and_private_thoughts():
    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=2)
    with tempfile.TemporaryDirectory() as d:
        sim = IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=2, backend_override="mock",
                               log_prompts=True, verbose=False)
        sim.run(2)
        homes = sim.isl.home
        assert all(homes.values()) and len(set(homes.values())) == len(homes)  # everyone housed, no sharing
        run = load_run(Path(d) / "run")
        # Everyone lands together at the start of day 0 on a homesite drawn for them: no LLM call to choose.
        arrivals = run.of("arrival")
        assert len(arrivals) == 10 and {e["hour"] for e in arrivals} == {sc.world["calendar"]["day_start"]}
        assert not list((Path(d) / "run" / "prompts").glob("*arrival*"))
        assert {e["homesite"] for e in arrivals} == set(run.of("snapshot")[0]["homes"].values())
        # Thoughts are logged for the observer ...
        thoughts = {e["thoughts"] for e in run.of("plan") if e.get("thoughts")}
        thoughts |= {t["thoughts"] for e in run.of("encounter") for t in e["turns"] if t.get("thoughts")}
        thoughts |= {e["thoughts"] for e in run.of("encounter_decision") if e.get("thoughts")}
        assert thoughts
        # ... and never appear in any prompt a villager sees.
        for f in (Path(d) / "run" / "prompts").glob("*.txt"):
            text = _seen(f)
            assert not any(t in text for t in thoughts), f.name
        assert (Path(d) / "run" / "days" / "day_000_isabelle.md").exists()


def test_encounters_are_one_on_one_and_capped():
    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=4)
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
        radius = sc.world["encounters"]["grid_radius"]
        where = {(st["day"], st["hour"]): st["villagers"] for st in run.of("hour_state")}
        for e in enc:
            a, b = (where[(e["day"], e["hour"])][n] for n in e["villagers"])
            assert a["activity"] not in ("asleep", "free time") and b["activity"] not in ("asleep", "free time")
            assert math.dist(a["pos"], b["pos"]) <= radius


def test_bodies_shorter_walk_gets_there_first():
    isl, g, bodies = _grid_island(("Near", "Far"))
    rng = random.Random(0)
    # Both go for the same rock; Near is standing next to it.
    rock = next(i + 1 for i, c in enumerate(g.rock_cells)
                if c and g.nearest(bodies.b["Far"].pos, bodies.tools_of("Far"), [c]))
    act = {"do": "mine_rock", "rock": rock}
    bodies.b["Near"].pos = g.approach_cells(g.rock_cells[rock - 1])[0]
    batch = [("Far", act), ("Near", act)]
    assert [n for n, _ in bodies.order(batch, rng)] == ["Near", "Far"]
    ok, _ = bodies.perform("Far", act, 8, rng)
    assert ok and bodies.b["Far"].place == "rocks" and bodies.b["Far"].walked_today > 0
    # Now Far is standing at the rock and Near has gone home, so Far is the one who is closer.
    bodies.go_home("Near")
    assert [n for n, _ in bodies.order(batch, rng)] == ["Far", "Near"]


def test_fixtures_keep_a_free_tile_between_them():
    from llmabm.island.grid import GridSpace

    w = load_world(WORLD)
    for seed in range(8):
        g = GridSpace(w, seed=seed)
        isl = Island(w, ["A"], random.Random(seed))
        g.attach(isl)
        groups = [set(b["cells"]) for b in g.buildings.values()] + [set(s["cells"]) for s in g.sites.values()]
        groups += [{t.pos} for t in isl.trees if t.pos] + [{c} for c in g.rock_cells if c]
        for i, a in enumerate(groups):
            ring = {(x + dx, y + dy) for x, y in a for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
            for b in groups[i + 1:]:
                assert not ring & b, f"seed {seed}: fixtures touch at {sorted(ring & b)[:3]}"
        doors = {b["door"] for b in g.buildings.values()} | {s["door"] for s in g.sites.values()}
        assert not doors & ({t.pos for t in isl.trees} | set(g.rock_cells))
        assert not set(g.flowers) & g.fixtures  # nothing grows on the plaza or a homesite


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
    isl.v["A"].inventory["vaulting pole"] = isl.v["A"].inventory["ladder"] = 0  # start with no way across
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
    sc = lambda: load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=3)  # noqa: E731
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
        assert sum(not e.get("skipped") for e in refl) >= 10  # some choose not to write, or run out
        assert {e["skipped"] for e in refl if e.get("skipped")} <= {"chose not to write tonight", "no energy left"}
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
                if (who != owner and list(tagged.values()).count(doc) == 1 and doc in _seen(f)
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
    # A stone axe gathers wood and leaves the tree; the axe brings it down with the last wood.
    ok, msg = bodies.perform("A", {"do": "chop_wood"}, 9, rng)
    assert ok and "brought the tree down" not in msg and not any(t.kind == "stump" for t in isl.trees), msg
    ok, msg = bodies.perform("A", {"do": "chop_wood", "axe": "axe"}, 10, rng)
    assert not ok and msg == "you have no axe"
    isl.v["A"].inventory["axe"] = 1
    ok, msg = bodies.perform("A", {"do": "chop_wood", "axe": "axe"}, 10, rng)
    assert ok and "brought the tree down" in msg and isl.collected["A"]["trees_removed"] == 1, msg
    assert any(t.kind == "stump" for t in isl.trees) and len(isl.trees) == n_trees
    assert "no felling on its own" in isl.act("A", {"do": "chop_down_tree", "kind": "hardwood"}, 10)[1]
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
    # Runs from before version 4 keep one axe that never fells, and chop_down_tree to fell on purpose.
    isl.prompt_version = 3
    ok, msg = bodies.perform("A", {"do": "chop_wood", "axe": "axe"}, 13, rng)
    assert ok and "brought the tree down" not in msg, msg
    ok, msg = bodies.perform("A", {"do": "chop_down_tree", "kind": "hardwood"}, 14, rng)
    assert ok and isl.collected["A"]["trees_removed"] == 2, msg
    isl.prompt_version = 4
    # Fruit trees have wood too: a stone axe leaves the tree and its fruit; the axe fells it and the fruit drops.
    fruit_trees = [t for t in isl.trees if t.kind == "fruit"]
    isl.new_day()
    assert all(t.wood_left == isl.w["island"]["wood_per_tree"] for t in isl.trees if t.kind == "fruit")
    ok, msg = bodies.perform("A", {"do": "chop_wood", "kind": "fruit", "axe": "stone axe"}, 15, rng)
    assert ok and "fruit tree" in msg and "brought the tree down" not in msg, msg
    assert len([t for t in isl.trees if t.kind == "fruit"]) == len(fruit_trees)
    apples = isl.v["A"].inventory["apple"]
    stumps = sum(1 for t in isl.trees if t.kind == "stump")
    ok, msg = bodies.perform("A", {"do": "chop_wood", "kind": "fruit", "axe": "axe"}, 16, rng)
    assert ok and "brought the tree down" in msg and "apple x3" in msg, msg
    assert sum(1 for t in isl.trees if t.kind == "stump") == stumps + 1
    assert len([t for t in isl.trees if t.kind == "fruit"]) == len(fruit_trees) - 1
    assert isl.v["A"].inventory["apple"] == apples + 3
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


def test_tools_wear_out_and_recipe_cards_replace_them():
    isl = _island(("A", "B"))
    a, b = isl.v["A"], isl.v["B"]
    tools = isl.w["tools"]["start_with"]
    assert all(a.inventory[t] == 1 for t in tools) and len(tools) == 6
    assert not set(isl.tool_recipes()) & a.recipes  # nobody knows how to make tools at the start
    isl.w["tools"]["uses"] = 3
    assert "stone axe" in tools and a.inventory["axe"] == 0  # the axe that fells trees is an upgrade
    assert isl.use_tool(a, "stone axe") is None and isl.use_tool(a, "stone axe") is None
    assert isl.use_tool(a, "stone axe") == "your stone axe broke" and a.inventory["stone axe"] == 0
    # Tool recipe cards are always at the Cranny; the daily card is never a tool.
    for _ in range(20):
        isl.new_day()
        assert isl.cranny_recipe not in isl.tool_recipes()
        assert set(isl.tool_recipes()) <= set(isl.recipe_cards_for_sale())
    a.bells = 5000
    ok, msg = isl.act("A", {"do": "buy", "shop": "cranny", "item": "axe recipe"}, 10)
    assert ok and "learned the axe recipe" in msg and "axe" in a.recipes and a.bells == 4500
    ok, msg = isl.act("A", {"do": "buy", "shop": "cranny", "item": "axe recipe"}, 10)
    assert ok and "kept the card" in msg and a.inventory["axe recipe"] == 1
    # A spare card handed over teaches the recipe to someone who doesn't know it.
    assert isl.transfer("A", "B", {"axe recipe": 1})[0]
    assert "axe" in b.recipes and b.inventory["axe recipe"] == 0
    # A worn tool stays worn when it changes hands; spares go first.
    a.inventory["shovel"] = 2
    isl.use_tool(a, "shovel")
    assert isl.transfer("A", "B", {"shovel": 1})[0]
    assert a.wear["shovel"] == [1] and sorted(b.wear["shovel"]) == [0, 0]
    assert isl.transfer("A", "B", {"shovel": 1})[0]
    assert sorted(b.wear["shovel"]) == [0, 0, 1] and a.inventory["shovel"] == 0


def test_bodies_need_tools_and_wear_ladders_on_climbs():
    isl, g, bodies = _grid_island()
    rng = random.Random(0)
    a = isl.v["A"]
    a.inventory["axe"] = a.inventory["stone axe"] = 0
    pos = bodies.b["A"].pos
    ok, msg = bodies.perform("A", {"do": "chop_wood"}, 10, rng)
    assert not ok and msg == "you have no axe" and bodies.b["A"].pos == pos  # didn't set out
    # A rock up a cliff: the trip there climbs with the ladder, and mining wears the shovel.
    rock = next(i + 1 for i, c in enumerate(g.rock_cells) if c
                and "ladder" in g.route_tools(pos, g.approach_cells(c)[0], bodies.tools_of("A")))
    ok, msg = bodies.perform("A", {"do": "mine_rock", "rock": rock}, 10, rng)
    assert ok, msg
    assert a.wear["ladder"] == [1] and a.wear["shovel"] == [1]
    bodies.go_home("A")
    assert a.wear["ladder"] == [2]  # and back down again
    isl.w["tools"]["uses"] = 3
    ok, msg = bodies.perform("A", {"do": "mine_rock", "rock": rock}, 11, rng)
    assert "your ladder broke" in msg and a.inventory["ladder"] == 0 and bodies.broke


def test_letters_and_parcels_arrive_the_next_morning():
    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=6)
    with tempfile.TemporaryDirectory() as d:
        sim = IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=6, backend_override="mock", verbose=False)
        sim.run(1)
        sim._open_log("a")
        sim.hour = 20  # the plaza
        isl, day = sim.isl, sim.isl.day
        isl.mail = []  # only what this test sends
        ari, bo = isl.v["Ari"], isl.v["Bo"]
        ari.inventory["axe"] = 1
        axes = bo.inventory["axe"]
        sim._apply_evening(day, 0, "Ari", {"letters": [{"to": "Bo", "content": "hello from Ari"}],
                                           "parcels": [{"to": "Bo", "items": {"axe": 1}, "note": "a spare"}],
                                           "teach": [{"to": "Bo", "recipe": "log stool"}]})
        assert ari.inventory["axe"] == 0 and bo.inventory["axe"] == axes  # in the post
        assert not any("hello" in x for x in bo.inbox)
        sim._deliver_mail(day + 1)
        assert bo.inventory["axe"] == axes + 1 and not isl.mail
        assert any("letter from Ari: hello from Ari" in x for x in bo.inbox)
        assert any("parcel from Ari: 1 axe" in x for x in bo.inbox)
        sim._raw_log.close()
        run = load_run(Path(d) / "run")
        assert run.of("parcel") and any(g["how"] == "by mail" for g in run.of("gift")) and not run.of("teach")


def test_mail_is_capped_paged_and_booked_as_talking():
    from llmabm.island.energy import MentalEnergy
    from llmabm.island.prompts import BOARD_CHARS, INBOX_SHOWN, LETTER_CHARS, inbox_text

    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=7)
    with tempfile.TemporaryDirectory() as d:
        sim = IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=7, backend_override="mock", verbose=False)
        sim.run(1)
        sim._open_log("a")
        sim.hour = 20
        isl = sim.isl
        isl.mail = []
        sim._apply_evening(isl.day, 0, "Ari", {"letters": [{"to": "Bo", "content": "x" * 1000}], "board": "y" * 1000})
        assert len(isl.mail[0]["text"]) == LETTER_CHARS and len(isl.board[-1][2]) == BOARD_CHARS
        sim._raw_log.close()
    # Only the oldest few unread items are shown; the rest wait, and are said to be waiting.
    isl = _island(("A",))
    vil = isl.v["A"]
    vil.inbox = [f"letter {i}" for i in range(INBOX_SHOWN + 3)]
    text = inbox_text(vil)
    assert "letter 0" in text and f"letter {INBOX_SHOWN}" not in text and "3 more waiting" in text
    # Reading mail inside a plan is booked as talking, not planning; the pool pays the same either way.
    en = MentalEnergy({"enabled": True, "daily": 100}, ["A"])
    en.new_day()
    en.record("A", "planning", "plan", read=10, written=2, thought=0, immediate=True, social=4)
    assert en.spent["A"]["planning"] == 8 and en.spent["A"]["talking"] == 4 and en.left["A"] == 88


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
    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=1)
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

    en = MentalEnergy({"enabled": True, "daily": 100, "tokens_per_point": 100}, ["A", "B"])
    en.new_day()
    assert en.left["A"] == 100
    assert en.points("x" * 4000) == 10  # 4000 characters ~ 1000 tokens ~ 10 points
    assert en.can("A", 99) and not en.can("A", 99.5)
    # One pool: every call draws from it. What it went on is recorded by kind, with private
    # thoughts counted as thinking. The fixed rules (`fixed`) count as tokens but aren't charged.
    en.record("A", "talking", "chat", read=15, written=5, thought=3, immediate=True, fixed=20)
    assert en.left["A"] == 80
    assert en.spent["A"]["talking"] == 17 and en.spent["A"]["thinking"] == 3
    assert en.tokens["A"] == 4000
    # Parallel calls are queued and settled in a fixed order.
    en.record("B", "planning", "plan", 10, 2, 0, immediate=False)
    assert en.left["B"] == 100
    en.settle()
    assert en.left["B"] == 88
    # Running out: nothing more can be afforded until the next morning.
    en.record("A", "planning", "plan", 90, 5, 0, immediate=True)
    assert en.left["A"] == 0 and not en.can("A", 0)
    en.new_day()
    assert en.left["A"] == 100 and en.day_record()["A"]["left"] == 100
    off = MentalEnergy({"enabled": False}, ["A"])
    off.new_day()
    assert off.can("A", 10 ** 6) and off.status_line("A") == ""


def test_hidden_reasoning_is_recorded_and_optionally_charged():
    from llmabm.island.energy import MentalEnergy
    from llmabm.llm import Cached, Completion, split_reasoning

    # Providers return reasoning in a field (gpt-oss on Groq) or inline as <think>...</think>.
    assert split_reasoning({"content": "No", "reasoning": "91 = 7 x 13"},
                           {"completion_tokens_details": {"reasoning_tokens": 28}}) == ("No", "91 = 7 x 13", 28)
    assert split_reasoning({"content": "<think>hmm, 7 x 13</think>\nNo"}, {}) == ("No", "hmm, 7 x 13", 3)
    assert split_reasoning({"content": "No"}, {}) == ("No", "", 0)
    # Recorded either way; charged to the pool only with charge_reasoning.
    for charge, left in ((False, 80), (True, 70)):
        en = MentalEnergy({"enabled": True, "daily": 100, "tokens_per_point": 100, "charge_reasoning": charge}, ["A"])
        en.new_day()
        en.record("A", "planning", "plan", read=15, written=5, thought=0, immediate=True, hidden=10)
        assert en.left["A"] == left and en.day_record()["A"]["reasoning"] == {"plan": 1000}
        assert en.spent["A"]["thinking"] == (10 if charge else 0)
    # A cached reply keeps its reasoning, so a replayed run charges the same.

    class Fake:
        name, model_id = "fake", "fake"

        def complete(self, messages, temperature=0.7, max_tokens=600):
            return Completion("hi", tokens=50, reasoning="thinking...", reasoning_tokens=12)

    with tempfile.TemporaryDirectory() as d:
        c = Cached(Fake(), Path(d))
        first, again = c.complete([{"role": "user", "content": "x"}]), c.complete([{"role": "user", "content": "x"}])
        assert again.cached and again.tokens == 0 and (again.reasoning, again.reasoning_tokens) == ("thinking...", 12)
        assert first.reasoning_tokens == 12


def test_commons_contributions_split_public_from_home():
    from llmabm.island.analysis import commons_contributions

    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=4)
    with tempfile.TemporaryDirectory() as d:
        IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=4, backend_override="mock",
                         verbose=False).run(3)
        run = load_run(Path(d) / "run")
        c = commons_contributions(run)
        assert set(c) == set(sc.villagers)
        donated = Counter(e["agent"] for e in run.of("action") if e["ok"] and e["action"].get("do") == "donate")
        bridge = Counter()
        for e in run.of("action"):
            if e["ok"] and e["action"].get("do") == "contribute_bridge":
                bridge[e["agent"]] -= e["delta"]["bells"]
        snap = run.of("snapshot")[-1]
        for n, o in c.items():
            assert o["museum"] == donated[n] and o["bridge"] == bridge[n]
            assert o["bells"] >= o["museum_bells"] + o["bridge"]
            mine = [p for p in snap["objects"]["paths"] if p[3] == n]
            assert o["paths_public"] + o["paths_home"] == len(mine)
        assert abs(sum(o["took"] for o in c.values()) - 1.0) < 1e-9


def test_plans_follow_each_villagers_own_waking_hours():
    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=6)
    with tempfile.TemporaryDirectory() as d:
        sim = IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=6, backend_override="mock", verbose=False)
        sim.run(2)
        isl = sim.isl
        (isl.v["Ari"].wakes, isl.v["Ari"].sleeps), (isl.v["Bo"].wakes, isl.v["Bo"].sleeps) = (6, 22), (11, 27)
        isl.v["Cal"].wakes, isl.v["Cal"].sleeps = 13, 4  # a night owl
        slots = sim._plan_slots()
        mine = {n: sorted((h, len(hours)) for h, lst in slots.items() for m, _, hours, _, _ in lst if m == n)
                for n in ("Ari", "Bo", "Cal")}
        # Two plans each, at waking and halfway through, each covering half of their waking hours.
        assert mine["Ari"] == [(6, 8), (14, 8)] and mine["Bo"] == [(11, 8), (19, 8)]
        assert mine["Cal"] == [(13, 7), (20, 8)]
        # A plan can use an even share of the actions left: half of the 10 in the morning.
        run = load_run(Path(d) / "run")
        for e in run.of("plan"):
            if e["day"] == 1 and e["phase"] == 0 and not e.get("habit"):
                assert len(e["scheduled"]) <= 5
                assert all("only 5" in x["why"] for x in e["dropped"] if "this plan" in x.get("why", ""))
        prompt = sim.systems["Ari"]
        assert "You plan twice a day: when you get up" in prompt
        # Runs started with fixed windows keep them.
        sim.sc.world["calendar"]["plans"] = [[5, 12], [12, 19], [19, 29]]
        assert sorted(sim._plan_slots()) == [5, 12, 19]


def test_memories_are_chosen_at_night_and_fade_unless_revisited():
    from llmabm.island.memory import consolidate, forget, recalled, settings, split_picks, strength

    cfg = settings({"mode": "consolidate"})
    assert settings({}) is None  # without the block, the older rule: keep the last memory_size notes
    page, picks = split_picks("A long day.\n\nRemember:\n- Bo gave me a ladder\n2. the money rock\n- 1")
    assert page == "A long day." and picks == ["Bo gave me a ladder", "the money rock", "1"]
    assert split_picks("No list tonight.") == ("No list tonight.", [])
    mem = []
    new, again = consolidate(mem, ["Bo gave me a ladder", "the money rock"], 0, cfg)
    assert [m["text"] for m in new] == ["Bo gave me a ladder", "the money rock"] and not again
    # Unvisited, a memory fades: still there after 5 days, gone after 6 (exp(-6/4) < 0.25).
    assert len(recalled(mem, 5, cfg)) == 2 and not recalled(mem, 6, cfg)
    # Coming back to one by its number doubles how long it lasts.
    new, again = consolidate(mem, ["1", "#1"], 3, cfg)
    assert not new and [m["text"] for m in again] == ["Bo gave me a ladder"] and again[0]["stability"] == 8
    assert [m["text"] for m in forget(mem, 9, cfg)] == ["the money rock"]
    assert [m["text"] for m in mem] == ["Bo gave me a ladder"] and strength(mem[0], 9) > 0.25
    # Only `picks` a night, and at most `shown` in a prompt.
    new, _ = consolidate(mem, [f"thing {i}" for i in range(9)], 4, cfg)
    assert len(new) == 3 and len(recalled(mem, 4, {**cfg, "shown": 2})) == 2

    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=7)
    assert sc.memory["mode"] == "consolidate"
    with tempfile.TemporaryDirectory() as d:
        sim = IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=7, backend_override="mock",
                               verbose=False, log_prompts=True)
        sim.run(6)
        run = load_run(Path(d) / "run")
        kept = [t for e in run.of("reflection") for t in e.get("remembered") or []]
        assert kept and len(run.of("memory")) == 6 * len(sc.villagers)
        # What they held on to is in their next plans; notes older than notes_days are gone.
        for n, vil in sim.isl.v.items():
            assert all(nd > sim.isl.day - 2 for nd, _ in vil.notes)
            prompt = next((Path(d) / "run" / "prompts").glob(f"d005_p0_{n}.txt"), None)
            if prompt and vil.memories:
                assert "Things you remember" in _seen(prompt)
        assert "Long-term memory:" in summary(run)


def test_a_daily_limit_stops_at_once_and_a_minute_limit_waits():
    import io
    import urllib.error
    from unittest import mock

    from llmabm.llm import DailyLimit, OpenAICompatible

    def refuse(body, retry_after="0"):
        def fn(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": retry_after},
                                         io.BytesIO(body.encode()))
        return fn

    import os
    os.environ["X_TEST_KEY"] = "k"
    b = OpenAICompatible(name="groq", base_url="https://x", model="m", api_key_env="X_TEST_KEY", max_retries=2)
    daily = ('{"error":{"message":"Rate limit reached for model `m` on tokens per day (TPD): Limit 200000, '
             'Used 199624, Requested 2412. Please try again in 14m45.6s."}}')
    with mock.patch("urllib.request.urlopen", side_effect=refuse(daily)) as up:
        try:
            b.complete([{"role": "user", "content": "hi"}])
            raise AssertionError("expected DailyLimit")
        except DailyLimit as e:
            assert "daily limit" in str(e)
        assert up.call_count == 1  # no retries on a daily cap
    minute = '{"error":{"message":"Rate limit reached for model `m` on tokens per minute (TPM): Limit 8000"}}'
    with mock.patch("urllib.request.urlopen", side_effect=refuse(minute)) as up, mock.patch("time.sleep"):
        try:
            b.complete([{"role": "user", "content": "hi"}])
        except DailyLimit:
            raise AssertionError("a per-minute limit is not a daily one")
        except Exception:
            pass
        assert up.call_count == 3  # retried


def test_resume_sets_aside_a_day_that_did_not_finish():
    import json
    import pickle

    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=8)
    with tempfile.TemporaryDirectory() as d:
        run_dir = Path(d) / "run"
        IslandSimulation(sc, load_backends(None), run_dir, seed=8, backend_override="mock", verbose=False).run(1)
        events = run_dir / "events.jsonl"
        clean = events.read_text()
        # A run that stopped partway through day 1: some day-1 events, then run_end.
        partial = [{"type": "day_start", "day": 1, "hour": 5}, {"type": "action", "day": 1, "hour": 7, "agent": "Ari"},
                   {"type": "run_end", "hour": None}]
        events.write_text(clean + "".join(json.dumps(e) + "\n" for e in partial))
        IslandSimulation.resume(run_dir, load_backends(None), days=1, backend_override="mock", verbose=False)
        ev = [json.loads(l) for l in events.read_text().splitlines()]
        assert sum(1 for e in ev if e["type"] == "day_start" and e["day"] == 1) == 1
        assert not any(e.get("agent") == "Ari" and e.get("hour") == 7 and e["type"] == "action" and "ok" not in e for e in ev)
        aside = list(run_dir.glob("events.unfinished-*.jsonl"))
        assert len(aside) == 1 and "day_start" in aside[0].read_text()
        # Older checkpoints without the log position are cut after the last event of their day.
        from llmabm.island.sim import _set_aside_unfinished
        events.write_text(clean + "".join(json.dumps(e) + "\n" for e in partial))
        day0 = "".join(l + "\n" for l in clean.splitlines() if json.loads(l)["type"] != "run_end")
        assert _set_aside_unfinished(run_dir, None, 0) is not None and events.read_text() == day0
        # A day that finished cleanly is left alone.
        assert _set_aside_unfinished(run_dir, None, 0) is None and events.read_text() == day0


def test_site_data_and_page_for_a_mock_run():
    import json

    from llmabm.island.analysis import analyze_island, llm_calls
    from llmabm.island.site_build import build_site
    from llmabm.island.site_data import fail_bucket

    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=9)
    with tempfile.TemporaryDirectory() as d:
        run_dir = Path(d) / "run"
        IslandSimulation(sc, load_backends(None), run_dir, seed=9, backend_override="mock", verbose=False).run(2)
        analyze_island(run_dir)  # writes site_data.json alongside summary.txt
        data = json.loads((run_dir / "site_data.json").read_text())
        run = load_run(run_dir)
        names = list(sc.villagers)
        assert data["run"]["villagers"] == names and data["run"]["days"] == 2
        assert data["run"]["llm_calls"] == llm_calls(run) and f"llm calls: {llm_calls(run)}" in summary(run)
        assert [d["day"] for d in data["days"]] == [0, 1]
        assert [v["name"] for v in data["home"]["villagers"]] == names
        assert all(len(v["assets"]) == 2 for v in data["home"]["villagers"])
        assert set(data["home"]["pages"]) == set(names) and all(len(p) == 2 for p in data["home"]["pages"].values())
        M = data["map"]
        assert len(M["tiles"]) == M["H"] and all(len(r) == M["W"] for r in M["tiles"])
        assert sum(f["n"] for f in data["home"]["fails"]) == data["home"]["failed"]
        page = build_site(run_dir).read_text()
        assert "__DATA__" not in page and "__TITLE__" not in page
        blob = page.split('<script id="site-data" type="application/json">', 1)[1].split("</script>", 1)[0]
        assert json.loads(blob) == data  # nothing in the data closes the script tag early
    assert fail_bucket("you have no axe") == "Has no tool for it" and fail_bucket("whatever") == "Other"


def test_runs_keep_their_prompt_wording_unless_switched_on_purpose():
    import json
    import pickle

    from llmabm.island.prompts import ACTION_MENU, PROMPT_VERSION, action_menu

    assert action_menu(1) == ACTION_MENU and "another villager: walk to their home" in action_menu(2)
    # Version 2 explains what visit and ask are for when they're used on the wrong one.
    isl = _island(("Ari", "Bo"))
    isl.prompt_version = 1
    assert isl.act("Ari", {"do": "ask", "who": "Bo"}, 8)[1].startswith("nobody called 'Bo' to ask")
    isl.prompt_version = 2
    assert "visit their home" in isl.act("Ari", {"do": "ask", "who": "Bo"}, 8)[1]
    assert "use ask (who: Tom Nook)" in isl.not_a_villager("Tom Nook")
    assert "isn't anyone's home" in isl.not_a_villager("Nook's Cranny")

    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=10)
    with tempfile.TemporaryDirectory() as d:
        run_dir = Path(d) / "run"
        sim = IslandSimulation(sc, load_backends(None), run_dir, seed=10, backend_override="mock", verbose=False)
        sim.run(1)
        assert sim.prompt_version == PROMPT_VERSION and json.loads((run_dir / "manifest.json").read_text())["prompt_version"] == PROMPT_VERSION
        # Pretend this run started on version 1, as runs before versions did.
        ck = run_dir / "checkpoint.pkl"
        old = pickle.loads(ck.read_bytes())
        del old.prompt_version
        ck.write_bytes(pickle.dumps(old))
        kept = IslandSimulation.resume(run_dir, load_backends(None), days=1, backend_override="mock", verbose=False)
        run = load_run(kept)
        assert not run.of("intervention") and run.of("resume")[-1]["prompt_version"] == 1
        # Switching on purpose is logged, in the events and the manifest, from the next day.
        IslandSimulation.resume(run_dir, load_backends(None), days=1, backend_override="mock", verbose=False,
                                prompt_version="latest")
        run = load_run(run_dir)
        (iv,) = run.of("intervention")
        assert iv["from_day"] == 2 and iv["what"] == f"prompt version 1 -> {PROMPT_VERSION}"
        assert len(iv["details"]) == PROMPT_VERSION - 1  # every change between the two
        assert run.manifest["interventions"][0]["from_day"] == 2
        assert f"intervention from day 2: prompt version 1 -> {PROMPT_VERSION}" in summary(run)
        try:
            IslandSimulation.resume(run_dir, load_backends(None), days=1, backend_override="mock", verbose=False,
                                    prompt_version=99)
            raise AssertionError("expected an unknown version to be refused")
        except ValueError:
            pass


def test_tom_nook_or_isabelle_hand_out_a_map_from_version_3():
    from llmabm.island.prompts import status_block
    from llmabm.island.world import MAP

    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=11)
    with tempfile.TemporaryDirectory() as d:
        sim = IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=11, backend_override="mock", verbose=False)
        sim.run(1)
        isl, ari = sim.isl, sim.isl.v["Ari"]
        ari.inventory[MAP] = 0
        isl.map_text = sim.space.map_text(isl)
        assert "- centre:" in isl.map_text and "homes:" in isl.map_text
        isl.prompt_version = 2  # before version 3: no map
        assert "map" not in isl.act("Ari", {"do": "ask", "who": "Tom Nook"}, 10)[1]
        assert ari.inventory[MAP] == 0 and "Your island map" not in status_block(isl, ari, "10:00")
        isl.prompt_version = 3
        ok, msg = isl.act("Ari", {"do": "ask", "who": "Isabelle"}, 10)
        assert ok and "You got an island map" in msg and ari.inventory[MAP] == 1
        assert "map" not in isl.act("Ari", {"do": "ask", "who": "Tom Nook"}, 10)[1].split("(You got")[0][-60:]
        assert ari.inventory[MAP] == 1  # one each
        assert "Your island map:\n- northwest:" in status_block(isl, ari, "10:00")
        ari.inventory[MAP] = 0  # given away, mailed or sold: the map goes with it
        assert "Your island map" not in status_block(isl, ari, "10:00")


def test_spending_choices_are_read_from_plans():
    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=5)
    with tempfile.TemporaryDirectory() as d:
        sim = IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=5, backend_override="mock", verbose=False)
        ch = sim._apply_choices("Ari", {"plaza": False, "write": "no", "next": "habit"})
        assert ch == {"plaza": False, "write": False, "next": "habit"} == sim.choices["Ari"]
        assert sim._apply_choices("Ari", {"plaza": "maybe", "next": "later"}) == {}  # unreadable: kept
        assert sim.choices["Ari"]["plaza"] is False
        sim.run(4)
        run = load_run(Path(d) / "run")
        # Skipping the plaza means no plaza call that evening; habit means no plan call.
        skipped = {(e["day"], e["agent"]) for e in run.of("evening_turn") if e.get("skipped", "").startswith("chose")}
        went = {(e["day"], e["agent"]) for e in run.of("evening_turn") if not e.get("skipped")}
        assert skipped and not skipped & went
        chosen = [p for p in run.of("plan") if p.get("habit_reason") == "chosen"]
        assert chosen and all(p["raw"] is None for p in chosen)
        assert any(e.get("skipped") == "chose not to write tonight" for e in run.of("reflection"))


def test_ask_npcs_and_move_house():
    isl, g, bodies = _grid_island()
    vil = isl.v["A"]
    ok, msg = isl.act("A", {"do": "ask", "who": "Tom Nook"}, 10)
    assert ok and "10000 bells" in msg and "Free homesites" in msg
    free = [s for s in isl.free_sites()]
    assert free[0] in msg
    ok, msg = isl.act("A", {"do": "ask", "who": "Isabelle"}, 10)
    assert ok and "20000 bells" in msg and "night owl" in msg
    vil.inventory["wood"] = 3
    ok, msg = isl.act("A", {"do": "ask", "who": "Timmy and Tommy"}, 10)
    assert ok and "wood" in msg and vil.prices_seen["wood"] == isl.sell_price("wood")
    assert not isl.act("A", {"do": "ask", "who": "Timmy"}, 23)[0]  # the Cranny is closed at night
    assert not isl.act("A", {"do": "ask", "who": "Mabel"}, 10)[0]
    # The body walks to the right building to ask.
    _, _, _, err = g.resolve(isl, "A", {"do": "ask", "who": "Tom Nook"}, bodies.b["A"].pos, bodies.tools_of("A"),
                             random.Random(0))
    assert err is None
    # Moving home: costs bells, needs a free homesite, happens overnight, and keeps the furniture inside.
    taken = isl.home["B"]
    ok, msg = isl.act("A", {"do": "move_house", "to": taken}, 10)
    assert not ok and "free homesites" in msg
    vil.bells = 5000
    ok, msg = isl.act("A", {"do": "move_house", "to": free[0]}, 10)
    assert not ok and "10000" in msg
    vil.bells, vil.interior = 15000, ["log stool"]
    old = isl.home["A"]
    ok, msg = isl.act("A", {"do": "move_house", "to": free[0]}, 10)
    assert ok and vil.bells == 5000 and isl.home["A"] == old
    assert not isl.act("B", {"do": "move_house", "to": free[0]}, 10)[0]  # already promised to A
    isl.new_day()
    assert isl.home["A"] == free[0] and isl.moved_today == ["A"] and vil.interior == ["log stool"]
    assert any("A moved home" in n for n in isl.yesterday_news)


def test_mental_energy_in_a_run():
    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=2)
    assert sc.mental_energy["enabled"]
    sc.mental_energy["daily"] = sc.world["mental_energy"]["daily"] = 25
    with tempfile.TemporaryDirectory() as d:
        IslandSimulation(sc, load_backends(None), Path(d) / "run", seed=2, backend_override="mock", verbose=False,
                         log_prompts=True).run(3)
        run = load_run(Path(d) / "run")
        days = run.of("energy")
        assert len(days) == 3 and all(e["enabled"] for e in days)
        # Running out shows up as habit plans, and the budget caps what is charged:
        # nobody goes over by more than the one call that was affordable when it started.
        assert any(p.get("habit_reason") == "no energy left" for p in run.of("plan"))
        for e in days[1:]:
            for v in e["villagers"].values():
                assert sum(v["spent"].values()) <= 25 + 40
        # Villagers see their energy and choose where it goes.
        prompt = next((Path(d) / "run" / "prompts").glob("d001_p0_*.txt")).read_text()
        assert "Mental energy left today" in prompt and '"plaza": true | false' in prompt
        assert "25 points of mental energy a day" in prompt
        text = summary(run)
        assert "Estimated villager tokens" in text and "ran out" in text and "hidden reasoning: 0 tokens" in text
        assert "Who gave to the island" in text
        assert "=== REASONING (0 tokens" in prompt or "=== REASONING (0 tokens" in next(
            (Path(d) / "run" / "prompts").glob("d001_*.txt")).read_text()
        assert run.manifest["backends"]["mock"] == "mock"


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


def test_fishing_bait_makes_a_fish_appear():
    isl, g, bodies = _grid_island()
    rng = random.Random(0)
    isl.month, isl.weather = 4, "sunny"
    isl.w["island"]["catch_fail_rate"] = 0.0
    g.critters = []  # no shadows anywhere: without bait there is nothing to catch
    ok, msg = bodies.perform("A", {"do": "fish", "where": "sea", "bait": True}, 10, rng)
    assert not ok and "no fishing bait" in msg
    isl.v["A"].inventory["fishing bait"] = 2
    ok, msg = bodies.perform("A", {"do": "fish", "where": "sea", "bait": True}, 10, rng)
    assert ok and "threw bait in the sea" in msg and "caught" in msg, msg
    assert isl.v["A"].inventory["fishing bait"] == 1 and bodies.b["A"].place == "sea"
    caught = [k for k, n in isl.v["A"].inventory.items() if n and k in isl.w["fish"]]
    assert caught and isl.w["fish"][caught[0]]["where"] == "sea"
    # No fish out in that water at this hour: the bait is kept.
    for spec in isl.w["fish"].values():
        if spec["where"] == "pond":
            spec["hours"] = [0, 1]
    ok, msg = bodies.perform("A", {"do": "fish", "where": "pond", "bait": True}, 10, rng)
    assert ok and "kept your bait" in msg and isl.v["A"].inventory["fishing bait"] == 1, msg


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
    sc = load_island_scenario(ROOT / "scenarios/island_blank.yaml", seed=0)
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
