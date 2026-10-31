# multiagent-island

Agent-based models where the agents are LLMs. The main model is an island in the style of
Animal Crossing: New Horizons (ACNH): ten villagers share limited, renewable resources, plan
their days hour by hour, run into each other, talk, trade, give gifts and change the island.

The Python package is `llm-abm` and the command is `llmabm`.

## Quick start

```bash
conda env create -f environment.yml     # Python 3.12, the package (editable) and pytest
conda activate multiagent_island_env
```

**Check that everything runs (offline, no keys).** The mock backend gives random but valid
replies, so this tests the plumbing and shows you the outputs, not real behaviour.

```bash
llmabm run scenarios/island_blank.yaml --backend mock --days 2
```

**A short run with a real model.** The default backend is Groq (`openai/gpt-oss-20b`, with
reasoning kept low), which has a free tier.

1. Get a key at console.groq.com.
2. Create `.env` in the repo root (it is gitignored):
   ```
   GROQ_API_KEY=your-key-here
   ```
3. Run two days:
   ```bash
   llmabm run scenarios/island_blank.yaml --seed 0 --days 2 --run-dir runs/test1 --log-prompts
   ```
4. Keep going later with `llmabm resume runs/test1 --days 1`.

Always pass `--days` for a test: the scenarios default to 30. An island day for 10 villagers is
about 65 to 70 calls and 100k tokens, and later days cost more. Groq's free tier allows 8,000
tokens a minute and 200,000 a day per model, so two island days is about the most one run can do
in a real day (the second may need finishing tomorrow), and each island day takes 10 to 15
minutes because the backend paces itself to stay under the per-minute cap (`rpm` and `tpm` in
`config/models.yaml`). The progress lines show the calls and tokens so far. If a run stops on a
rate or daily limit, rerun the same command with the same seed (or `llmabm resume` it the next
day): cached replies replay the finished part for free. Small models sometimes return broken
JSON; check the error count in `summary.txt` before reading much into a run. `--log-prompts` saves exactly what
each villager saw, which is worth having on a first run.

Other providers (Gemini, Cerebras, Mistral, NVIDIA, OpenRouter, or a local model through Ollama
with no key) are in `config/models.yaml`: put their key in `.env` and pass `--backend <name>`.
See [Models, free tiers and caching](#models-free-tiers-and-caching).

**Where to look afterwards.** In the run directory:

| what | where |
|---|---|
| daily logs and conversations | `days/day_NNN.md`, hour by hour; private thoughts are marked 🔒 |
| reflections | `selves/<name>.md`, night by night, and at the end of each day log |
| Isabelle's report | `days/day_NNN_isabelle.md` |
| plans | the reasoning is in the day logs; the full scheduled and dropped lists are `plan` events in `events.jsonl` |
| everything, raw | `events.jsonl`, including each model's raw reply |
| prompts | `prompts/`, with `--log-prompts`: what each villager was shown, then the model's hidden reasoning and its reply |

`llmabm analyze <run_dir>` rebuilds the logs, summary and plots from `events.jsonl` without any
LLM calls. More under [Outputs](#outputs).

| command | what it does |
|---|---|
| `llmabm run <scenario>` | run a scenario. `--seed`, `--days` (or `--steps`), `--backend`, `--concurrency N`, `--no-cache`, `--log-prompts`, `--run-dir`, `--no-analyze` |
| `llmabm resume <run_dir>` | continue an island run from its last checkpoint. `--days`, `--backend` |
| `llmabm analyze <run_dir>` | rebuild the summary, plots and logs, for example after you edit the analysis code |

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
  is the tile island; `Space` is the interface bodies rely on.

The split keeps the LLM's tokens for decisions: anything that is bookkeeping (routes, distances,
who arrives first, what a rock gives, whether a shop is open) is done in code and never written
into a prompt. Why it is built this way, with measurements, is in
[docs/token_design.md](docs/token_design.md).

### A day on the island

The day runs on a 24-hour clock and rolls over at 05:00, when resources refill. Flowcharts of a
run, a day and an hour, with every LLM call and output file, are in
[docs/day_flow.md](docs/day_flow.md).

| time | what happens |
|---|---|
| 05:00 | new day: resources refill, weather is drawn, shops restock, a pending ordinance takes effect |
| when each villager wakes up | their first plan, for the first half of their waking hours, starting with Isabelle's announcement |
| 05:00–04:00 | the engine steps hour by hour. Each scheduled action takes one hour. Villagers with nothing scheduled are at home or wandering |
| halfway through their waking hours | their second and last plan, until bedtime |
| 20:00, 21:00 | the plaza: the bulletin board, trade offers, and letters and parcels (which arrive the next morning) |
| end of day | snapshot, optional nightly reflection, Isabelle's report, checkpoint |

- Each villager plans **twice a day on their own clock** (`calendar.plans_per_day`), so early
  risers and late sleepers get the same number of plans covering the same number of hours.
- Each villager has **10 actions a day** (`calendar.actions_per_day`). The first plan can use
  at most half of them; whatever it doesn't use carries over to the second. A plan places each
  action at the hour it asks for, or the next free hour in that window. Actions that don't
  fit, or fall while the villager is asleep, are dropped and logged.
- Villagers who are asleep for a whole window, or out of actions, are not called.
- **Sleep is a hard constraint.** Each villager has `wakes` and `sleeps` hours in the cast
  file.
- **Who gets there first.** Actions in the same hour are taken in order of how far each
  villager has to walk from where they are. Equal distances are shuffled. Whoever gets to a
  rock, tree, shell or dig spot first takes what is there.

**Day 0: arrivals.** Every villager starts in a tent with 1,000 bells, no loan, one of each
tool (shovel, axe, fishing rod, net, ladder, vaulting pole) and three random DIY recipes, plus
the simple wooden fence everyone knows. Nobody knows how to make tools yet. Everyone lands
together at 05:00, and Tom Nook has a tent ready
for each of them on a homesite drawn at random; nobody chooses. On landing day they are up
from 05:00. Later, a villager can move home with `move_house` at Resident Services for 10,000
bells (`move_house_cost` in the world file). The move happens overnight, and the furniture
inside comes along.

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

**The map.** The island is a tile grid, 112 x 96 by default, generated from the seed:
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
built. The pond and the sea can't be crossed. Everyone starts with both. Bodies pathfind with
the tools the villager carries,
and each villager's prompt says which rocks and buildings are within reach and how many steps
away. Placed furniture and fences block walking, so villagers can wall off space. Trees,
rocks, buildings (the plaza too) and homes always keep at least one free tile between them.

**Tools wear out.** Every tool breaks after 50 uses (`tools.uses`). A use is one action that
needs it (axe: `chop_wood`, `chop_down_tree`; shovel: `mine_rock`, `dig_fossil`, `dig_stump`,
`break_rock`; fishing rod: `fish`; net: `catch_bug`; looking around without finding anything
doesn't count), or one trip whose route climbs a cliff with the ladder or jumps the river with
the pole (going home counts as a trip). Without the tool, the action fails before the villager
sets out ("you have no axe"). Villagers aren't told how worn a tool is; they find out when it
breaks. A replacement takes the tool's recipe card, always for sale at Nook's Cranny for 500
bells, and materials: shovel 5 hardwood, axe 3 stone and 1 iron nugget, fishing rod 4 wood, net
4 softwood, ladder 4 wood, 4 hardwood and 4 softwood, vaulting pole 5 wood. The axe takes rock
materials and the others wood, so losing the axe or the shovel alone never leaves a villager
stuck. Spare tools can be handed over or mailed, and a worn tool stays worn.

**Recipe cards.** Recipes come on cards: the Cranny's tool cards and one other card a day,
message bottles on the beach, and other villagers. A card for a recipe you don't know is
learned on the spot and used up. A card for one you already know stays in your pockets, to
hand over in person, mail, trade or sell. There is no free copying of recipes.

The full design, with the rules I wasn't sure of marked, is in `docs/grid_spec.md`.
[docs/world.md](docs/world.md) shows the island for seed 0 with a legend, what you can reach with
each tool, and every item, recipe and price. `python scripts/world_doc.py` regenerates it.

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
| shells | 12 per day on the beach | 4 per `beachcomb`. 15% chance of a message bottle with a recipe card (never a tool) |
| fossils | 4 dig spots per day | a dug-up fossil is unidentified until Blathers assesses it: 400 bells as it is, 1,000 to 10,000 once assessed |
| fish | 19 species in the sea, river, pond and clifftop river | by hour, month and weather. 15% of tries get away |
| bugs | 16 species on flowers, trees, stumps, rocks and the ground | by hour, month and weather. 15% of tries get away |
| planting | anywhere the spacing rules allow (no tree, rock or building in the 8 tiles around, not next to water or a cliff edge) | saplings and fruit grow into trees in 5 days; flower seeds bloom in 3 |
| trees cut down | permanent | leaves a stump until someone digs it out |
| broken rocks | come back elsewhere after 3 days | breaking one takes eating an apple first |

Runs perturb these facts: every species gets an invented name, prices are jittered by 0.6 to
1.6x, and fish locations and hours are reshuffled, so models can't fall back on what they
remember about ACNH. `data/island_world.yaml` holds the unperturbed version the tables above
describe.

**Buildings, NPCs and opening hours.** The NPCs are rules, not agents (except Isabelle's
writing, below).

| place | who | hours | what it does |
|---|---|---|---|
| Resident Services | Tom Nook | all day | house upgrades on an interest-free loan with no deadline; the loan must be paid off before the next upgrade. Takes bridge contributions |
| Resident Services | Isabelle | all day | sets the island ordinance for 20,000 bells; makes the morning announcement |
| Nook's Cranny | Timmy and Tommy | 08:00–22:00 | buys almost anything at a fixed price (shop goods and clothes at a quarter of their price). Sells saplings, 2 kinds of flower seeds, 3 rotating items and one DIY recipe card (2,000 bells) each day, plus every tool's recipe card (500 bells) |
| Able Sisters | the Able Sisters | 09:00–21:00 | 4 pieces of clothing a day, from `clothing` in the world file. The entries are placeholders for your own designs |
| museum | Blathers | all day | takes the first donation of each fish, bug and fossil species and shows who gave what, with a tally per villager. No bells for it. Assesses fossils for free |
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

Villagers are told only what they can't find out by trying: the clock and planning times, the
10 actions a day, when the plaza meets, that they can talk to whoever is nearby, who runs which
building, their mental energy, and what is private. Everything else they learn on the island:
- **by trying.** What rocks, trees and the beach give, how many there are, when shops open,
  what a fish is: a failed or empty action uses the hour and says why ("Nook's Cranny is closed
  at 06:00 (open 08:00-22:00)", "there is no rock you can reach from here").
- **by asking.** `ask` an NPC at their building: Tom Nook (house upgrades and loans, moving
  home and the free homesites, the bridge), Isabelle (ordinances and their cost), Blathers (the
  museum and fossils), Timmy and Tommy (what they'd pay for what you're carrying) or the Able Sisters.
- **from each other**, at the plaza, in letters and in conversation.

What they learn lasts as long as their memory does: today's and yesterday's events, their notes
from the last 2 days, what they chose to hold on to at night (see
[Long-term memory](#long-term-memory)) and their nightly page. The full world, as described in this README, is in
`data/island_world.yaml` for you; villagers never see it.

The persona and these rules make up the system prompt. Plan calls add a compact action menu
(verbs and their fields only); conversations and the plaza leave it out. Each planning call
adds:
- the date, weather, ordinance, their bells, loan, home, neighbors, inventory, clothes,
  furniture and open requests
- the recipes they know and the Cranny prices they have seen (they learn prices by selling)
- the museum, the bridge fund and today's shop stock
- what they remember and their notes from the last 2 days (see [Long-term memory](#long-term-memory)),
  yesterday's actions and today's so far
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
| `fish` | `where`: sea, river, pond (clifftop also works); `size` (optional); `bait: true` (optional) | go after a fish shadow; with fishing bait (Nook's Cranny, 300 bells), a fish is sure to appear, though it can still get away |
| `catch_bug` | `where`: flowers, trees, stumps, rocks, ground; `species` (optional) | go after a bug |
| `beachcomb` | `near` (optional) | collect shells; sometimes a message bottle |
| `dig_fossil` | `near` (optional) | dig up a fossil spot |

**Making and trading**

| action | parameters | where | what it does |
|---|---|---|---|
| `craft` | `recipe` | home | use a known recipe; fences make 5 pieces |
| `sell` | `items: {item: qty}` | Nook's Cranny | sell at the fixed price |
| `buy` | `shop`: cranny or able; `item` (`"<recipe> recipe"` for a card) | the shop | buy from today's stock, a tool recipe card, or the day's recipe card |
| `donate` | `item` | museum | first of each species only; fossils must be assessed first |
| `assess` | | museum | Blathers identifies every unassessed fossil you carry |
| `wear` | `item` | home | others see what you wear |

**Home and money**

| action | parameters | where | what it does |
|---|---|---|---|
| `upgrade_house` | | Resident Services | upgrade now, on a loan for the cost |
| `pay_loan` | `bells` | Resident Services | |
| `contribute_bridge` | `bells` | Resident Services | |
| `set_ordinance` | `ordinance`: early_bird, night_owl, bell_boom, none | Resident Services | pay Isabelle 20,000 bells; starts tomorrow |
| `move_house` | `to`: a free homesite | Resident Services | pay Tom Nook 10,000 bells; the home moves overnight, furniture and all |
| `ask` | `who`: Tom Nook, Isabelle, Blathers, Timmy and Tommy, Able Sisters | their building | hear what they do and what it costs (open hours apply) |
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
1. **Notice** (no LLM). After each hour, bodies check who is within 6 tiles of whom. Homes
   don't count.
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
both, until the other gives at least that much, in person or by parcel. Nothing enforces it.

**Visits.** `visit` is an action. If the host is awake, at home and not busy, they let the
guest in: the guest sees the furniture inside and they talk, with gifts and requests as
above. Otherwise nobody answers, and the host later hears that the guest came by.

**The plaza** at 20:00 and 21:00 (no action hour). In the first round every awake villager
gets a call. The second round only includes villagers with new offers waiting. A villager
can:
- post on the bulletin board, which everyone sees (the last 8 posts; up to 250 characters)
- offer a trade to another villager (`give` and `want`, with `"bells"` as an item for money)
- accept, reject or counter offers waiting for them. Both villagers are at the plaza, so an
  accepted trade swaps on the spot. Unanswered offers expire after the next evening, and an
  accepted trade fails if either side no longer has the goods or room
- write letters (`letters`, up to 350 characters; longer ones are cut) and send parcels of
  items, bells, spare tools or recipe cards (`parcels`). The items leave right away, and both
  arrive the next morning. A parcel that doesn't fit in the recipient's pockets waits for them,
  and they are told.

Letters, parcel slips and trade notices wait in the villager's inbox. Each plan or plaza prompt
shows the 8 oldest unread items and says how many more are waiting; what was shown counts as
read. Oldest first means nothing is starved by newer mail.

### Isabelle

At the end of each day an LLM call as Isabelle reads a compact record of the day (actions,
conversations and trades, not private thoughts) and writes a 150 to 300 word report to
`days/day_NNN_isabelle.md`. The report is for you, not the villagers. `isabelle.backend` sets
her model; `null` means the villagers' model.

`isabelle.announce: true` also has her write the next morning's announcement from public
information only. That puts an LLM between events and what villagers hear, so treat it as an
experimental condition of its own. Otherwise the announcement is a fixed template.

### Mental energy (a token budget)

Turn it on with `mental_energy: {enabled: true}` in a scenario. Each villager gets one pool of
`daily` points every morning (150 by default; a point is about 100 tokens, counted as
characters / 4) for every call they make: plans, deciding whether to approach someone, each
line in a conversation, the plaza, and writing at night.

A call is charged for what changes from call to call (the situation in the prompt, and their
own nightly page) plus everything they write back. The fixed rules and persona in the system
prompt aren't charged: they are the same every time, and charging them would mostly measure
how often a villager re-reads the rulebook. Villagers see what they have left and the typical
cost of each kind of call. Isabelle isn't charged.

**Choosing where it goes.** Deciding whether to spend energy can't itself cost a call, so
villagers decide ahead, in each plan reply:
- `"plaza": true | false`: go to the plaza this evening
- `"write": true | false`: write the private page tonight
- `"next": "plan" | "habit"`: at the next planning time, plan again, or repeat what they did
  yesterday at that time, which is free. Habit covers one planning time; they plan again after.

Each choice keeps its value until they change it, and the prompt shows the current value. The
plaza and writing default to on, and the next plan to "plan". In the moment they also choose
whether to approach someone, how long to talk and how much to write.

**When it runs out** they can't make any call until the next morning: they follow habit instead
of planning, only nod when spoken to, skip the plaza, and don't write. Their old page stays.

**Hidden reasoning.** Reasoning models (gpt-oss on Groq, for example) think before they answer,
in text the villager never writes down. That reasoning is always recorded: the provider's
reasoning token count per villager and call type goes into each day's `energy` event and the
summary, and with `--log-prompts` the reasoning text itself is saved after each prompt. By
default it isn't charged, so a villager could deliberate at length and pay only for what it
writes. Set `charge_reasoning: true` under `mental_energy` to charge it as thinking, like the
`thoughts` field; villagers are then told that thinking things over costs energy too. How much
a model reasons is set per backend (`params: {reasoning_effort: ...}` in `config/models.yaml`),
so the backend and this switch are two settings to hold fixed, or vary, between experiments.
The manifest records exactly which model and request settings each backend name meant.

What it was spent on is still recorded by kind for analysis (planning, talking, and thinking for
private thoughts, notes and the nightly page; reading other villagers' mail counts as talking,
even inside a plan), along with what each villager chose to skip and
how often they ran out. `island_blank.yaml` has it on. Mock runs of 5 days with 10 blank
villagers:

| daily budget | villager tokens (incl. system prompts) | plans replaced by habit (ran out) |
|---|---|---|
| off | 343k | 0 of 100 |
| 150 | 318k | 0 of 101 |
| 60 | 296k | 0 of 104 |
| 30 | 201k | 23 of 109 |

Mock replies are short. Real models write longer replies and thoughts, so a budget binds sooner.
On the first day of a real run (gpt-oss-20b, 10 blank villagers, budget 150) villagers spent
33 to 76 points and nobody ran out, so 150 is generous; lower it to make the choices bite.

Talking is one of the things energy goes on: noticing someone and deciding whether to go over,
each line of a conversation, the plaza and letters. So how social a villager is (how often they
approach people, skip the plaza, write letters) is a choice with a cost, and differences in it
are one of the things that can emerge. `approach_rate`, `chats`, `messages` and `talk_energy` in
the traits measure it.

### Emergent personality: blank villagers

`scenarios/island_blank.yaml` starts ten identical villagers. They share one persona
("you have just moved to this island"), have neutral names, no species, no personality type,
no goals, and the same sleep schedule. The world facts are perturbed, so ACNH knowledge doesn't
steer them, and they are told only what they can't find out by trying. The only asymmetries at
the start come from the island: the homesite drawn for each of them, which side of the river
that is, luck, and who they meet.

**Reflection.** Each night, one call per villager gives them a private page to write on, free
form, at most 150 words (`reflection.max_words`; a length cap only). They see what happened that
day, their notes, and what they wrote the night before. The prompt gives no topics, questions or
format, and doesn't frame the page as a self-description, so whatever they choose to write about
is part of what emerges. The new page replaces the old one, and from the next day it sits in
their prompts under "What you wrote to yourself last night". That is the feedback loop through
which differences can grow. The page is private, and a test checks this. Reflection works with
any cast: turn it on in another scenario to let named villagers drift from their starting
personality.

### Long-term memory

A basic model, loosely after how human memory works. Salience comes from the villagers, not from
rules: after the nightly page, a villager can add a `Remember:` line and up to 3 short lines,
each something from the day in their own words, or the number of a memory they already have
that is still on their mind. That is the only way anything from the day lasts beyond tomorrow.

- **Forgetting curve.** A memory's strength is `exp(-days since it was last recalled /
  stability)`. New memories have a stability of 4 days, so one that is never revisited is gone
  after about 6 days (below a strength of 0.25).
- **Rehearsal.** Naming a memory again doubles its stability (4, 8, 16, 32 days...), so what a
  villager keeps coming back to lasts longer and longer.
- **Consolidation at night.** A villager who doesn't write that night, by choice or for lack of
  energy, keeps nothing new from the day.
- **What prompts show.** Up to 10 memories still above the threshold, strongest first, then in
  the order they happened. Notes written during the day last 2 days unless held on to.

Memories, what was revisited and what was forgotten overnight are in each day log and in
`events.jsonl` (`reflection` and `memory` events), and `summary.txt` counts them per villager.
The settings are under `memory:` in the scenario (`picks`, `shown`, `stability`, `threshold`,
`notes_days`). Without that block, villagers keep their last `memory_size` notes instead, which
is also what runs started before this keep when resumed.

**Measuring emergence.** Personality here means stable differences between villagers in what
they do, so it is measured from behaviour. What villagers write at night is kept as text to read
alongside, not scored. Analysis of a run with reflection writes:
- `traits_daily.csv`: per villager per day, 22 behavioural metrics (time use, rock share,
  places visited, distance walked, how often they approach someone they notice, chats,
  letters, gifts, visits made and hosted, favors asked and done, furniture inside, donations,
  bridge money, trades).
- `villagers_env.csv`: what each villager was dealt: homesite, steps to Resident Services, and
  buildings and rocks reachable without tools.
- `selves/<name>.md`: what each villager wrote to themselves, night by night.
- `traits_divergence.png`: each metric by villager, with the spread across villagers. A rising
  spread means villagers are differentiating.
- `traits_profile.png`: each villager's recent behavioural profile, z-scored.
- Summary lines: how much the spread grew, and whether the environment predicts behaviour
  (Spearman across villagers).

With 10 villagers per run, single-run correlations are hints. Run several seeds and pool the
CSVs before drawing conclusions. A useful control is the same scenario with
`reflection.enabled: false`: if villagers still differentiate, it comes from the environment and
memory, not from the nightly writing loop.

### Scenarios and villagers

`scenarios/island_blank.yaml` is the scenario: `villagers/blank_cast.yaml` with
`villagers/blank.md`, perturbed facts, the grid, free-time wandering, everyone landing together,
nightly reflection, mental energy on, and 30 days.

### Outputs

Each island run directory has:

| file | what it is |
|---|---|
| `summary.txt` | see below |
| `days/day_NNN.md` | one log per day, hour by hour: every action with its location, result and the state it changed, every letter, parcel, offer, trade and broken tool, the island's resources after each hour, and a table of everyone's state at the end |
| `days/day_NNN_isabelle.md` | Isabelle's report |
| `hourly_villagers.csv` | one row per villager per hour: activity, location, bells, loan, items, house |
| `contributions.csv` | one row per villager: what they gave to the island (museum, bridge, public paths, decor, flowers, trees, ordinances), bells and hours it cost them, gifts to others, and their share of shared resources taken |
| `hourly_island.csv` | one row per hour: rock hits left, wood, fruit, shells, fossil spots, trees, museum, bridge fund |
| `networth.png`, `extraction.png`, `museum.png`, `commons.png`, `activity_by_hour.png` | plots |
| `island_map.png`, `tracks_day_NNN.png` | the island, and where every villager was hour by hour on the last day (grid) |
| `events.jsonl` | every event with its day and hour. Each action has a `delta` (bells, loan, items, recipes, house) and a `pools_delta` (public resources it changed). An `hour_state` snapshot every hour |
| `manifest.json` | the scenario, world and villagers used |
| `checkpoint.pkl` | the whole simulation, saved after every day |

`summary.txt` covers:
- who took each resource, with a concentration index (HHI), and net worth with its Gini
- museum completeness, donors, how often a villager sold a species the museum still lacked, and
  how many fossils were assessed or sold unassessed
- who gave to the island, and at what cost to themselves: museum donations, bridge bells, paths,
  decor and flowers in public places (more than 3 tiles from their own homesite) and trees
  planted, as bells given up at Cranny prices and hours spent, next to gifts to other villagers
  and each villager's share of the shared resources they took. Ordinances are listed apart.
  Per villager in `contributions.csv`. It describes what they did, not why
- bridge contributors, trades and the median price relative to Nook's Cranny, recipes taught
- each villager's action mix, and when they acted across the day
- what hour the rocks, shells and fossil spots typically run out
- ordinances and who set them
- weather: days of each kind, time use and catches per attempt by weather
- sightings: how often a try came up empty, and how much each villager spotted
- visits, gifts and favors per villager, and the most frequent gift pairs
- estimated tokens per villager, hidden reasoning tokens (by call type and per villager, and whether
  they were charged), and with mental energy on, what each villager spent it on, chose to skip,
  and how often they ran out

### Cost

Two plan calls per awake villager per day, the encounter calls (mostly the cheap decision
calls), the plaza rounds, one call per villager per night when they write, and one Isabelle
call. For 10 villagers, the first day of a real run was 67 calls and about 100k tokens (prompts
plus replies, as the provider counted them), on both gpt-oss-20b and qwen3.8-27b:

| call | share of the day's tokens |
|---|---|
| plans | 43% |
| plaza | about 20% |
| conversations | 12% |
| noticing someone | 10% |
| nightly pages | 9% |
| Isabelle | 5% |

A plan on day 1 was about 2,200 tokens in: about 730 of rules and action menu (the system
prompt), 140 of the nightly page, and 1,370 of situation, of which yesterday's events were the
largest part (about 355), then where they are and can reach (236), the instructions (188) and
today so far (118). Later days cost more as villagers accumulate notes, mail and memories.
Mock replies are shorter than real ones, so mock runs undercount.

### Checkpoints and one island day per real day

After every day the whole simulation is saved to `checkpoint.pkl` in the run directory:

```bash
llmabm run scenarios/island_blank.yaml --run-dir runs/live --days 1   # day 0
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

`rpm` and `tpm` in the config keep under a provider's per-minute request and token caps: each
request reserves its prompt plus its reply budget, then is corrected to what the provider says
it used. 429s are retried with backoff, waiting at most 2 minutes, so a daily cap stops the run
cleanly rather than sleeping for hours.

Groq's free tier (October 2026) allows, per model, 30 requests and 8,000 tokens a minute, 1,000
requests and 200,000 tokens a day. That is about two island days a day for 10 villagers, and the
token pacing makes each island day take 10 to 15 minutes. Groq no longer serves Llama chat
models; the config uses `openai/gpt-oss-20b` (reasoning kept low) and also has `gpt-oss-120b`
and `qwen/qwen3.8-27b`, which didn't reason at all on Groq in our tests. Groq says cached prompt
tokens don't count toward its limits, but repeating the same system prompt showed no caching in
a test, so plan on paying for every token. `--concurrency N` runs calls within an hour in parallel. Responses are cached
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
- Reasoning models think in tokens nobody sees and, by default, nobody pays for. A model that
  reasons more has more uncharged deliberation per decision, so model and reasoning effort are
  conditions to hold fixed (see [Hidden reasoning](#mental-energy-a-token-budget)).
- Names and labels cue behaviour. That's why the blank villagers have short neutral names and
  the species have invented ones.
- Prices, yields and species tables are from memory of ACNH and approximate. Lines marked
  ASSUMPTION in `data/island_world.yaml` and `docs/grid_spec.md` are the ones to check.

## Layout

```
docs/                   day_flow.md (flowcharts of a run), token_design.md (why the token budget works as it does),
                        world.md and island_grid.png (generated), the grid spec
scenarios/              island scenarios
villagers/              island villager templates and casts
data/island_world.yaml  island rules: calendar, weather, resources, species, recipes, shops, houses
config/models.yaml      LLM backends (all OpenAI-compatible)
scripts/daily.sh        start or resume an island run by one day
scripts/world_doc.py    regenerate docs/world.md and docs/island_grid.png
src/llmabm/
  cli.py                run, resume, analyze
  agent.py              villager file format (frontmatter + persona)
  llm.py                backends, rate limiting, retries, disk cache, mock
  analysis.py           loading a run back from disk
  island/
    world.py            island state and the rules for every action
    sim.py              the day loop: plans, hours, encounters, visits, plaza, Isabelle, checkpoints
    prompts.py          what villagers are told, and the action menu
    body.py             position, tools, walking, who arrives first
    space.py            the Space interface
    grid.py             the tile island: generation, pathfinding, sight, building
    energy.py           mental energy
    traits.py           emergence metrics for reflection runs
    analysis.py         summary, daily logs, CSVs, plots
    grid_render.py      island and track images
    mock.py             offline villager policy
tests/
```

## Extending

- New island scenario: copy `scenarios/island_blank.yaml` and change the cast, calendar or
  world file.
- New island rule: an action is a `_do_<name>` method on `Island` in `island/world.py`, plus
  a line in `ACTION_MENU` in `island/prompts.py`. If it happens somewhere specific, teach
  `GridSpace.resolve` where.
- New measurement: everything is in `events.jsonl`; `analysis.load_run` gives you the events
  and manifest to work from.
- Not built yet: villagers joining or leaving mid-run, and villagers rewriting their own docs.
