# What villagers keep, and how they learn

Four charts: where everything a villager experiences ends up, which prompt reads what, what
happens at night, and the life of one long-term memory. Then a table of every store, the
settings, and what `runs/test1` shows so far. The run as a whole is in [day_flow.md](day_flow.md).

A villager has no memory of its own between calls. Everything it "knows" in a call is text the
simulation puts in that call's prompt, so memory here means: what is stored, for how long, and
which prompts show it.

The four charts describe prompt versions up to 19. **From prompt version 20 long-term memory is
retrieved instead**: everything is kept and each prompt shows what fits its situation. What
changes, and what stays, is in [section 5](#5-from-prompt-version-20-memory-by-retrieval).

Legend, in all four charts:
- **purple, rounded:** an LLM call
- **yellow:** a check, or a choice the villager made
- **grey cylinder:** something stored between calls
- everything else is plain code

## 1. Where a day's experience goes

Read it left to right: what happens, where it is stored, how long it lasts.

```mermaid
flowchart LR
    classDef llm fill:#e9e3fb,stroke:#6250d6,color:#1b1240
    classDef file fill:#f1f0ec,stroke:#8a8984,color:#0b0b0b
    classDef gate fill:#fdf3d6,stroke:#eda100,color:#0b0b0b

    subgraph happens["What happens during the day"]
        act["An action and its result<br/>worked or failed, and why"]
        note(["A private note<br/>the optional note field of a plan,<br/>a conversation turn or a plaza turn"]):::llm
        chat["A conversation"]
        hand["Something changes hands<br/>gift, trade, parcel, favor"]
        price["A price is heard<br/>selling, standing at the counter,<br/>or asking Timmy and Tommy"]
        card["A recipe is learned<br/>recipe card, message bottle, first clam"]
        fact["Isabelle's morning fact"]
    end

    subgraph short["Short-term: gone within days"]
        today[("Today's log<br/>one line per thing that happened")]:::file
        yest[("Yesterday's log<br/>today's log, one day later")]:::file
        notes[("Notes<br/>today and the 2 days before")]:::file
    end

    subgraph long["Long-term: kept until changed or faded"]
        page[("The nightly page<br/>replaced each night it is written")]:::file
        mem[("Long-term memories<br/>fade unless picked again")]:::file
    end

    subgraph kept["Kept by the island, never fades"]
        lastchat[("Last conversation with each villager<br/>its latest 8 lines")]:::file
        exch[("What changed hands with each villager<br/>the latest 6 shown")]:::file
        prices[("Prices heard at Nook's Cranny")]:::file
        recipes[("Recipes known")]:::file
        i101[("Island 101<br/>the same for everyone")]:::file
    end

    night(["Night: write the page<br/>and pick up to 3 things<br/>see chart 3"]):::llm

    act --> today
    chat --> today
    hand --> today
    note --> notes
    chat --> lastchat
    hand --> exch
    price --> prices
    card --> recipes
    fact --> i101
    today -- "end of day" --> yest
    yest -- "one day later" --> gone1["gone"]
    notes -- "after 2 days" --> gone2["gone"]
    today --> night
    notes --> night
    night --> page
    night --> mem
```

The only way anything from today's log or the notes lasts beyond those few days is the night
call: the villager writes it into their page, or picks it as a memory.

## 2. Which prompt reads what

```mermaid
flowchart LR
    classDef llm fill:#e9e3fb,stroke:#6250d6,color:#1b1240
    classDef file fill:#f1f0ec,stroke:#8a8984,color:#0b0b0b

    page[("The nightly page")]:::file
    mem[("Long-term memories")]:::file
    notes[("Notes")]:::file
    today[("Today's log")]:::file
    yest[("Yesterday's log")]:::file
    lastchat[("Last conversation<br/>with this villager")]:::file
    exch[("What changed hands<br/>with this villager")]:::file
    state[("Recipes known, prices heard,<br/>pockets, home, bells")]:::file
    i101[("Island 101")]:::file

    plan(["Plan<br/>up to 3 a day"]):::llm
    plaza(["Plaza turn"]):::llm
    talk(["Conversation turn"]):::llm
    snap(["Snap decisions<br/>notice someone, go after a find"]):::llm
    reflect(["Night page"]):::llm

    page -- "system prompt, every call" --> plan & plaza & talk & snap & reflect
    mem -- "up to 10" --> plan & plaza & talk
    mem -- "up to 4" --> snap
    mem -- "numbered, to pick from" --> reflect
    notes --> plan & plaza & talk & reflect
    notes -- "latest 6" --> snap
    today --> plan & plaza & talk & reflect
    today -- "latest 5 lines" --> snap
    yest --> plan
    lastchat --> talk
    exch --> talk
    exch -- "today's only" --> reflect
    state --> plan & plaza & talk
    state -- "pockets, recipes, changes since morning" --> reflect
    i101 -- "closed; opened on request, free" --> plan & plaza & talk
```

Two things follow from this chart:
- **Yesterday's log is read only when planning.** In a conversation, at the plaza and at night a
  villager sees today but not yesterday.
- **Recipes and prices are not memory.** The island keeps them and prints them in full in every
  plan, plaza and conversation prompt. From prompt version 8 each known recipe is listed with
  what it needs and how much of that the villager has.

## 3. The night

One call per villager, after the day ends. It is the only place long-term memory is written.

```mermaid
flowchart TD
    classDef llm fill:#e9e3fb,stroke:#6250d6,color:#1b1240
    classDef file fill:#f1f0ec,stroke:#8a8984,color:#0b0b0b
    classDef gate fill:#fdf3d6,stroke:#eda100,color:#0b0b0b

    start["End of day"]
    chose{"Chose to write tonight?"}:::gate
    energy{"Enough mental energy<br/>to read the prompt?"}:::gate
    nothing["Nothing is written<br/>the old page stays, no new memories"]
    call(["Night call<br/>reads: today's log, what changed hands today,<br/>what they have, notes, numbered memories, last night's page"]):::llm
    split["Split the reply at the line Remember:"]
    pagetext["Before it: the page<br/>free-form, up to 150 words"]
    picks["After it: up to 3 short lines"]
    page[("The nightly page<br/>replaces last night's")]:::file
    kind{"Is the line a number<br/>from tonight's list?"}:::gate
    rehearse["Rehearse that memory<br/>stability doubles, clock restarts"]
    new["New memory<br/>the line in their own words, up to 200 characters<br/>stability 4 days"]
    mem[("Long-term memories")]:::file
    fade{"Strength tomorrow<br/>below 0.25?"}:::gate
    forgot["Forgotten for good<br/>logged in a memory event"]
    keep["Kept<br/>shown tomorrow if among the 10 strongest"]
    trim["Notes older than 2 days are dropped<br/>today's log becomes yesterday's"]

    start --> chose
    chose -- no --> nothing
    chose -- yes --> energy
    energy -- no --> nothing
    energy -- yes --> call --> split
    split --> pagetext --> page
    split --> picks --> kind
    kind -- yes --> rehearse --> mem
    kind -- no --> new --> mem
    nothing --> fade
    mem --> fade
    fade -- yes --> forgot
    fade -- no --> keep
    forgot --> trim
    keep --> trim
```

The page prompt is deliberately open: no topics, no questions, no format beyond the length cap.
The pick instruction says only that a line can be something from today in their own words, or
the number of something they already remember that is still on their mind.

## 4. The life of one memory

Strength is `exp(-days since last picked / stability)`. A memory is forgotten the first night
its strength for the next day is below 0.25.

```mermaid
flowchart LR
    classDef gate fill:#fdf3d6,stroke:#eda100,color:#0b0b0b

    born["Picked at night<br/>stability 4 days"]
    q1{"Picked again<br/>within 5 days?"}:::gate
    s8["Stability 8 days"]
    q2{"Picked again<br/>within 11 days?"}:::gate
    s16["Stability 16 days"]
    q3{"Picked again<br/>within 22 days?"}:::gate
    s32["Stability 32 days<br/>and so on, doubling"]
    gone["Forgotten for good"]

    born --> q1
    q1 -- yes --> s8 --> q2
    q1 -- no --> gone
    q2 -- yes --> s16 --> q3
    q2 -- no --> gone
    q3 -- yes --> s32
    q3 -- no --> gone
```

| Times picked | Stability | Lasts this long without being picked again |
|---|---|---|
| 1 | 4 days | 5 days, gone on the 6th |
| 2 | 8 days | 11 days, gone on the 12th |
| 3 | 16 days | 22 days, gone on the 23rd |
| 4 | 32 days | 44 days, gone on the 45th |

Being shown a memory in a prompt does not count as recalling it. Only picking it again at night
does.

## 5. From prompt version 20: memory by retrieval

Up to version 19 a villager keeps only what they pick at night, every prompt shows the same ten
strongest picks, and a pick that fades is deleted. From version 20 the set-up is the usual one
for retrieval (RAG): keep everything, index it, and fetch the few pieces that match the question.
Here the "question" is the situation the villager is in.

```mermaid
flowchart TD
    subgraph keep["Kept, at the end of each day (nothing is deleted)"]
        today["the day's lines<br/>(long conversations cut into pieces)"] --> exp[("vil.experience<br/>in the checkpoint")]
        notes["notes written that day"] --> exp
        page["the night's page"] --> exp
        picks["what they chose to hold on to"] --> mem[("vil.memories<br/>as before")]
    end
    subgraph fetch["Fetched, before each prompt is written"]
        sit["the situation: text already in this prompt<br/>(where, who is near, what was just said)"] --> emb["embed"]
        exp --> cand["candidates: not what the prompt<br/>already shows in full"]
        mem --> cand
        cand --> emb
        emb --> score["score = relevance x recency x picks"]
        score --> top["top k, in the order they happened"]
    end
    top --> block["Things you remember right now ..."]
    block --> call(["the LLM call"])
```

The diagram above is the overview. Step by step, this is what happens each time a batch of
prompts is about to be written (plans for the hour, one line of a conversation, turns at the
plaza, the night's pages), and how the night feeds back into what is kept:

```mermaid
flowchart TD
    start(["a batch of prompts is about to be written"]) --> on{"long-term memory on<br/>and prompt version 20 or later?"}
    on -- no --> old["the older list: the ten strongest picks,<br/>the same in every prompt"]
    on -- yes --> each["for each villager in the batch, in name order,<br/>on the main thread"]

    each --> q["build the query from text already in their prompt<br/>(table below), joined into one text,<br/>cut at query_chars (1,500)"]
    each --> c["gather candidates from their own records only:<br/>picks + everything lived through"]
    c --> drop["leave out what the prompt already shows in full:<br/>today, notes of the last notes_days days, the latest page,<br/>yesterday (plans), the last talk with this person (conversations)"]
    drop --> any{"any candidates?"}
    any -- no --> none["nothing comes to mind:<br/>the block says (nothing yet)"]

    any -- yes --> vec{"is each text's vector<br/>already at hand?"}
    q --> vec
    vec -- "in memory, or in .cache/embed" --> sim
    vec -- no --> model["embed with the model<br/>(local by default), save in .cache/embed"]
    model -- "fails" --> stop(["the run stops; finished days are checkpointed,<br/>llmabm resume continues"])
    model --> sim["similarity: cosine of the query<br/>with each candidate"]

    sim --> rel["relevance: rescaled over this villager's candidates,<br/>closest 1.00, farthest relevance_floor (0.05)"]
    rel --> score["score = relevance<br/>x recency: exp(-days since / stability), at least recency_floor (0.1)<br/>x pick: pick_weight (2) if they chose to hold on to it"]
    score --> top["take the best k (8; 4 for quick decisions; 10 at night),<br/>at most per_line (2) pieces of any one line"]
    top --> order["put them in the order they happened,<br/>on vil.recalled; log a recall event with each score"]
    order --> block["the prompt is written with<br/>Things you remember right now ..."]
    none --> block
    block --> call(["the LLM calls of the batch, in parallel"])
    call --> clear["vil.recalled is cleared"]

    clear --> night{"was this the night's page?"}
    night -- no --> done(["on to the next batch"])
    night -- yes --> picks{"each line under Remember:"}
    picks -- "the number of a pick" --> rehearse["its stability doubles,<br/>it counts as fresh again"]
    picks -- "the number of something<br/>lived through" --> promote["it becomes a pick,<br/>in the same words"]
    picks -- "their own words" --> newpick["a new pick"]
    rehearse --> store
    promote --> store
    newpick --> store
    store[("kept on the villager, in the checkpoint:<br/>vil.memories (picks) and vil.experience<br/>(the day's lines, notes, the page).<br/>Nothing is deleted.")]
    store -. "tomorrow's candidates" .-> c
```

Retrieval itself never changes a record: being fetched does not make something last longer.
Only the villager's own choice at night does. For the scores real situations get, see
[recall_examples.md](recall_examples.md).

**What is kept.** At the end of each day, every line of `today`, every note written that day
and (after the night call) the page. A long line, in practice a conversation, is cut between
the things said into pieces of at most 240 characters, each keeping the start of the line
("11:00 ran into Gus at the river:") so it reads on its own. Private thoughts are never kept:
villagers are not shown their own earlier reasoning now either.

**What is fetched.** Before a batch of prompts is written, the simulation works out for each
villager what comes to mind and puts it on `vil.recalled`; the prompt builders read it from
there, and it is cleared once the prompts are written.

| call | records | the situation is built from | not drawn on (already in the prompt) |
|---|---|---|---|
| plan | 8 | where they are and what they can reach, the last 5 lines of today, what they noticed, unread letters, open requests, the announcement on the first plan | today, yesterday |
| chat turn | 8 | who they are talking to and where, the last 4 things said | today, the last conversation with that person |
| plaza | 8 | the newest board posts, unread letters, the last 5 lines of today | today |
| notice, spot | 4 | where they are, who or what is nearby, what they had planned | today |
| night | 10 | the day's lines | today |

Notes of the last `notes_days` days and the latest page are never fetched either: every prompt
already shows them. Each villager draws only on their own records.

**The situation is only text the villager is about to read.** Nothing is added to it: no goal,
no topic, no hint about what matters. Retrieval can only bring back things they lived through
that resemble where they are now.

**The score** is `relevance x recency x picks`:
- *relevance*: cosine similarity between the situation and the record, scaled over the
  candidates so the closest is 1 (and never below 0.05).
- *recency*: the same curve as before, `exp(-days since last recalled / stability)`, but with a
  floor of 0.1. An old record is harder to recall; it is never gone.
- *picks*: a record the villager chose to hold on to counts double. Rehearsing it at night still
  doubles its stability, so it stays easy to recall for longer. Retrieval itself never changes how
  long anything lasts: only the villager's choice does.

Ties go to the later record. At most 2 pieces of one line are shown, so one long conversation
can't fill the list. The result is shown in the order things happened, each line saying what it
is: `(day 9)` for something that happened, `(day 9, a note)`, `(day 9, from your page)`,
`(day 9, held on to)`.

**The night.** The numbered list is what today brings to mind, picked or not. A number names
something in it: a pick is rehearsed as before; something not yet picked becomes a pick, in the
same words. A line written out is a new pick. The page is asked for in exactly the words used
before. Nothing is forgotten overnight: the `memory` event reports `forgotten: []` and how many
records are kept (`lived`).

**What stays as it was.** Yesterday's full list in each plan, today so far, notes of the last
`notes_days` days, the last conversation and what changed hands with a person, the Island 101,
and the page in the system prompt.

**Size.** At most 8 pieces of 240 characters, against 10 picks of 200 before, so a prompt costs
about the same mental energy to read.

**Embeddings.** Relevance needs each text as a vector. `embeddings:` in `config/models.yaml`
lists embedders that run on this machine; `--embeddings <name>` picks one. The default,
`local`, is a small model run in the simulation's own process (`fastembed`); the others are
local servers with an OpenAI-compatible `/embeddings` endpoint. Vectors are kept in
`.cache/embed/`, keyed on the model and the text, so a text is embedded once and a resume
retrieves exactly what the first attempt did. The built-in `hash` embedder needs no model and
compares texts by shared words: it is what mock runs and tests use.
[recall_examples.md](recall_examples.md) shows the scores real situations give. Vectors are not in the checkpoint, so changing the
embedding model needs no migration; the change is logged as an intervention.

**Switching a running run** (`llmabm resume <run> --prompt-version 20`): its earlier days are
filled in from what it saved. Each day's lines come from the next morning's plan ("Yesterday
you:", the list exactly as the villager saw it), or the night's prompt, or failing both the
logged actions; notes from the notes its prompts showed; pages from the nightly records. Picks
are kept as they are. The switch and what was filled in are recorded as interventions.

**What this changes about the experiment.** Before, villagers decided what survived, and lost
the rest. Now nothing is lost and the simulation decides what surfaces, with the villager's
picks as a weight. Runs before and after version 20 are not directly comparable on anything
memory touches.

## Every store

| Store | Written by | Kept for | Shown in |
|---|---|---|---|
| Today's log | the simulation, one line per action, conversation, visit, find or failure | the day | every call (snap decisions: the latest 5 lines) |
| Yesterday's log | today's log, copied at the end of the day | one day | plans only |
| Notes | the villager, in the optional `note` of a plan, a conversation turn or a plaza turn | today and the 2 days before | every call (snap decisions: the latest 6) |
| The nightly page | the villager, at night | until the next night they write | the system prompt of every call |
| Long-term memories | the villager, after the page, up to 3 a night | see chart 4 | plans, plaza and conversation turns (up to 10, strongest first, then in the order they happened); snap decisions (up to 4); numbered at night |
| Last conversation with each villager | the simulation | until the pair talks on a later day | conversation turns with that villager (the latest 8 lines, each up to 200 characters) |
| What changed hands with each villager | the simulation | the whole run | conversation turns with that villager (the latest 6); today's at night (up to version 19) |
| Prices heard | the simulation: on selling, at the Cranny counter, on asking Timmy and Tommy | the whole run, updated when heard again | plans, plaza and conversation turns |
| Recipes known | the simulation: a recipe card, a message bottle, the first clam | the whole run | plans, plaza and conversation turns, with what each needs; names only at night |
| Island 101 | worked out from the island's rules: Isabelle's morning facts so far | the whole run, the same for everyone | plans, plaza and conversation turns, closed; one free look per prompt |
| Typical energy costs | the simulation: the average cost so far of each kind of call, across all villagers | the whole run | plans and plaza turns |

Not stored anywhere a villager can read: their own earlier plans and the private `thoughts` in
them, earlier pages (each replaces the last), conversations before the last one with each
villager, and anything from two or more days ago that wasn't written into the page or picked.

## Settings

In the scenario file (`scenarios/island_blank.yaml`):

```yaml
reflection:
  enabled: true
  max_words: 150        # the page; cut at 1.3 times this
memory:
  mode: consolidate     # without this block: keep the last memory_size notes, nothing else
  picks: 3              # things to hold on to per night
  shown: 10             # memories in a prompt at most
  stability: 4          # days, for a new memory
  threshold: 0.25       # forgotten below this strength (before version 20)
  notes_days: 2         # how long a note lasts (from version 20: how long it is shown in full)
  # from prompt version 20 (retrieval); these are the defaults
  k: 8                  # records in a plan, a conversation turn or a turn at the plaza
  k_quick: 4            # ... in a quick decision; the night shows `shown`
  chunk_chars: 240      # a longer line is kept as pieces of at most this
  per_line: 2           # pieces of one line in a prompt at most
  pick_weight: 2.0      # how much more a record counts when they chose to hold on to it
  recency_floor: 0.1    # the least an old record's recency counts
```

The code is in `src/llmabm/island/memory.py` (picking, fading, forgetting; from version 20
`MemoryStore`, `chunks`, `score_records`, `recall`, `consolidate_recalled`),
`prompts.py` (`memory_block`, `reflect_prompt`, `last_chat_with`, `exchanges_with`,
`island_101`), `sim.py` (`_reflect`, `_forget`; from version 20 `_recall`, `_embed`,
`_backfill_memory`) and `llm.py` (`Embeddings`, `HashEmbeddings`, `CachedEmbeddings`).

## Where to read it for a run

- `villagers/<name>/memory.md`: what a villager carries into their prompts as of the last
  finished day, each memory with its strength and the day it fades, then night by night what
  they held on to, came back to and forgot.
- `villagers/<name>/pages.md`: their nightly pages in order.
- `events.jsonl`: `reflection` events (the page, `remembered`, `rehearsed`, or why it was
  skipped) and `memory` events (how many are kept, what was forgotten). From version 20 also
  `recall` events, one per villager per prompt: which call (`during`), how many records it
  could draw on (`of`), and what came to mind with each score (`got`; `m3` is their third pick,
  `e17` the 17th thing kept).

## What test1 shows so far (13 days, 10 villagers)

Counted from `runs/test1/events.jsonl`; these are observations, not conclusions.

- **Long-term memory has only been on since day 8.** Before that, villagers kept notes and the
  page only. Nothing has faded yet: the earliest memories are from day 8 and would first be
  forgotten on the night of day 13.
- **Four nights in ten, nothing is written.** Of 130 villager-nights, 51 were skipped for lack
  of mental energy: the page stayed as it was and nothing from that day was kept. On day 12,
  8 of 10 villagers were out of energy by night.
- **Whoever writes always picks 3.** Since day 8 that is 24 nights, 64 new memories and 8
  rehearsals (5 of them on day 11).
- **What gets picked is mostly today's state.** A sample: "No bait yet.", "6,750 bells in the
  tent.", "Shovel uses 29 left.", "No trades today", "Dax still wants bait, I owe him one.",
  "Cal: offered 1 wood for bait, need to confirm". Several repeat what the prompt already
  prints every time (bells, pockets, tool uses).
- **Notes are used often.** 272 of 364 plans carried a note.
- **Nobody has opened the Island 101** (0 lookups), though it has only been there for one day:
  the run moved to prompt version 19 on day 12.
- **Knowing a recipe hasn't led to using it.** 4 craft attempts in 1,090 actions, all on days 2
  to 5. One villager bought a recipe card he already knew (day 2, prompt version 5, before
  cards were marked "you know it"), and the next day tried to craft a recipe he didn't know,
  with materials he guessed.
- **The run changed under them.** The prompt version moved 11 times in 13 days (1 to 19), so
  early and late days are not the same conditions. A clean read of memory needs a run that
  starts on the current version.
