# llm-abm

An agent-based model where the agents are LLMs. An agent's "rules" are its persona
prompt, the documents it carries, a few numeric traits, and a short private memory.
Agents sit on a network, exchange messages in discrete ticks, and every message
records which earlier messages it was based on, so you can trace where information
came from and how it changed on the way.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Offline, no keys. The mock backend forwards messages with small random edits.
llmabm run scenarios/class_suspension.yaml --backend mock --seed 0

# Real models. Copy .env.example to .env and add a key first.
llmabm run scenarios/class_suspension.yaml --backend groq --seed 0
```

Each run writes to `runs/<timestamp>_<scenario>_s<seed>/`:

| file | what it is |
|---|---|
| `events.jsonl` | every seed, turn (raw LLM reply included), message, drop and probe answer |
| `manifest.json` | the exact agents, traits, network and scenario used, for reproducibility |
| `summary.txt` | reach, drift by hop, probe answers over time, error counts |
| `reach.png`, `drift.png`, `probes.png` | the same, as plots |
| `viewer.html` | step through ticks: who knows what, which messages moved, what each agent believed |

`llmabm trace runs/<dir> rumor` prints the cascade tree for one seed.
`llmabm analyze runs/<dir>` regenerates the outputs, for example after you edit the analysis code.

## Concepts

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

**Populations.** A scenario can instantiate a template many times and sample traits
per instance (`{uniform: [0, 1]}`, `{normal: [mu, sd]}`, or a list to choose from).
This is where the ABM-style heterogeneity lives.

**Tick protocol.** Updates are synchronous. At tick t an agent sees what was delivered
by the end of t-1 plus any seeds injected at t, along with its contacts, current
beliefs and notes. It replies with JSON: updated beliefs, an optional private note,
and messages to contacts with `based_on` ids. Messages sent at t are read at t+1.
Agents can only message their network neighbors.

**Seeds** are exogenous information injected at a tick to chosen agents. They are the
roots of provenance.

**Probes** are private survey questions asked to every agent at chosen ticks with a
fixed answer set. They do not enter memory or messages, so they work as a
measurement of belief state, something like an order parameter.

**Provenance.** A message's origin is the set of seeds reachable by following
`based_on` links. Exposure means an agent has read something descended from a seed.
Drift is text similarity (difflib ratio) between a message and its seed, by hop count.

## Layout

```
agents/              persona files; agents/templates/ for population templates
docs/                documents agents carry
scenarios/           network scenarios (population + network + seeds + probes) and island scenarios
villagers/           island villager template and cast
data/island_world.yaml  island resources, species, recipes, shops, houses
config/models.yaml   LLM backends (all OpenAI-compatible)
src/llmabm/
  agent.py           file format, prompt construction, reply parsing
  scenario.py        loading, trait sampling, network generation, placement
  world.py           the tick loop and event log
  llm.py             backends, rate limiting, retries, disk cache, mock
  analysis.py        provenance, exposure, drift, probes, plots, viewer data
  viewer_template.html
  island/            world rules, villager prompts, sim loop, analysis, mock policy
tests/
```

## Free tiers and completely free runs

All backends use the OpenAI chat-completions format, so any compatible provider works by
adding an entry to `config/models.yaml`. The config includes Groq, Gemini, Cerebras,
Mistral, NVIDIA, OpenRouter and Ollama.

The only truly free option is a local model through Ollama: no key, no rate limit, no data
leaving your machine. You pay with hardware time instead. An 8B model runs on a recent laptop
with 16 GB of RAM, slowly.

The hosted free tiers are free but capped, and the caps change often. Some free tiers
(Gemini outside the EU, Mistral's experiment tier) may use your prompts for training.
Check model names and limits before a real run.

Keep one model per experimental condition. Spreading villagers across providers to stay under
rate limits makes "which model" a hidden variable. If a run won't fit one provider's daily
cap, split it across days: the cache means a rerun with the same seed replays the finished
part for free.

Cost per run is roughly (agents acting per tick) x ticks, plus agents x probe ticks.
The example scenario is 16 agents and 12 ticks, which is on the order of 100 to 250
calls. `rpm` in the config spaces requests to stay under a rate limit, and 429s are
retried with backoff. `--concurrency N` runs agents within a tick in parallel.

Responses are cached in `.cache/llm/`, keyed on the prompt and the run seed. Re-running
the same seed replays for free; a new seed resamples. Use `--no-cache` to force fresh
calls. `--log-prompts` saves every full prompt, which is the fastest way to see what an
agent actually saw.

Small free models often return malformed JSON. Those turns are logged as errors with
the raw reply, and the agent does nothing that tick. Check the error count in
`summary.txt` before reading anything into a run.

## Things to know before trusting results

- `based_on` is self-reported by the model. It can omit a source it actually used or
  cite one it did not. Ids the agent never saw are dropped and counted as
  `unverifiable based_on ids`. A content-based attribution check would be a good
  addition if provenance matters for a result.
- LLM agents share a training distribution, so they are less heterogeneous than the
  traits suggest. Varying models across agents (`model:` per agent) is one lever.
- Outputs depend on prompt wording as much as on traits. Treat prompt variants as
  experimental conditions and run several seeds per condition.
- Seeds and agent names are visible to agents. If you do not want a label to cue
  behavior (for example "official advisory"), change the seed's `source`.

## Island scenarios (Animal Crossing: New Horizons style)

A second kind of world, for cooperation as well as information flow. Ten villagers
share an island with limited, renewable resources.

```bash
llmabm run scenarios/island_baseline.yaml --backend mock --days 10
llmabm run scenarios/island_goals.yaml --backend groq --seed 0
```

**Architecture: mind, body, world, space.**
- **Mind** (LLM, `prompts.py` and the calls in `sim.py`): plans intents with hours, thinks
  privately, and talks. It never handles coordinates or pathfinding.
- **Body** (`body.py`, one per villager, no LLM): position, tools and walking. It turns an
  intent into a trip to a specific place, decides who arrives first within an hour (the
  shorter walk from where each villager currently is), and goes home for free time and sleep.
- **World** (`world.py`): resources, the shops and NPCs, and the rules that resolve an action
  once the body is there.
- **Space** (`space.py`): where things are and how far apart. `PlaceSpace` is the current
  10x10 map of named places with straight-line distances. A tile grid with cliffs and rivers
  (needing a ladder or a pole) will be another `Space`, without changes to the mind or the
  world rules.

**Initial conditions.** Every villager starts in a tent with 1,000 bells, empty pockets,
no loan and 3 random DIY recipes. The island starts with full rocks, trees, beach and dig
spots, an empty museum, an unfunded bridge and no ordinance.

**Arrivals and homesites (day 0).**
- `arrivals.mode: trickle` (the default) lands each villager at a random hour between
  05:00 and 18:00 on day 0.
- `together` lands everyone at 05:00, and they choose one after another in random order.
- On arrival a villager makes one call: pick a homesite from the free ones, then plan the
  rest of that window. The prompt shows the island map and who already lives where.
- There are 12 sites in 7 areas (`map` in the world file). A taken or invalid choice falls
  back to the nearest free site.

**Where you live matters in two ways.**
- **Proximity.** When villagers go to the same place in the same hour, whoever lives closer
  gets there first. Equal distances are shuffled.
- **Neighbors.** Villagers in the same area notice each other heading out, and everyone sees
  who else was at the same place in the same hour. That gives information a spatial channel:
  you can learn that Dom clears the rocks at 06:00 by being there or living next to him.

**Encounters.** Three steps, as in ACNH:
1. **Notice (no LLM).** After each hour, bodies check who is within `encounters.radius` of
   whom. On the current map that means the same place; on a grid it will be tiles.
2. **Decide (a small LLM call).** Each villager who notices someone gets a light prompt:
   persona, goals, recent notes, today so far, and who is nearby. They choose whether to
   approach one of them. Approaching includes the opening line. These calls are about 300
   tokens, against about 2,000 for a planning call.
3. **Talk.** Approaches are taken in random order. Conversations are one on one and each
   villager is in at most one per hour. The approached villager replies with full context,
   for one more call per turn (`turns`, default 2, up to 4). Either side can stop by saying
   nothing.

Chats cost no action hour. Only the two villagers hear them, and they go into both villagers'
record of the day. Caps: `max_decisions_per_villager_per_day` (default 4) and
`max_chats_per_villager_per_day` (default 2). With the defaults, encounters add roughly 25
calls a day, most of them the cheap decision calls. Every decision is logged: who noticed whom,
whether they approached, and their private reasoning.

**The map.** There are two kinds of space:
- `space: grid` (the default) is an ACNH-style tile island, 112x96 by default: a beach, three
  cliff tiers, a river from a clifftop source to two mouths, a pond, the buildings, 14
  homesites, and trees and rocks on single tiles with spacing rules.
- `space: places` is the older map of named points.

On the grid, getting around is gated. Cliffs need a ladder and the river needs a vaulting
pole, until the bridge is funded. Everyone knows both tool recipes from the start but has to
gather the wood and craft them. Bodies pathfind with the tools the villager actually carries,
and pick the nearest reachable rock, tree, shore or building for each intent. Prompts tell
each villager where the rocks and buildings are, and how many steps away each one is or that
it's out of reach.

Fish depend on the kind of water: sea, river, pond, or clifftop river (char and golden trout).
Bugs depend on habitat: flowers, trees, stumps, rocks or open ground. A villager says where to
look, and the body walks to the nearest bloomed flower, tree, stump or rock. The island starts
with a few patches of wild flowers.

The full design, with the rules I wasn't sure of marked, is in `docs/grid_spec.md`.
`docs/island_grid.png` shows the island for seed 0. Each analyzed grid run draws
`island_map.png` and `tracks_day_NNN.png`, showing where every villager was, hour by hour, on
the last day.

**Changing the island.** On the grid, villagers can do these things, each taking one action hour:
- lay roads between two places (dirt, stone, brick, sand or terracotta), and remove their own
- place furniture and fences outside, fence off their home plot, and pick their own things back up
- plant trees and flowers
- chop trees down to a stump, and dig the stump out
- eat a fruit and break a rock, which reappears elsewhere a few days later
- change clothes, which other villagers see
- put furniture inside their home (`decorate`), up to a limit that grows with the house
- visit another villager's home

Placed things block walking, so fences and furniture can wall off space. The catalog of items,
recipes, shop stock and clothing is in `data/island_world.yaml`. The clothing entries are
placeholders for your own designs. Details are in `docs/grid_spec.md`.

**Free time.** With `free_time: wander`, an idle villager spends each hour at home half the
time, and otherwise takes a short random walk, as ACNH villagers do. Once there are roads,
wanderers follow them most of the time (`road_bias`). `free_time: home` keeps them home. This changes how often villagers meet. Mock
runs over 5 days and 3 seeds gave these average counts of villager pairs within sight of each
other:

| setup | pairs |
|---|---|
| named places (everyone at the same point) | 136 |
| grid, idle at home | 45 |
| grid, wandering | 72 |

**Sight.** On the grid, villagers see about 8 tiles around them. Bugs and fish shadows are
objects on the map that come and go by the hour. A villager can only go after one they can see,
or one they find by walking to where those creatures live and looking around. A fish shows only
its shadow size until it's caught. Shells and fossil dig spots are only known once seen. Each
villager's prompt lists what they spotted today, so a rare bug can be shared or kept quiet.
`summary.txt` reports how often a try came up empty and how much each villager spotted. Bugs
come up empty most often in April because few of the 16 bug species are out then.

**Seasons and weather.** The run starts on April 1, and a month is 10 island days by default
(`calendar.days_per_month`). Each morning brings sunny, cloudy, rain or, in winter, snow,
drawn from the season's odds. Villagers see the date and weather in their prompts. The weather
only changes what can be caught: each fish and bug has months and a dry, wet or any weather
setting (butterflies need dry days, snails and the coelacanth need rain). Nothing else changes
in the rain, so whether villagers care is an open question. `summary.txt` compares time use
and catch rates by weather to answer it.

**Visits, gifts and favors.**
- A villager can `visit` someone's home. If the host is awake, at home and free, they let the
  guest in: the guest sees the furniture inside and they talk. Otherwise nobody answers, and
  the host later hears that the guest came by.
- In any conversation (a visit or a chance meeting), either villager can hand the other
  something ("give") or ask for something ("ask").
- A request stays open, shown to both, until the other gives at least that much, in person or
  by letter. Nothing enforces it.

**Privacy.** Every villager reply has a `thoughts` field and an optional `note`. Both are
private: villagers never see each other's. You see the thoughts in the logs, marked 🔒.
Villagers are told that others only learn what they choose to say, what they are seen doing,
and public records (museum, board, bridge fund, Isabelle's announcements). So deception and
discretion are possible. The tests check that no villager's thoughts appear in any other
villager's prompt.

**Isabelle's report.** At the end of each day an extra LLM call, as Isabelle, reads a compact
record of the day and writes a 150 to 300 word report to `days/day_NNN_isabelle.md`. The
report also appears at the top of that day's log. The record covers actions, conversations
and trades, but not private thoughts, and the report is for you, not the villagers.

`isabelle.announce: true` also has her write the next morning's announcement from public
information only. That puts an LLM between events and what villagers hear, so treat it as an
experimental condition of its own.

**Checkpoints and resume.** After every day the whole simulation is saved to
`checkpoint.pkl` in the run directory. To continue:

```bash
llmabm run scenarios/island_goals.yaml --run-dir runs/live --days 1   # day 0
llmabm resume runs/live                                                # day 1, 2, ...
llmabm resume runs/live --days 3 --backend cerebras                    # three more, another model
```

A resumed run is identical to an uninterrupted one with the same seed; a test checks this.
Code and prompt changes take effect from the next resumed day, which lets you adjust the
experiment as it goes. Note any changes you make, since they become part of the run's
history. Only load checkpoints you made: pickle files can run code when loaded.

**One island day per real day.**
- `scripts/daily.sh` starts the run if it doesn't exist and otherwise resumes it by one day.
  It's configured with environment variables (RUN_DIR, SCENARIO, SEED, BACKEND).
- `.github/workflows/island-daily.yml` runs that script at 05:00 Manila time (21:00 UTC).
  Your API key lives as a repository secret, and the workflow commits each new day back to
  the repo. Setup steps are at the top of the file.
- On your own machine, a cron line does the same:
  `0 5 * * * cd /path/to/llm-abm && scripts/daily.sh >> runs/daily.log 2>&1`

**A 24-hour day.** As in ACNH, the day rolls over at 05:00, which is when resources refill.
Each action takes one in-game hour, and each villager has energy for 10 actions a day.

Each villager plans three times a day: at 05:00, 12:00 and 19:00. Each plan covers the hours
until the next one, and an action carries the clock `hour` it should happen at. A villager
who is asleep for a whole window, or already out of energy, is not called, which saves
requests.

The engine steps hour by hour. Villagers acting in the same hour resolve in a shuffled
order, so an early start beats a late one and ties are luck.

Opening hours apply. Nook's Cranny is open 08:00 to 22:00, and fish and bugs have
ACNH-style hour windows. Villagers who are awake meet at the plaza at 20:00 and 21:00 for
letters, the board, trades and teaching recipes.

**Schedules.** Each villager in `villagers/cast.yaml` has `wakes` and `sleeps` clock hours,
loosely following their personality type in ACNH (lazy villagers go to bed at 23:00, cranky
and sisterly ones stay up until 03:00). Sleep is a hard constraint: an action scheduled
while asleep is dropped. Each villager also has a `routine` line, a soft preference for when
they like to be out or at home. The model sees it and nothing enforces it, so the summary
reports when each villager actually acted.

**Isabelle's ordinances** are a rule, like Tom Nook. Any villager can pay her 20,000 bells at Resident
Services to set the island ordinance for everyone, starting the next morning. The options:
- Early Bird: everyone's hours and the shop hours shift 2 hours earlier.
- Night Owl: everyone's hours and the shop hours shift 2 hours later.
- Bell Boom: Cranny pays 20% more and shop prices rise 20%.

One villager can change everyone else's schedule. Each morning, every villager's first plan
starts with Isabelle's announcement: the current ordinance and yesterday's public news
(donations, house upgrades, bridge progress, ordinance changes).

All of this is configurable under `calendar`, `hours` and `ordinances` in the world file,
and a scenario can override the calendar.

**Shared resources** (all in `data/island_world.yaml`):
- 6 rocks with 8 materials each per day. A single villager can clear every rock.
- One random money rock per day.
- Wood per tree per day; fruit that regrows over 3 days.
- A limited daily supply of shells and fossil spots, and limited open plots for planting.
- Trees that can be chopped down permanently.

**Fish and bugs** come from rarity tables that depend on location and time of day.

**NPCs are rules, not agents, in the baseline:**
- Tom Nook: house upgrades on an interest-free loan that must be repaid before the next upgrade.
- Timmy and Tommy: buy at fixed prices and sell a small daily stock, including one recipe card.
- Able Sisters: clothes.
- Blathers: takes the first donation of each species.

**A bridge** costs 98,000 bells, can be funded by anyone, and opens more rocks and trees for everyone.

**Partial observability.** Villagers only see what they did themselves, what they are told,
and public state (the museum, the board, the bridge fund, shop stock). They learn
Cranny prices by selling. Recipes are knowledge a villager holds and can teach.

**Conditions.**
- `island_baseline.yaml` gives no goals.
- `island_goals.yaml` adds a shared wish for a full museum, plus one personal goal per
  villager sampled from a pool (or set per villager under `goals.personal`).
- `world_facts: perturbed` renames every species, jitters prices and reshuffles fish
  locations, so the models cannot fall back on what they remember about ACNH.

**Villagers.** All ten share `villagers/villager.md`. Only the traits in
`villagers/cast.yaml` differ: species, personality type and a one-line temperament.
Swap in original names before anything public; real names also cue the model's memory
of those characters.

**Outputs.**
- `summary.txt`:
  - who took each resource, with a concentration index (HHI)
  - net worth and its Gini
  - museum completeness, donors, and how often a villager sold a species the museum still lacked
  - bridge contributors
  - trades and the median price relative to Nook's Cranny
  - recipes taught
  - each villager's action mix
  - what hour the rocks, shells and fossil spots typically run out
  - when each villager acted across the day, to compare against their routine
  - the history of ordinances and who set them
  - weather: days of each kind, time use and catches per attempt by weather
  - visits (knocks and let in), gifts and favors per villager, and the most frequent gift pairs
- `days/day_NNN.md`: one log per day, hour by hour. It records every action with its location and
  result, the state changes it caused, and every message, offer, trade and teaching. After each
  hour it shows the island's resources and who had free time; at the end of the day, a table of
  everyone's state.
- `hourly_villagers.csv`: one row per villager per hour, with activity, location, bells, loan,
  items and house.
- `hourly_island.csv`: one row per hour, with rock hits left, wood, fruit, shells, fossil spots,
  plots, trees, museum and bridge fund.
- `networth.png`, `extraction.png`, `museum.png`, `commons.png`
- `activity_by_hour.png`: the island's daily rhythm.
- `events.jsonl`: every event with its day and hour. Each action has a `delta` (bells, loan, items,
  recipes, house) and a `pools_delta` (the public resources it changed). There is an `hour_state`
  snapshot every hour.

**Cost.** Up to 3 plan calls per awake villager per day, the plaza rounds, and one Isabelle call: roughly 35
to 45 calls and 50 to 60k input tokens per in-game day with the default cast. A 30-day run is
about 1,200 calls and 1.5 to 2 million tokens.

**Mental energy (a token budget).** Turn it on with `mental_energy: {enabled: true}` in a
scenario. Each villager gets `daily` points every morning (150 by default; a point is about 100
tokens read or written, counted as characters / 4) and divides them between three kinds of
thinking:
- planning: the plan calls
- thinking: the private thoughts and notes in every reply, and the nightly reflection
- talking: deciding whether to approach someone, each line in a conversation, and the plaza

Everything a villager reads counts, including its system prompt, so a plan costs about 30
points and a line in a conversation about 18. Villagers see what they have left and the typical
costs. Each time they plan they can re-divide what is left, and the split they last chose is how
the next day's energy is divided, so a talker or a planner can emerge.

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

`summary.txt` reports estimated tokens per villager in every run, and with energy on, how
each villager split and spent it and how often they ran out. `traits_daily.csv` adds the share
spent talking and the number of habit plans. Reflections skipped for lack of energy leave gaps
in the self-ratings, which is a result in itself but worth knowing when comparing runs.

## Emergent personality: blank villagers

`scenarios/island_blank.yaml` starts ten identical villagers. They share one persona
("you have just moved to this island"), have neutral names, no species, no personality type,
no goals, and the same sleep schedule. The world facts are perturbed, so ACNH knowledge doesn't
steer them. The only asymmetries at the start come from the island: arrival order, the homesite
each one picks, which side of the river they end up on, luck, and who they meet.

**Reflection.** Each night, one call per villager rewrites a private, first-person description
of who they are, at most 150 words. From the next day it sits in their prompts under "Who you
are, in your own words". That is the feedback loop through which differences can grow. The same
call has them rate eight fixed statements from 1 to 5 (sharing, keeping to themselves, planning,
enjoying talk, trust, caring about the island, risk, getting upset). The items are configurable
under `reflection.items`. The self-description is private: other villagers never see it, and a
test checks this. Reflection works with any cast: turn it on in another scenario to let named
villagers drift from their starting personality.

**Measuring emergence.** Personality here means stable differences between villagers in what
they do, so behaviour comes first and self-report second. Analysis of a run with reflection
writes:
- `traits_daily.csv`: per villager per day, 22 behavioural metrics (time use, rock share,
  places visited, distance walked, how often they approach someone they notice, chats,
  letters, gifts, visits made and hosted, favors asked and done, furniture inside, donations, bridge money, trades) and the self-ratings.
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

Cost: one reflection call per villager per night, about 1,000 tokens each.

## Extending

- New scenario: copy `scenarios/class_suspension.yaml`, swap agents, seeds and probes.
- New network: add a case in `scenario._build_graph`, or use `type: explicit` with an
  `edges` list.
- New measurement: everything is in `events.jsonl`; `analysis.load_run` gives you the
  events and manifest to work from.
- Directed networks, agents joining or leaving mid-run, broadcast channels (one-to-many
  pages), and agents rewriting their own docs are natural next steps and not built yet.
# multiagent-island
