# Grid island: design spec

This is what `space: grid` builds, in `src/llmabm/island/grid.py`. Settings live under
`grid:` in `data/island_world.yaml` and can be overridden per scenario.

Lines marked **ASSUMPTION** are from memory of ACNH, or are simplifications I chose.
They are the ones to check and correct.

## Layout

- **Size: 112 x 96 tiles.** ASSUMPTION: ACNH islands are 7 x 6 acres of 16 x 16 tiles. The
  size is a setting (`width`, `height`); 256 x 256 works, though villagers will be spread
  thinner and meet less often.
- **Sea margin** of 7 tiles, a **beach** 3 tiles wide, and grass inside it.
- **Cliff tiers**: 3 levels of grass, getting higher toward the north. Tier 3 is a band in
  the middle of the north. The tier boundaries are smooth, noisy lines.
- **River**, 2 tiles wide. It starts on the top tier, runs south, and splits into two
  branches that reach the sea at the south-west and south-east. It keeps the tier of the land
  it runs through, so the upper part is a "clifftop" river.
- **Rocky shore**: a stretch of the north beach, in line with the river's source, can't be
  walked. ASSUMPTION: without it the beach is a way around the river, and ACNH coasts have
  rocky stretches like this.
- **Pond**: one, on the lower tier, away from the river.
- **Buildings** (all on the lower tier, sizes in tiles): Resident Services 6x4, museum 6x4,
  Nook's Cranny 5x4, Able Sisters 5x4, airport 6x4. The plaza is 7x5 and can be walked on.
  Each building has a door tile in front of it. ASSUMPTION on sizes and rough placement.
- **Homesites**: 14 plots of 5x4, at least 9 tiles apart, all on the lower tier. Villagers
  pick one on arrival. ASSUMPTION: ACNH also allows homes on cliffs; keeping homes on the lower
  tier means nobody needs a ladder just to get home.

## Objects

- **Trees and rocks** take one tile each and block walking.
- **Spacing.** ASSUMPTION: a tree or rock needs all 8 neighbouring tiles free of other trees,
  rocks and buildings. It can't touch water or sit on a cliff edge.
- **Counts** come from `island:` (30 hardwood trees, 10 fruit trees, 6 rocks). ACNH islands
  usually have more trees; raise `hardwood_trees` if you want denser woods.
- **Rocks** keep their tiles and refill to 8 hits every morning. One random rock a day is the
  money rock.
- **Wild flowers**: 6 small patches at the start, owned by nobody (`wild_flower_patches`).
- **Daily spawns**: shells on random beach tiles, and fossil dig spots on random grass tiles.
- **Planting** puts a sapling on the nearest tile that satisfies the spacing rule, within 25
  steps of the villager.

## Building and changing the island

Mechanics live in `world.py` (inventory, rules) and `grid.py` (tiles). Settings are under
`building:` in the world file.

| action | what happens |
|---|---|
| `lay_path` from, to, style | lays path tiles along the walking route between two places, up to 30 per hour, starting at the "from" end and skipping tiles already paved. Repeat to continue. Paths don't change walking speed (ACNH); `path_step_cost` below 1 makes roads faster. |
| `remove_path` near | removes your own path tiles near a place |
| `place` item, near, count | puts furniture or fence pieces outside on free tiles nearest the place, within 12 tiles. Furniture can be 1x1, 2x1 or 2x2 (`items:` sizes). Placed things block walking. |
| `fence_off` item, around home | fences the ring around your home plot, leaving the door open, using as many pieces as you carry |
| `pick_up` item, near, count | takes back your own placed things (`owner_only_pickup`) |
| `plant` seeds, near | plants flower seeds on a free grass tile; they bloom after `flower_bloom_days` |
| `plant` fruit or sapling, near | plants a sapling near the place, following the tree spacing rule; it grows in 5 days |
| `chop_down_tree` kind, near | fells the tree for 2 wood and leaves a stump that blocks its tile |
| `dig_stump` near | removes a stump and frees the tile |
| `break_rock` rock | needs a fruit (eaten). The rock disappears; after `rock_respawn_days` a rock appears at a random valid tile. ASSUMPTION: breaking gives no materials. |
| `wear` clothing | changes outfit. Others see it when they notice you nearby. |

Places villagers can name: `here`, `home`, `<villager>'s home`, any building, `plaza`, `rock N`.

**Catalog** (`items:`, `recipes:`, `shops:`, `clothing:` in the world file). Everything is
approximate and editable:
- **Tools:** ladder, vaulting pole.
- **Furniture:** 15 recipes and 4 shop items, with sizes and whether each can go outside.
- **Fences:** simple wooden, stone and iron, 5 pieces per craft.
- **Flower seeds:** 4 kinds, two sold at the Cranny each day.
- **Saplings:** always sold at the Cranny.
- **Clothing:** placeholders to replace with your own designs, each with a price, slot and
  description.

Everyone knows the ladder, vaulting pole and simple wooden fence recipes from day 0.

## Movement

- **Walking** is 4-directional over beach, grass and bridges, with one step per tile.
- **Ladder**: climb or descend one tier between adjacent tiles, costing 3 steps. There are no
  inclines yet.
- **Vaulting pole**: jump a straight line of up to 3 river tiles onto land at the same level,
  costing the number of tiles plus 2. Ponds and the sea can't be crossed. ASSUMPTION on the
  maximum width.
- **Bridge**: funding the bridge project opens a short crossing over the lower river near
  Resident Services.
- **Tool recipes** are known to everyone from the start: ladder (4 wood, 4 hardwood,
  4 softwood) and vaulting pole (5 wood). ASSUMPTION on the costs. Tools are crafted at home
  and never break.
- **Time.** An action still takes one hour wherever it is. Walking distance only decides who
  gets somewhere first within the hour, and whether a place can be reached at all.

## Bodies (no LLM)

The mind schedules an intent with an hour. The body then picks a concrete target:

| intent | target |
|---|---|
| mine a rock | the named rock, or the nearest reachable rock with hits left |
| chop wood, shake a tree | nearest reachable tree that still has wood or fruit today |
| fish (sea, river, pond, clifftop) | a fish shadow in sight in that water (a preferred `size` first); if none, up to 3 shore spots out of sight of each other, nearest first, until one shows a shadow |
| catch bugs (`where`: flowers, trees, stumps, rocks, ground) | a bug in sight in that habitat (a preferred `species` first); if none, up to 3 nearby flowers, trees, stumps or rocks (or grass spots for ground) to look from |
| visit someone | the door of their home |
| beachcomb, dig | the nearest shell or dig spot the villager has seen today; if none, up to 3 beach or grass spots not yet looked at today (or near the place they name) |
| shops, museum, Nook, Isabelle | the building's door, if reachable |
| craft, rest | home |

- If nothing suitable can be reached with the villager's tools, the action fails and says
  why. The mind sees that in its next plan.
- **Free time** (`free_time: wander`): each idle, awake hour a villager stays home with
  probability `wander_home_chance` (0.5), and otherwise takes a 12-step random walk on their
  current level. Once a villager is more than 30 tiles from home, each step heads back toward
  home. With `free_time: home`, idle villagers stay home.
- **Roads**: a wanderer on or next to a path steps onto a path tile with probability
  `road_bias` (0.8), and keeps going the same way along the road when it can. Off the roads
  the walk is random as before. ASSUMPTION: this is how ACNH villagers seem to behave.

## Sight

- **Radius**: villagers see 8 tiles around them (`sight_radius`), with no line of sight.
  ASSUMPTION: roughly what fits on screen in ACNH.
- **Bugs** are objects on the map: on a bloomed flower, a tree, a stump, a rock, or a grass
  tile. Each hour, bugs whose hours, months or weather are over leave, and new ones appear up
  to `bugs_out` per habitat (fewer when a habitat is scarce), picked by rarity from what is out
  at that time.
- **Fish shadows** sit on water tiles next to the shore, up to `fish_out` per kind of water. A
  shadow has a size (tiny, small, medium, large, very large, huge; `shadow` in `fish:`,
  approximate ACNH sizes). The species is only known once caught.
- A bug or fish that gets away is gone from that spot.
- **Shells and fossil dig spots** are placed each morning as before, but a villager only knows
  the ones they have seen today. Looking happens at the end of every hour, wherever an awake
  villager is (acting, wandering or at home), and when they go looking for shells or dig spots.
- **What they spotted** today goes into their prompts (the last 8 things), with a rough place
  ("lower ground, southwest, near rock 1"), so they can go back or tell others. Places the mind
  can name now include parts of the island: "north beach", "south-west", "centre".
- If nothing turns up, the hour is spent looking. When nothing of that kind is out anywhere on
  the island at that time, the villager is told so, which is how seasons and hours can be learned.

## Seasons and weather

- **Calendar**: the run starts in April (`start_month`). A month is `days_per_month` island days
  (10 by default, so a year is 120 days; set it to 30 for real-length months). Seasons follow
  the northern hemisphere: spring 3-5, summer 6-8, autumn 9-11, winter 12-2.
- **Weather** is drawn each morning from the season's odds (`weather:`): sunny, cloudy or rain,
  and snow in winter. Rain and snow count as wet. Villagers see the date and weather in every
  prompt and in Isabelle's announcement.
- **What it changes**: only what can be caught. Each fish and bug has `months` and a `weather`
  of any, dry or wet. Most butterflies and bees need dry weather; snails and the coelacanth
  need wet weather. ASSUMPTION: months are approximate ACNH northern months. Walking, shops and
  everything else are the same in the rain, so whether villagers change their days is something
  to measure (the summary has time use by weather).

## Homes, visits, gifts and favors

- **Interiors**: `decorate` moves furniture from your pockets into your home, up to the
  house level's `interior` slots (6, 12, 18, 30, 40). `undecorate` takes it out again.
- **Visits**: `visit` walks to someone's door. If they are awake, at home and not already
  busy, they let you in: you see their furniture and the two of you talk (the same chat as an
  encounter, marked as a visit). If not, nobody answers, and the host later hears you came by.
- **Gifts**: in any conversation, a villager can hand over items or bells ("give"). Letters
  that ask for nothing back are gifts too.
- **Favors**: in a conversation, a villager can ask for something ("ask"). The request stays
  open and shows in both villagers' status until the other gives at least that much, in person
  or by letter. There is no deadline and no penalty; whether they keep track is up to them.

## What villagers know

They know the island's layout: buildings, regions, and where the 6 rocks are, with how many
steps each one is from them or whether it is out of reach with their tools. That is roughly the
in-game map. They don't know which rocks have been hit today, or who is where, unless they see
them.

## Encounters

At the end of each hour, villagers who are out (acting away from home, or wandering) and
within 6 tiles of each other notice each other (`encounters.grid_radius`). After that, the
notice, decide, talk flow runs. Meetings along the walking path within an hour are not counted.

## Not modelled yet

- flower hybrids, watering, and flowers spreading
- furniture that does anything once placed (inside or out)
- villagers inviting others over, or more than two people in a home
- terraforming
- custom design patterns for paths and clothing

Also not modelled:

- inclines and terraforming
- more than one bridge
- weeds and beach palms
- events (fishing tourney, bug-off, birthdays), seasonal items, and snow on the ground
- fish shadow sizes and fish that need bait
- homes on cliffs
- walking from the airport on arrival
- tools wearing out
- the stalk market

## Questions for you

1. Is 112 x 96 right, or do you want more room?
2. Tree and rock spacing: does ACNH really need all 8 neighbours clear, or only the 4 sides?
3. Ladder and pole costs, and should villagers have to unlock the recipes rather than know them
   from day 0?
4. Rock behaviour: should broken rocks (eating fruit and hitting them) be possible, and do new
   rocks spawn somewhere else?
5. Which fish should be river-mouth-only, and should bugs depend on trees, flowers and rocks?
6. Should the sea margin hold a pier, for pier fishing?
7. Month length: keep 10 days a month (a year in 120 days), or real 30-day months?
8. Should rain change anything besides spawns (for example, fewer villagers out, or shells)?
9. Sight radius and spawn counts: ACNH shows more fish and bugs at once. Too many and seeing
   stops mattering; too few and most tries come up empty.
