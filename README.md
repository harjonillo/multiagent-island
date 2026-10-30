# multiagent-island

Agent-based models where the agents are LLMs. The main model is an island in the style of
Animal Crossing: New Horizons (ACNH): ten villagers share limited, renewable resources, plan
their days hour by hour, run into each other, talk, trade, give gifts and change the island.
The second model is simpler: agents on a network passing information, with every message
traced back to where it came from.

The Python package is `llm-abm` and the command is `llmabm`.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # or pip install -e ".[dev]" for pytest

# Offline, no keys. The mock backend gives random but valid replies.
llmabm run scenarios/island_baseline.yaml --backend mock --days 3
llmabm run scenarios/class_suspension.yaml --backend mock --seed 0

# Real models. Put the key in .env first, e.g. GROQ_API_KEY=...
llmabm run scenarios/island_goals.yaml --backend groq --seed 0
```

| command | what it does |
|---|---|
| `llmabm run <scenario>` | run a scenario. `--seed`, `--days` (or `--steps`), `--backend`, `--concurrency N`, `--no-cache`, `--log-prompts`, `--run-dir`, `--no-analyze` |
| `llmabm resume <run_dir>` | continue an island run from its last checkpoint. `--days`, `--backend` |
| `llmabm analyze <run_dir>` | rebuild the summary, plots and logs, for example after you edit the analysis code |
| `llmabm trace <run_dir> <seed_id>` | print the cascade tree for one seed (network model only) |

Runs are written to `runs/<timestamp>_<scenario>_s<seed>/`.

## The island

### Architecture: mind, body, world, space

- **Mind** (the LLM; `prompts.py` and the calls in `sim.py`): plans intents with hours,
  thinks privately and talks. It never handles coordinates or pathfinding.
- **Body** (`body.py`, one per villager, no LLM): position, tools and walking. It turns an
  intent into a trip to a specific place, decides who gets there first within an hour, and
  takes the villager home for free time and sleep.
- **World** (`world.py`): resources, shops, NPCs, and the rules that resolve an action once
  the body is there.
- **Space** (`space.py`, `grid.py`): where things are and how far apart they are. `GridSpace`
  is the tile island (the default); `PlaceSpace` is an older map of named points.

### A day on the island

The day runs on a 24-hour clock and rolls over at 05:00, when resources refill.

| time | what happens |
|---|---|
| 05:00 | new day: resources refill, weather is drawn, shops restock, a pending ordinance takes effect. Each awake villager plans 05:00–11:00, starting with Isabelle's announcement |
| 05:00–04:00 | the engine steps hour by hour. Each scheduled action takes one hour. Villagers with nothing scheduled are at home or wandering |
| 12:00 | second plan, for 12:00–18:00 |
| 19:00 | third plan, for 19:00 until 05:00 |
| 20:00, 21:00 | the plaza: letters, the bulletin board, trade offers and recipe teaching |
| end of day | snapshot, optional nightly reflection, Isabelle's report, checkpoint |

- Each villager has **10 actions a day** (`calendar.actions_per_day`). A plan places each
  action at the hour it asks for, or the next free hour in that window. Actions that don't
  fit, or fall while the villager is asleep, are dropped and logged.
- Villagers who are asleep for a whole window, or out of actions, are not called.
- **Sleep is a hard constraint.** Each villager has `wakes` and `sleeps` hours in the cast
  file, and a `routine` line, which is a soft preference the model sees but nothing enforces.
- **Who gets there first.** Actions in the same hour are taken in order of how far each
  villager has to walk from where they are. Equal distances are shuffled. Whoever gets to a
  rock, tree, shell or dig spot first takes what is there.

**Day 0: arrivals.** Every villager starts in a tent with 1,000 bells, empty pockets, no
loan and three random DIY recipes, plus the starter recipes everyone knows (ladder, vaulting
pole, simple wooden fence). With `arrivals.mode: trickle` (the default) each villager lands
at a random hour between 05:00 and 18:00; with `together` everyone lands at 05:00 and they
choose one after another in random order. On arrival a villager makes one call: pick a
homesite from the free ones, then plan the rest of that window. A taken or invalid choice
falls back to the nearest free site.

### Environmental conditions

All of these are in `data/island_world.yaml` and can be edited. A scenario can override the
calendar.

**Calendar, seasons and weather.**

| setting | default |
|---|---|
| start date | April 1 (`calendar.start_month: 4`) |
| month length | 10 island days (`calendar.days_per_month`), so a 30-day run sees the season change. Set 30 for a real-time year |
| seasons | spring Mar–May, summer Jun–Aug, autumn Sep–Nov, winter Dec–Feb (northern hemisphere) |
| weather | one per day, drawn from the season's odds |

| season | sunny | cloudy | rain | snow |
|---|---|---|---|---|
| spring | 45% | 25% | 30% | |
| summer | 55% | 20% | 25% | |
| autumn | 45% | 30% | 25% | |
| winter | 35% | 30% | | 35% |

Weather only changes what can be caught. Each fish and bug has active months and a dry, wet
or any weather setting: butterflies need dry days; snails and the coelacanth need rain or
snow. Nothing else changes in the rain, so whether villagers care is an open question.
`summary.txt` compares time use and catch rates by weather.

**The map.** `space: grid` (the default) is a tile island, 112 x 96 by default, generated
from the seed:
- a sea margin, a beach three tiles wide, and grass on three cliff tiers that rise toward
  the north
- a river from a clifftop source that splits into two mouths, a pond, and a rocky stretch
  of the north beach that can't be walked
- the buildings, all on the lower tier: Resident Services, the plaza (walkable), the museum,
  Nook's Cranny, Able Sisters and the airport
- 14 homesites of 5 x 4 tiles on the lower tier, named by region (for example "lower ground,
  south-west")
- 30 hardwood trees, 10 fruit trees and 6 rocks, each on a single tile with spacing rules, and
  6 patches of wild flowers

Getting around is gated. Changing cliff tier needs a **ladder** (one tier at a time), and
crossing the river needs a **vaulting pole** (up to 3 water tiles) until the bridge is
built. The pond and the sea can't be crossed. Everyone knows both recipes from the start but
has to gather the wood and craft them. Bodies pathfind with the tools the villager carries,
and each villager's prompt says which rocks and buildings are within reach and how many steps
away. Placed furniture and fences block walking, so villagers can wall off space.

`space: places` is the older map: 13 named places on a 10 x 10 square with straight-line
distances, and 12 homesites in 7 areas. Building actions (paths, placing furniture, fences,
flower seeds) need the grid.

The full design, with the rules I wasn't sure of marked, is in `docs/grid_spec.md`.
`docs/island_grid.png` shows the island for seed 0.

**Sight.** On the grid, villagers see about 8 tiles around them. Bugs and fish shadows are
objects on the map that come and go by the hour, up to a set number per habitat. A villager
can only go after one they can see, or find by walking to where those creatures live and
looking around. A fish shows only its shadow size (tiny to huge) until it's caught. Shells
and fossil dig spots are new every morning and are only known once seen. Each prompt lists
what the villager spotted today, so a rare bug can be shared or kept quiet.

**Shared resources.** Everything refills at 05:00 unless noted.

| resource | supply | notes |
|---|---|---|
| rocks | 6 rocks, 8 items each per day | one `mine_rock` takes everything left in a rock: stone 50%, clay 20%, iron nugget 25%, gold nugget 5% |
| money rock | one random rock per day | pays up to 6,750 bells instead of materials. Nobody knows which until someone hits it |
| hardwood trees | 30 trees, 3 wood each per day | a mix of wood, softwood and hardwood |
| fruit trees | 10 trees, 3 apples each | regrow 3 days after being shaken bare |
| shells | 12 per day on the beach | 4 per `beachcomb`. 15% chance of a message bottle with a recipe you don't know |
| fossils | 4 dig spots per day | |
| fish | 19 species in the sea, river, pond and clifftop river | by hour, month and weather. 15% of tries get away |
| bugs | 16 species on flowers, trees, stumps, rocks and the ground | by hour, month and weather. 15% of tries get away |
| planting | grid: anywhere the spacing rules allow; places map: 15 open plots | saplings and fruit grow into trees in 5 days; flower seeds bloom in 3 |
| trees cut down | permanent | leaves a stump until someone digs it out |
| broken rocks | come back elsewhere after 3 days | breaking one takes eating an apple first |

With `world_facts: perturbed`, every species gets an invented name, prices are jittered by
0.6 to 1.6x, and fish locations and hours are reshuffled, so models can't fall back on what
they remember about ACNH.

**Buildings, NPCs and opening hours.** The NPCs are rules, not agents (except Isabelle's
writing, below).

| place | who | hours | what it does |
|---|---|---|---|
| Resident Services | Tom Nook | all day | house upgrades on an interest-free loan with no deadline; the loan must be paid off before the next upgrade. Takes bridge contributions |
| Resident Services | Isabelle | all day | sets the island ordinance for 20,000 bells; makes the morning announcement |
| Nook's Cranny | Timmy and Tommy | 08:00–22:00 | buys almost anything at a fixed price (shop goods and clothes at a quarter of their price). Sells saplings, 2 kinds of flower seeds, 3 rotating items and one DIY recipe card (2,000 bells) each day |
| Able Sisters | the Able Sisters | 09:00–21:00 | 4 pieces of clothing a day, from `clothing` in the world file. The entries are placeholders for your own designs |
| museum | Blathers | all day | takes the first donation of each fish, bug and fossil species. No bells for it |
| plaza | | 20:00, 21:00 | the evening rounds |

**Houses.**

| level | storage | furniture inside | loan |
|---|---|---|---|
| tent | 0 | 6 | 0 |
| house | 80 | 12 | 98,000 |
| bigger house | 120 | 18 | 198,000 |
| house with back room | 240 | 30 | 348,000 |
| two-story house | 320 | 40 | 548,000 |

Everyone carries 40 items in their pockets; storage adds to that.

**The bridge** costs 98,000 bells, anyone can chip in, and it's built the moment it is
funded. It adds 3 rocks and 10 hardwood trees, and on the grid it is a way across the river
without a pole.

**Ordinances.** One at a time, paid for by any villager, effective the next morning, for
everyone.

| ordinance | effect |
|---|---|
| Early Bird | everyone's hours and the shop hours shift 2 hours earlier |
| Night Owl | everyone's hours and the shop hours shift 2 hours later |
| Bell Boom | Nook's Cranny pays 20% more, and shop prices are 20% higher |
| none | lifts the current ordinance |

One villager can change everyone else's schedule.

### What villagers know

Every villager's system prompt has their persona, the island rules, the action menu and
(in the goals scenario) their goals. Each planning call adds:
- the date, weather, ordinance, their bells, loan, home, neighbors, inventory, clothes,
  furniture and open requests
- the recipes they know and the Cranny prices they have seen (they learn prices by selling)
- the museum, the bridge fund and today's shop stock
- their private notes (the last 15), yesterday's actions and today's so far
- what they noticed today: neighbors heading out, someone who knocked while they were out
- unread letters, what they spotted, and on the grid, where they are, what tools they carry
  and what they can reach

**Privacy.** Every reply has a `thoughts` field and an optional `note`. Both are private:
villagers never see each other's. You see the thoughts in the logs, marked 🔒. Villagers are
told that others only learn what they choose to say, what they are seen doing, and public
records (museum, board, bridge fund, Isabelle's announcements). So deception and discretion
are possible. A test checks that no villager's thoughts appear in any other villager's prompt.

### Agent actions

Each action takes one hour of the day's 10 and carries the clock hour it should happen at,
for example `{"hour": 7, "do": "mine_rock"}`. A `<place>` can be `"here"`, `"home"`,
`"<villager>'s home"`, a building, `"rock <n>"` or, on the grid, a part of the island such as
`"north beach"`.

**Gathering**

| action | parameters | what it does |
|---|---|---|
| `mine_rock` | `rock` (optional) | take what a rock has left today |
| `chop_wood` | | take today's wood from the nearest tree that has some |
| `shake_tree` | | take the fruit from a fruit tree |
| `fish` | `where`: sea, river, pond (clifftop also works); `size` (optional) | go after a fish shadow |
| `catch_bug` | `where`: flowers, trees, stumps, rocks, ground; `species` (optional) | go after a bug |
| `beachcomb` | `near` (optional) | collect shells; sometimes a message bottle |
| `dig_fossil` | `near` (optional) | dig up a fossil spot |

**Making and trading**

| action | parameters | where | what it does |
|---|---|---|---|
| `craft` | `recipe` | home | use a known recipe; fences make 5 pieces |
| `sell` | `items: {item: qty}` | Nook's Cranny | sell at the fixed price |
| `buy` | `shop`: cranny or able; `item` | the shop | buy from today's stock, or the day's recipe card |
| `donate` | `item` | museum | first of each species only |
| `wear` | `item` | home | others see what you wear |

**Home and money**

| action | parameters | where | what it does |
|---|---|---|---|
| `upgrade_house` | | Resident Services | upgrade now, on a loan for the cost |
| `pay_loan` | `bells` | Resident Services | |
| `contribute_bridge` | `bells` | Resident Services | |
| `set_ordinance` | `ordinance`: early_bird, night_owl, bell_boom, none | Resident Services | pay Isabelle 20,000 bells; starts tomorrow |
| `decorate` / `undecorate` | `item` | home | put furniture in or take it out; visitors see it |
| `visit` | `who` | their home | see the next section |
| `rest` | | home | |

**Changing the island** (grid only, except planting trees)

| action | parameters | what it does |
|---|---|---|
| `plant` | `item`: apple, sapling or flower seeds; `near` | a new tree, or flowers that bloom in 3 days |
| `chop_down_tree` | `kind`: hardwood or fruit; `near` | leaves a stump and 2 wood |
| `dig_stump` | `near` | clears the stump |
| `break_rock` | `rock` | eat an apple first; the rock is gone and turns up elsewhere in 3 days |
| `lay_path` | `style`: dirt, stone, brick, sand, terracotta; `from`, `to` | up to 30 tiles an hour; repeat to continue. Paths don't change walking speed by default |
| `remove_path` | `near` | your own path tiles only |
| `place` | `item`, `near`, `count` | put furniture or fences outside, within 12 tiles of the landmark |
| `fence_off` | `item`, `around: "home"` | fence your home plot, leaving the door open |
| `pick_up` | `item`, `near`, `count` | take back things you put outside |

Only the villager who placed something can pick it up or remove it (`owner_only_pickup`). An
action that fails (closed shop, rock mined out, out of reach, pockets full) still uses the
hour, and the villager sees why.

**Free time.** With `free_time: wander`, an idle villager spends each hour at home half the
time, and otherwise takes a short random walk, as ACNH villagers do. Once there are roads,
wanderers follow them most of the time (`road_bias`). `free_time: home` keeps them home. This
changes how often villagers meet. Mock runs over 5 days and 3 seeds gave these average counts
of villager pairs within sight of each other:

| setup | pairs |
|---|---|
| named places (everyone at the same point) | 136 |
| grid, idle at home | 45 |
| grid, wandering | 72 |

### Interactions between villagers

**Without talking.**
- **Competition.** Rocks, trees, shells and dig spots are first come, first served, and a
  shorter walk wins within the hour.
- **Being seen.** Villagers within sight of each other in the same hour are told who else
  was there. Villagers whose homes are in the same area notice each other heading out ("your
  neighbor Bo headed to the rocks"). So you can learn that someone clears the rocks at 06:00
  by being there or by living next to them.
- **Public goods and records.** The museum, the bridge fund, the bulletin board, house
  upgrades and ordinances are public, and Isabelle's morning announcement repeats yesterday's
  public news.
- **Shaping space.** Paths, fences and furniture outside change where everyone can walk.

**Encounters** (no action hour), in three steps as in ACNH:
1. **Notice** (no LLM). After each hour, bodies check who is within 6 tiles of whom (on the
   places map, at the same place). Homes don't count.
2. **Decide** (a small LLM call, about 300 tokens). Each villager who notices someone
   chooses whether to approach one of them, and if so, their opening line.
3. **Talk.** Approaches are taken in random order. Conversations are one on one, each
   villager is in at most one an hour, and the other villager replies with full context.
   There are 2 utterances by default (`turns`, up to 4). Either side can stop by saying
   nothing.

Caps per villager per day: 4 decisions and 2 chats. Only the two villagers hear a chat, and
it goes into both villagers' record of the day.

**Gifts and requests.** In any conversation, either villager can hand the other something
from their pockets (`"give"`) or ask for something (`"ask"`). A request stays open, shown to
both, until the other gives at least that much, in person or by letter. Nothing enforces it.

**Visits.** `visit` is an action. If the host is awake, at home and not busy, they let the
guest in: the guest sees the furniture inside and they talk, with gifts and requests as
above. Otherwise nobody answers, and the host later hears that the guest came by.

**The plaza** at 20:00 and 21:00 (no action hour). In the first round every awake villager
gets a call. The second round only includes villagers with new offers waiting. A villager
can:
- write letters to anyone, which arrive in their inbox
- post on the bulletin board, which everyone sees (the last 8 posts)
- offer a trade (`give` and `want`, with `"bells"` as an item for money); an offer with an
  empty `want` is a gift by mail
- accept, reject or counter offers waiting for them. Unanswered offers expire after the
  next evening. An accepted trade fails if either side no longer has the goods or room
- teach a recipe they know, which the other villager learns immediately

### Isabelle

At the end of each day an LLM call as Isabelle reads a compact record of the day (actions,
conversations and trades, not private thoughts) and writes a 150 to 300 word report to
`days/day_NNN_isabelle.md`. The report is for you, not the villagers. `isabelle.backend` sets
her model; `null` means the villagers' model.

`isabelle.announce: true` also has her write the next morning's announcement from public
information only. That puts an LLM between events and what villagers hear, so treat it as an
experimental condition of its own. Otherwise the announcement is a fixed template.

### Mental energy (a token budget)

Turn it on with `mental_energy: {enabled: true}` in a scenario. Each villager gets `daily`
points every morning (150 by default; a point is about 100 tokens read or written, counted as
characters / 4) and divides them between three kinds of thinking:
- planning: the plan calls
- thinking: the private thoughts and notes in every reply, and the nightly reflection
- talking: deciding whether to approach someone, each line in a conversation, and the plaza

Everything a villager reads counts, including its system prompt, so a plan costs about 30
points and a line in a conversation about 18. Villagers see what they have left and the
typical costs. Each time they plan they can re-divide what is left, and the split they last
chose is how the next day's energy is divided, so a talker or a planner can emerge. Isabelle
isn't charged.

When a part runs out:
- no planning energy: they do what they did yesterday at that time of day (habit)
- no talking energy: they don't approach anyone, only nod when spoken to, and skip the plaza
- no thinking energy: their replies keep no thoughts or notes, and they skip reflecting

`island_blank.yaml` has it on. Mock runs of 5 days with 10 blank villagers used these totals:

| daily budget | tokens | plans replaced by habit |
|---|---|---|
| off | 604k | 0 |
| 150 | 408k | 23 of 102 |
| 100 | 260k | 85 of 135 |
| 60 | 64k | almost all; nobody can afford a plan |

Reflections skipped for lack of energy leave gaps in the self-ratings, which is a result in
itself but worth knowing when comparing runs.

### Emergent personality: blank villagers

`scenarios/island_blank.yaml` starts ten identical villagers. They share one persona
("you have just moved to this island"), have neutral names, no species, no personality type,
no goals, and the same sleep schedule. The world facts are perturbed, so ACNH knowledge doesn't
steer them. The only asymmetries at the start come from the island: arrival order, the
homesite each one picks, which side of the river they end up on, luck, and who they meet.

**Reflection.** Each night, one call per villager rewrites a private, first-person description
of who they are, at most 150 words. From the next day it sits in their prompts under "Who you
are, in your own words". That is the feedback loop through which differences can grow. The same
call has them rate eight fixed statements from 1 to 5 (sharing, keeping to themselves, planning,
enjoying talk, trust, caring about the island, risk, getting upset). The items are configurable
under `reflection.items`. The self-description is private, and a test checks this. Reflection
works with any cast: turn it on in another scenario to let named villagers drift from their
starting personality.

**Measuring emergence.** Personality here means stable differences between villagers in what
they do, so behaviour comes first and self-report second. Analysis of a run with reflection
writes:
- `traits_daily.csv`: per villager per day, 22 behavioural metrics (time use, rock share,
  places visited, distance walked, how often they approach someone they notice, chats,
  letters, gifts, visits made and hosted, favors asked and done, furniture inside, donations,
  bridge money, trades) and the self-ratings.
- `villagers_env.csv`: what each villager was dealt: arrival hour, homesite, steps to Resident
  Services, and buildings and rocks reachable without tools.
- `selves/<name>.md`: each villager's self-description, day by day.
- `traits_divergence.png`: each metric by villager, with the spread across villagers. A rising
  spread means villagers are differentiating.
- `traits_profile.png`: each villager's recent profile, z-scored, behaviour beside self-ratings.
- Summary lines: how much the spread grew, whether self-ratings match behaviour, and whether the
  environment predicts behaviour (Spearman across villagers).

With 10 villagers per run, single-run correlations are hints. Run several seeds and pool the
CSVs before drawing conclusions. A useful control is the same scenario with
`reflection.enabled: false`: if villagers still differentiate, it comes from the environment and
memory, not from the self-description loop.

### Scenarios and villagers

| scenario | villagers | what's different |
|---|---|---|
| `island_baseline.yaml` | `villagers/cast.yaml` | no goals. What do they do with ten actions a day when nothing is asked of them? |
| `island_goals.yaml` | `villagers/cast.yaml` | a shared wish for a full museum, plus one personal goal per villager sampled from a pool (or set under `goals.personal`) |
| `island_blank.yaml` | `villagers/blank_cast.yaml` | identical villagers, perturbed facts, nightly reflection and mental energy on |

All three use the grid, free-time wandering, trickle arrivals and 30 days.

In the cast, all ten villagers share `villagers/villager.md`. Only the traits in
`villagers/cast.yaml` differ: species, personality type, a one-line temperament, sleep hours
loosely following their ACNH personality type (lazy villagers go to bed at 23:00, cranky and
sisterly ones stay up until 03:00), and a routine. Swap in original names before anything
public; real names also cue the model's memory of those characters.

### Outputs

Each island run directory has:

| file | what it is |
|---|---|
| `summary.txt` | see below |
| `days/day_NNN.md` | one log per day, hour by hour: every action with its location, result and the state it changed, every message, offer, trade and teaching, the island's resources after each hour, and a table of everyone's state at the end |
| `days/day_NNN_isabelle.md` | Isabelle's report |
| `hourly_villagers.csv` | one row per villager per hour: activity, location, bells, loan, items, house |
| `hourly_island.csv` | one row per hour: rock hits left, wood, fruit, shells, fossil spots, plots, trees, museum, bridge fund |
| `networth.png`, `extraction.png`, `museum.png`, `commons.png`, `activity_by_hour.png` | plots |
| `island_map.png`, `tracks_day_NNN.png` | the island, and where every villager was hour by hour on the last day (grid) |
| `events.jsonl` | every event with its day and hour. Each action has a `delta` (bells, loan, items, recipes, house) and a `pools_delta` (public resources it changed). An `hour_state` snapshot every hour |
| `manifest.json` | the scenario, world and villagers used |
| `checkpoint.pkl` | the whole simulation, saved after every day |

`summary.txt` covers:
- who took each resource, with a concentration index (HHI), and net worth with its Gini
- museum completeness, donors, and how often a villager sold a species the museum still lacked
- bridge contributors, trades and the median price relative to Nook's Cranny, recipes taught
- each villager's action mix, and when they acted across the day compared with their routine
- what hour the rocks, shells and fossil spots typically run out
- ordinances and who set them
- weather: days of each kind, time use and catches per attempt by weather
- sightings: how often a try came up empty, and how much each villager spotted
- visits, gifts and favors per villager, and the most frequent gift pairs
- estimated tokens per villager, and with mental energy on, how each villager split and spent it

### Cost

Up to 3 plan calls per awake villager per day, about 25 encounter calls (mostly the cheap
decision calls), the plaza rounds, one Isabelle call, and with reflection one call per villager
per night (about 1,000 tokens each). Without encounters that was roughly 35 to 45 calls and 50
to 60k input tokens per in-game day with the default cast.

### Checkpoints and one island day per real day

After every day the whole simulation is saved to `checkpoint.pkl` in the run directory:

```bash
llmabm run scenarios/island_goals.yaml --run-dir runs/live --days 1   # day 0
llmabm resume runs/live                                                # day 1, 2, ...
llmabm resume runs/live --days 3 --backend cerebras                    # three more, another model
```

A resumed run is identical to an uninterrupted one with the same seed; a test checks this.
Code and prompt changes take effect from the next resumed day, which lets you adjust the
experiment as it goes. Note any changes you make, since they become part of the run's
history. Only load checkpoints you made: pickle files can run code when loaded.

`scripts/daily.sh` starts the run if it doesn't exist and otherwise resumes it by one day. It
reads `RUN_DIR`, `SCENARIO`, `SEED`, `BACKEND` and `DAYS` from the environment. A cron line
runs it every morning:

```
0 5 * * * cd /path/to/multiagent-island && scripts/daily.sh >> runs/daily.log 2>&1
```

The script also mentions a GitHub Actions workflow (`.github/workflows/island-daily.yml`),
which isn't in this repo yet.

## Information-flow networks

An agent's "rules" are its persona prompt, the documents it carries, a few numeric traits,
and a short private memory. Agents sit on a network, exchange messages in discrete ticks, and
every message records which earlier messages it was based on, so you can trace where
information came from and how it changed on the way. `scenarios/class_suspension.yaml` is the
example.

**Agent = markdown file.** YAML frontmatter holds parameters, the body is the persona.
`$name` and `$<trait>` in the body are filled in at load time.

```markdown
---
attention: 5        # messages read per tick; the rest are dropped (logged)
memory_size: 10     # private notes kept, oldest dropped first
activation: 0.05    # chance of acting with an empty inbox
model: groq         # backend name from config/models.yaml
placement: hub      # optional: take one of the best-connected nodes
docs: [docs/verification-checklist.md]
traits: {skepticism: 0.5}
---
You are $name. Your skepticism is $skepticism ...
```

**Populations.** A scenario can instantiate a template many times and sample traits per
instance (`{uniform: [0, 1]}`, `{normal: [mu, sd]}`, or a list to choose from). This is where
the ABM-style heterogeneity lives.

**Networks.** `watts_strogatz`, `barabasi_albert`, `erdos_renyi`, `complete`, or `explicit`
with an `edges` list.

**Tick protocol.** Updates are synchronous. At tick t an agent sees what was delivered by the
end of t-1 plus any seeds injected at t, along with its contacts, current beliefs and notes.
It replies with JSON: updated beliefs, an optional private note, and messages to contacts with
`based_on` ids. Messages sent at t are read at t+1. Agents can only message their network
neighbors.

**Seeds** are exogenous information injected at a tick to chosen agents. They are the roots of
provenance.

**Probes** are private survey questions asked to every agent at chosen ticks with a fixed
answer set. They do not enter memory or messages, so they work as a measurement of belief
state, something like an order parameter.

**Provenance.** A message's origin is the set of seeds reachable by following `based_on`
links. Exposure means an agent has read something descended from a seed. Drift is text
similarity (difflib ratio) between a message and its seed, by hop count.

**Outputs.**

| file | what it is |
|---|---|
| `events.jsonl` | every seed, turn (raw LLM reply included), message, drop and probe answer |
| `manifest.json` | the exact agents, traits, network and scenario used, for reproducibility |
| `summary.txt` | reach, drift by hop, probe answers over time, error counts |
| `reach.png`, `drift.png`, `probes.png` | the same, as plots |
| `viewer.html` | step through ticks: who knows what, which messages moved, what each agent believed |

Cost per run is roughly (agents acting per tick) x ticks, plus agents x probe ticks. The
example is 16 agents and 12 ticks, on the order of 100 to 250 calls.

## Models, free tiers and caching

All backends use the OpenAI chat-completions format, so any compatible provider works by
adding an entry to `config/models.yaml`. The config includes Groq (the default), Gemini,
Cerebras, Mistral, NVIDIA, OpenRouter and Ollama. Keys go in `.env` (for example
`GROQ_API_KEY=...`) or your environment; the run stops early if one is missing. `mock` is
always available.

The only truly free option is a local model through Ollama: no key, no rate limit, no data
leaving your machine. You pay with hardware time instead. An 8B model runs on a recent laptop
with 16 GB of RAM, slowly.

The hosted free tiers are free but capped, and the caps change often. Some free tiers
(Gemini outside the EU, Mistral's experiment tier) may use your prompts for training. Check
model names and limits before a real run.

Keep one model per experimental condition. Spreading villagers across providers to stay under
rate limits makes "which model" a hidden variable. If a run won't fit one provider's daily
cap, split it across days: the cache means a rerun with the same seed replays the finished
part for free.

`rpm` in the config spaces requests to stay under a rate limit, and 429s are retried with
backoff. `--concurrency N` runs calls within a tick or hour in parallel. Responses are cached
in `.cache/llm/`, keyed on the prompt and the run seed. Re-running the same seed replays for
free; a new seed resamples. Use `--no-cache` to force fresh calls. `--log-prompts` saves
every full prompt, which is the fastest way to see what an agent actually saw.

Small free models often return malformed JSON. Those turns are logged as errors with the raw
reply, and the agent does nothing that turn. Check the error count in `summary.txt` before
reading anything into a run.

## Things to know before trusting results

- LLM agents share a training distribution, so they are less heterogeneous than the traits
  suggest. Varying models across agents (`model:` per agent) is one lever, at the cost of a
  hidden variable.
- Outputs depend on prompt wording as much as on traits. Treat prompt variants as
  experimental conditions and run several seeds per condition.
- Names and labels cue behaviour. ACNH villager names and species cue the model's memory of
  those characters; a seed's `source` (for example "official advisory") cues trust.
- Prices, yields and species tables are from memory of ACNH and approximate. Lines marked
  ASSUMPTION in `data/island_world.yaml` and `docs/grid_spec.md` are the ones to check.
- In the network model, `based_on` is self-reported. It can omit a source the model actually
  used or cite one it did not. Ids the agent never saw are dropped and counted as
  `unverifiable based_on ids`.

## Layout

```
agents/                 network-model personas; agents/templates/ for population templates
docs/                   documents agents carry, the grid spec and island images
scenarios/              network and island scenarios
villagers/              island villager templates and casts
data/island_world.yaml  island rules: calendar, weather, resources, species, recipes, shops, houses
config/models.yaml      LLM backends (all OpenAI-compatible)
scripts/daily.sh        start or resume an island run by one day
src/llmabm/
  cli.py                run, resume, analyze, trace
  agent.py              file format, prompt construction, reply parsing
  scenario.py           loading, trait sampling, network generation, placement
  world.py              the network tick loop and event log
  llm.py                backends, rate limiting, retries, disk cache, mock
  analysis.py           provenance, exposure, drift, probes, plots, viewer data
  viewer_template.html
  island/
    world.py            island state and the rules for every action
    sim.py              the day loop: plans, hours, encounters, visits, plaza, Isabelle, checkpoints
    prompts.py          what villagers are told, and the action menu
    body.py             position, tools, walking, who arrives first
    space.py            the Space interface and the named-places map
    grid.py             the tile island: generation, pathfinding, sight, building
    energy.py           mental energy
    traits.py           emergence metrics for reflection runs
    analysis.py         summary, daily logs, CSVs, plots
    grid_render.py      island and track images
    mock.py             offline villager policy
tests/
```

## Extending

- New island scenario: copy `scenarios/island_baseline.yaml` and change the cast, goals,
  calendar, arrivals or world file.
- New island rule: an action is a `_do_<name>` method on `Island` in `island/world.py`, plus
  a line in `ACTION_MENU` in `island/prompts.py`. If it happens somewhere specific, teach
  `GridSpace.resolve` where.
- New network scenario: copy `scenarios/class_suspension.yaml`, swap agents, seeds and probes.
- New network: add a case in `scenario._build_graph`, or use `type: explicit` with an `edges`
  list.
- New measurement: everything is in `events.jsonl`; `analysis.load_run` gives you the events
  and manifest to work from.
- Not built yet: directed networks, agents joining or leaving mid-run, broadcast channels, and
  agents rewriting their own docs.
