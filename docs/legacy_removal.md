# Removing the old prompt versions (deferred)

Written during the code quality audit of 27 November 2026. Nothing here has been done yet.

The code keeps every prompt version from 1 to 22 so that a run replays in the wording it started with.
Once a baseline version is established and `runs/test1` is archived, the versions below the baseline
are dead weight: about 74 `prompt_version >= N` checks, several whole code paths, and the tests that
pin them. This is the list of what goes, so it can be done in one pass.

## When

After the baseline is chosen and no run that will be resumed is on a version below it. `runs/test1`
is on version 21, so until it is archived the floor is 21, not 22.

## Before starting

1. Pick the floor version `F` (21 while `runs/test1` is live, otherwise the baseline).
2. Make `llmabm resume` refuse a checkpoint below `F` with a clear message, instead of failing deep
   inside a removed branch.
3. Take a seeded mock run at every version from `F` up and keep its events and prompt files: the
   removal must leave them byte-identical.

## Source to remove

| Where | What | Was for |
|---|---|---|
| `prompts.py` | `ACTION_MENU`, `_MENU_CHANGES`, `action_menu` | the free-form action menu, below 16 |
| `prompts.py` | `_announcement_v1` and the `< 5` branch of `announcement_text` | announcements below 5 |
| `prompts.py` | `_board_turn_prompt`, `evening_prompt`, the `< 12` body of `plaza_prompt` | evening rounds and the plaza below 12 |
| `prompts.py` | `trade_table_text_v13`, `_trade_act_text` (keep the `_v18` ones), `exchange_line`'s old wording | trade wording below 18 |
| `options.py` | `options_text`, `read_picks` | numbered picks, 16 to 20 |
| `sim.py` | habit and the plaza/`next` choices (`choices`, the `v < 7` branches of `_plan`) | below 7 |
| `sim.py` | `_new_offer`, `_execute`, `Offer`, `self.offers`, the offers part of `_apply_evening` | plaza offers, below 12 |
| `sim.py` | `_backfill_memory`, `_notes_in_prompts`, `_section` | the one-time switch from 19 to 20 |
| `sim.py` | `_PerVillager`, `_per_villager`, `_MOVED`, `_REBUILT` and the properties built on them | checkpoints from before the Villager class |
| `memory.py` | `recalled`, `memory_lines`, `consolidate`, `forget` (keep `settings`, `strength`) | the fading memory model, 12 to 19 |
| `mock.py` | the free-form planner in `plan_reply` and `_picks` | mock replies below 16, and 16 to 20 |
| `world.py` | every `self.prompt_version >= N` and `< N` check with `N <= F` | rules that changed below the floor |
| `grid.py`, `body.py`, `npc.py`, `villager_files.py` | every `version_of(...)` check with `N <= F` | the same |
| `grid.py`, `options.py` | the `near`, `from`/`to` and `fence_off` building branches | building before 22 (only once `F` is 22) |
| `world.py` | `PROMPT_CHANGES` entries below `F` stay as history; `changelog.yaml` is not touched | |

After the removal, `space.version_of` and `Island.prompt_version` stay: versions above the floor
will keep arriving.

## Tests to remove with it

Whole tests (names as of this writing):

- `test_spending_choices_are_read_from_plans` (version 6)
- `test_from_version_6_the_plaza_is_opt_in` (versions 5 and 6)
- `test_version_16_reads_picks_by_number` (16 to 20)
- `test_switching_a_run_to_retrieval_fills_in_the_days_it_already_lived` (the 19 to 20 switch)
- `test_prompt_version_8_shows_what_you_have_and_what_changed_hands` (pinned to 17 and 7). First move
  the "Tools you carry / Other things you carry" checks into a test at the latest version.
- `test_prompt_version_12_what_villagers_know_and_who_did_what` (pinned to 12). First move the checks
  on `tools_for_sale` prices, the `_do_buy` messages, `keep_chat` and requests lapsing.
- `test_announcement_from_version_5_is_short_and_ends_with_a_fun_fact` (3 to 5). Rewrite it at the
  latest version rather than delete it: the current list of fun facts has no test.
- `test_memories_are_chosen_at_night_and_fade_unless_revisited` (the fading model)

Blocks inside tests that stay: every `isl.prompt_version = N` or `_at_version(N)` block with `N`
below the floor, and the checks on `action_menu`, the version 1 and 2 wording of `ask`, the version
3 `chop_down_tree`, one pile for pockets and home (11), no structures (16), and checkpoints from
before the Villager class or without a log position. The two trade tests
(`test_trading_in_person_...`) are pinned to the version 17 wording: move them to the latest
wording instead of deleting them.

Also goes: the `_at_version` helper once nothing uses it, and the members kept only for those tests
(`IslandSimulation.choices`, `noticed`, `talk_systems`).

## Checks after

- The full test suite, and the seeded mock runs from "Before starting" unchanged.
- `ruff check .` and `mypy` clean.
- `grep -rn "prompt_version\|version_of" src` shows only checks above the floor.
- `docs/actions.md`, `docs/world.md` and `docs/day_flow.md` regenerated and read for any mention of a
  removed version.
