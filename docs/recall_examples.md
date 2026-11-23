# What comes to mind, and how it is scored

Written by `python scripts/recall_doc.py runs/test1`; rerun it after changing the embedding model, the memory settings or the scoring, so this page can't drift. Nothing here is made up: the situations are the prompts the 10 villagers of `runs/test1` were really given on days 11 to 13, and the memories are what each of them had lived through before that day (3,625 records in all: 1,990 happened, 729 note, 588 conversation, 256 nightly page, 62 held on to).

That run was played with the older memory (a short list of picks), so this shows what retrieval *would* have brought to mind in those moments, not what the villagers saw. See [memory.md](memory.md#5-from-prompt-version-20-memory-by-retrieval) for the rules and a flowchart of the steps.

Embedding model: `fastembed:BAAI/bge-small-en-v1.5`. Compared with: `hash-256`.

## What the query is

Retrieval has no question written for it. The query is text taken from the prompt the villager is about to read: the parts listed for each kind of prompt below, in that order, joined with spaces into one text and cut at 1,500 characters. That one text is embedded and compared with each record. Under each example the query is written out in full, one line for each part; the names in italics are not part of it.

| Prompt | The query, in order |
|---|---|
| A plan | where they are and what they can reach, the last 5 things that happened today, who they noticed, unread letters, open requests, and the morning announcement in the first plan |
| One line in a conversation | who they are talking with and where, and the last 4 things said |
| Noticing someone nearby | where they are, who is nearby, and the last 5 things that happened today |
| Spotting a bug or a fish | what they spotted, where they are, and what they had planned for that hour |
| A turn at the plaza | the last 8 posts on the board, letters, and the last 5 things that happened today |
| Writing at night | everything that happened today |

## How to read the numbers

    score = relevance x recency x pick

| Column | What it is |
|---|---|
| similarity | The cosine between the query's vector and the record's, straight from the embedding model. It is not used as it is. |
| relevance | The similarity rescaled over that villager's candidates for that prompt: the closest record is 1.00, the farthest 0.05. So it says how a record ranks for this prompt, not how good the match is. |
| recency | exp(-days since / stability), never below 0.1. Stability is 4 days, doubled each time a pick is named again at night. |
| pick | 2 if the villager chose to hold on to it, else 1. |

A prompt shows the top records by score, at most 2 pieces of any one line.

## Across every prompt

All 300 prompts of days 11 to 13 that had something to draw on (a kind nobody was given in those days is taken from the last day it has, named in its row). "In the run" counts every prompt of that kind over the whole run, days 0 to 13; kinds are listed most common first. Similarities are medians over the prompts.

| Prompt | In the run | Scored here | Records to draw on | Shown | Similarity: best shown | weakest shown | all candidates | Recency of what is shown | Also shown by `hash` |
|---|---|---|---|---|---|---|---|---|---|
| A plan | 313 | 65 | 277 | 8 | 0.81 | 0.64 | 0.66 | 0.61 | 51% |
| One line in a conversation | 292 | 107 | 302 | 8 | 0.87 | 0.66 | 0.65 | 0.78 | 59% |
| Noticing someone nearby | 227 | 35 | 303 | 4 | 0.81 | 0.70 | 0.68 | 0.78 | 36% |
| Spotting a bug or a fish | 115 | 76 | 297 | 4 | 0.87 | 0.75 | 0.67 | 0.78 | 40% |
| A turn at the plaza (day 10) | 88 | 4 | 226 | 8 | 0.91 | 0.60 | 0.66 | 0.61 | 78% |
| Writing at night | 83 | 13 | 299 | 10 | 0.88 | 0.75 | 0.69 | 0.78 | 45% |

What the shown records are: conversation 46%, happened 31%, held on to 21%, note 1%, nightly page 1%. Half of them are from 2 days back or less; the oldest is from 5 days back.

## Worked examples

For each kind of prompt, the latest ones in the run. Rows marked ✓ go in the prompt; the rows after them are the next best that don't.

### A plan

**Ari, day 13** (`d013_p0.txt`; 306 records to draw on)

Query (558 characters):

- *where they are:* Getting around: You are at lower ground, southeast. You carry: ladder. Cliffs need a ladder and the river needs a vaulting pole (or a bridge).
- *the last 5 things today:* (nothing)
- *who they noticed:* (nothing)
- *unread letters:* (nothing)
- *open requests:* Open requests: you want 1 fishing bait from Jo (until day 13); Gus wants 1 fishing bait from you (until day 14); you want 1 fishing bait from Gus (until day 14)
- *the morning announcement:* Isabelle's morning announcement: Good morning, everyone! It's day 13. The island ordinance is no ordinance. It's May 4 and the weather today is rain. Nothing much to report from yesterday. Did you know eating an apple first lets your shovel break a rock?

| | score | similarity | relevance | recency | pick | from | text |
|---|---|---|---|---|---|---|---|
| ✓ | 0.607 | 0.832 | 1.00 | 0.61 | 1 | day 11, conversation | 08:00 ran into Jo at Dax's home: you said "Sure, Ari! I’ll bring a rock if I find one. Do you have any fishing bait to trade for a wood?" [you asked… |
| ✓ | 0.587 | 0.822 | 0.97 | 0.61 | 1 | day 11, conversation | 08:00 ran into Jo at Dax's home: Jo said "Thanks, Ari! Here’s a selzalne fish. Can you bring me a rock if you find one?" [Jo handed you 1 selzalne fi… |
| ✓ | 0.578 | 0.817 | 0.95 | 0.61 | 1 | day 11, happened | 17:00 fished for a large shadow in the river: caught a selit fish (also nearby: Ike) |
|  | 0.574 | 0.815 | 0.95 | 0.61 | 1 | day 11, conversation | 08:00 ran into Jo at Dax's home: you said "Thanks! Here’s a selit fish, can you trade me a fishing bait?" [you handed Jo 1 selit fish; you asked Jo f… |
|  | 0.574 | 0.815 | 0.95 | 0.61 | 1 | day 11, conversation | 08:00 ran into Jo at Dax's home: Jo said "" [Jo's offer: Jo gives you 1 selit fish, you give Jo 1 fishing bait]; you said "Hey Jo, I still need that… |
|  | 0.565 | 0.811 | 0.93 | 0.61 | 1 | day 11, conversation | 08:00 ran into Jo at Dax's home: you said "Thanks, Jo! Do you have any fishing bait I could trade for a wood?" [you asked Jo for 1 fishing bait] |
| ✓ | 0.557 | 0.807 | 0.92 | 0.61 | 1 | day 11, conversation | 10:00 Bo visited you at home: Bo said "Hey Ari, can I trade 3 wood for a fishing bait?" [Bo's offer: Bo gives you 3 wood, you give Bo 1 fishing bait;… |
| ✓ | 0.553 | 0.805 | 0.91 | 0.61 | 1 | day 11, conversation | 13:00 Ike visited you at home: Ike said "Hey Ari, could you spare some bait for me?" [Ike's offer: Ike gives you 1 wood, you give Ike 1 fishing bait;… |
| ✓ | 0.484 | 0.728 | 0.66 | 0.37 | 2 | day 9, held on to | Got selit fish at 08:00 |
| ✓ | 0.432 | 0.806 | 0.91 | 0.47 | 1 | day 10, conversation | 07:00 ran into Ike at Ike's home: you said "Sure, here's a wood." [you handed Ike 1 wood]; Ike said "Sure, here's a wood." [Ike's offer: you give 1 f… |
| ✓ | 0.431 | 0.805 | 0.91 | 0.47 | 1 | day 10, note | Remember to bring a wood to Ike tomorrow and keep asking for bait. Also check Cranny if I can reach it. |

`hash` would show 3 of the same 8; in place of the others: "Still no fishing bait, 200 bells, 7 wood, 12 hardwood, 8 softwood."; "13:00 visited Ike at home: you said "Hey Ike, can I trade a wood for a bait?" [your offer…"; "07:00 ran into Ike at Ike's home: you said "Hey Ike, can I trade you some wood for a fish…".

**Bo, day 13** (`d013_p0.txt`; 290 records to draw on)

Query (397 characters):

- *where they are:* Getting around: You are at lower ground, southwest. You carry: ladder. Cliffs need a ladder and the river needs a vaulting pole (or a bridge).
- *the last 5 things today:* (nothing)
- *who they noticed:* (nothing)
- *unread letters:* (nothing)
- *open requests:* (nothing)
- *the morning announcement:* Isabelle's morning announcement: Good morning, everyone! It's day 13. The island ordinance is no ordinance. It's May 4 and the weather today is rain. Nothing much to report from yesterday. Did you know eating an apple first lets your shovel break a rock?

| | score | similarity | relevance | recency | pick | from | text |
|---|---|---|---|---|---|---|---|
| ✓ | 0.732 | 0.625 | 0.41 | 0.88 | 2 | day 8, held on to | Ladder 22 uses left, need more wood |
| ✓ | 0.488 | 0.621 | 0.40 | 0.61 | 2 | day 11, held on to | 28 shovel uses left |
| ✓ | 0.436 | 0.766 | 0.92 | 0.47 | 1 | day 10, happened | 09:00 tried to break_rock: you need an apple in your pockets: breaking a rock eats one first, to be strong enough (shaking a fruit tree drops them) (… |
| ✓ | 0.411 | 0.698 | 0.68 | 0.61 | 1 | day 11, conversation | 10:00 visited Ari at home: you said "Hey Ari, can I trade 3 wood for a fishing bait?" [your offer: you give Ari 3 wood, Ari gives you 1 fishing bait;… |
| ✓ | 0.399 | 0.601 | 0.33 | 0.61 | 2 | day 11, held on to | Gus wants 1 bait from me tomorrow |
| ✓ | 0.370 | 0.727 | 0.78 | 0.47 | 1 | day 10, nightly page | Nothing changed in inventory, nothing exchanged.; Plan: tomorrow bring 3‑4 wood to Dax or Kit, maybe try Gus.; Keep an eye on the rocks for breaking… |
| ✓ | 0.369 | 0.679 | 0.61 | 0.61 | 1 | day 11, happened | 15:00 spotted a small fish shadow in the river (lower ground, southwest) and went after it at 16:00 instead of rest |
| ✓ | 0.362 | 0.723 | 0.77 | 0.47 | 1 | day 10, happened | 16:00 tried to go to the plaza: couldn't find a way to the plaza without a vaulting pole |
|  | 0.346 | 0.713 | 0.73 | 0.47 | 1 | day 10, nightly page | Day 10, night.; Still no bait, no fish, no bells.; Ari offered wood but I’m out of space.; Got 7 hardwood, 7 wood, 10 softwood—keep 3‑5 wood for next… |
|  | 0.335 | 0.608 | 0.35 | 0.47 | 2 | day 10, held on to | Shovel uses 29 left. |
|  | 0.333 | 0.706 | 0.70 | 0.47 | 1 | day 10, note | If Ari has wood, trade for shovel later. If Dax gives bait, fish tomorrow. Stay tuned. |

`hash` would show 3 of the same 8; in place of the others: "No bait yet."; "09:00 spotted an ashrumo moth on a rock (lower ground, southeast, near rock 5) and went a…"; "15:00 no fishing bait in your pockets, so you fished without it: looked for fish in the r…".

### One line in a conversation

**Ari, day 13** (`d013_h07_chat.txt`; 324 records to draw on)

Query (200 characters):

- *who they are talking with:* talking with Ike
- *how they met:* You ran into Ike at the rocks.
- *the last 4 things said:* You: Hey Ike, any chance you have some fishing bait? Ike: Sure, here's a wood for bait. [Ike's offer: Ike gives you 1 wood, you give Ike 1 fishing bait]

| | score | similarity | relevance | recency | pick | from | text |
|---|---|---|---|---|---|---|---|
| ✓ | 0.661 | 0.882 | 0.85 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: Gus said "Hey Ari, I can give you a piece of wood. In return, can you spare a fishing bait?" [Gus asked you for 1 f… |
| ✓ | 0.631 | 0.864 | 0.81 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: you said "Hey Gus, can I trade you my fishing rod and one wood for a fishing bait?" [your offer: you give Gus 1 fis… |
|  | 0.624 | 0.860 | 0.80 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: you said "" [you handed Gus 1 wood; you asked Gus for 1 fishing bait]; Gus said "Sorry, I still need a bait. Can yo… |
|  | 0.616 | 0.854 | 0.79 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: Gus said "Sure, but I need a bait. Can you spare one?" [Gus asked you for 1 fishing bait]; you said "Sorry, I have… |
|  | 0.581 | 0.833 | 0.75 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: you said "Hey Gus, I was hoping you had a spare wood or could point me to some rocks near the river for bait."; Gus… |
| ✓ | 0.506 | 0.876 | 0.83 | 0.61 | 1 | day 11, conversation | 10:00 Bo visited you at home: Bo said "Hey Ari, can I trade 3 wood for a fishing bait?" [Bo's offer: Bo gives you 3 wood, you give Bo 1 fishing bait;… |
| ✓ | 0.490 | 0.863 | 0.81 | 0.61 | 1 | day 11, conversation | 08:00 ran into Jo at Dax's home: you said "Sure, Ari! I’ll bring a rock if I find one. Do you have any fishing bait to trade for a wood?" [you asked… |
| ✓ | 0.472 | 0.955 | 1.00 | 0.47 | 1 | day 10, conversation | 07:00 ran into Ike at Ike's home: you said "Sure, here's a wood." [you handed Ike 1 wood]; Ike said "Sure, here's a wood." [Ike's offer: you give 1 f… |
| ✓ | 0.468 | 0.845 | 0.77 | 0.61 | 1 | day 11, conversation | 08:00 ran into Jo at Dax's home: you said "Thanks, Jo! Do you have any fishing bait I could trade for a wood?" [you asked Jo for 1 fishing bait] |
| ✓ | 0.464 | 0.946 | 0.98 | 0.47 | 1 | day 10, conversation | 07:00 ran into Ike at Ike's home: Ike said "Sure, give me that bait." [Ike's offer: you give 1 fishing bait, you get 1 wood]; you said "Sorry, I don’… |
| ✓ | 0.418 | 0.747 | 0.57 | 0.37 | 2 | day 9, held on to | Ike dropped a selzalne fish after wood offer |

`hash` would show 7 of the same 8; in place of the others: "07:00 ran into Ike at Ike's home: you said "Hey Ike, can I trade you some wood for a fish…".

**Bo, day 13** (`d013_h09_chat.txt`; 309 records to draw on)

Query (89 characters):

- *who they are talking with:* talking with Dax
- *how they met:* You are visiting Dax at their home. Inside you can see: nothing much yet
- *the last 4 things said:* (nothing)

| | score | similarity | relevance | recency | pick | from | text |
|---|---|---|---|---|---|---|---|
| ✓ | 0.779 | 0.868 | 1.00 | 0.78 | 1 | day 12, happened | 09:00 inside Dax's home: not much furniture yet |
| ✓ | 0.672 | 0.810 | 0.86 | 0.78 | 1 | day 12, happened | 09:00 walked over to Dax's home |
| ✓ | 0.501 | 0.715 | 0.64 | 0.78 | 1 | day 12, happened | 07:00 walked over to Ari's home (also nearby: Ike) |
| ✓ | 0.459 | 0.764 | 0.76 | 0.61 | 1 | day 11, happened | 10:00 inside Ari's home: not much furniture yet |
| ✓ | 0.418 | 0.670 | 0.54 | 0.78 | 1 | day 12, conversation | 07:00 ran into Ike at Ari's home: Ike said "Sorry, Bo. No room right now. Maybe later."; you said "Sure, maybe later. Good luck!" |
| ✓ | 0.411 | 0.812 | 0.87 | 0.47 | 1 | day 10, happened | 08:00 walked over to Dax's home (also nearby: Jo) |
| ✓ | 0.410 | 0.665 | 0.53 | 0.78 | 1 | day 12, happened | 07:00 knocked at Ari's home, but nobody answered |
| ✓ | 0.400 | 0.660 | 0.51 | 0.78 | 1 | day 12, conversation | 07:00 ran into Ike at Ari's home: you said "Hey Ike, got any wood spare?"; Ike said "Hey Bo, can I trade 3 wood for one of those fishing rods?" [Ike… |
|  | 0.397 | 0.800 | 0.84 | 0.47 | 1 | day 10, happened | 12:00 walked over to Dax's home (also nearby: Ari) |
|  | 0.394 | 0.656 | 0.51 | 0.78 | 1 | day 12, conversation | 07:00 ran into Ike at Ari's home: Ike said "Hey Bo, can I trade you my fishing rod for a wood?" [Ike's offer: Ike gives you 1 fishing rod, you give I… |
|  | 0.383 | 0.650 | 0.49 | 0.78 | 1 | day 12, conversation | 07:00 ran into Ike at Ari's home: Ike said "Sure, here's my rod." [Ike accepted: you gave Ike 1 wood, Ike gave you 1 fishing rod; then you went your… |

`hash` would show 3 of the same 8; in place of the others: "No bait yet."; "09:00 knocked at Gus's home, but nobody answered"; "07:00 ran into Ike at Ari's home: you said "Sure, I'll trade you a wood for the rod." [yo…".

### Noticing someone nearby

**Ari, day 13** (`d013_h07_notice.txt`; 325 records to draw on)

Query (443 characters):

- *where they are:* at the rocks
- *who is nearby:* Nearby: Ike.
- *the last 5 things today:* (day 12) Got a trade, hope Gus has bait. Keep an eye on inventory. (day 12) Need bait tomorrow, keep offering wood (day 12) Keep looking for rocks near the river and maybe try to trade with Jo or Dax tomorrow. (day 12) Keep track of Gus's patience; maybe he'll give me bait next time if I keep offering wood. (day 12) Remember to stock up on bait tomorrow, maybe trade with Dax or buy from Cranny when I get a ladder.

| | score | similarity | relevance | recency | pick | from | text |
|---|---|---|---|---|---|---|---|
| ✓ | 0.641 | 0.813 | 0.82 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: you said "Hey Gus, can I trade you my fishing rod and one wood for a fishing bait?" [your offer: you give Gus 1 fis… |
| ✓ | 0.632 | 0.809 | 0.81 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: Gus said "Hey Ari, I can give you a piece of wood. In return, can you spare a fishing bait?" [Gus asked you for 1 f… |
|  | 0.617 | 0.802 | 0.79 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: Gus said "Sure, but I need a bait. Can you spare one?" [Gus asked you for 1 fishing bait]; you said "Sorry, I have… |
|  | 0.607 | 0.798 | 0.78 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: you said "" [you handed Gus 1 wood; you asked Gus for 1 fishing bait]; Gus said "Sorry, I still need a bait. Can yo… |
|  | 0.595 | 0.792 | 0.76 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: you said "Hey Gus, I was hoping you had a spare wood or could point me to some rocks near the river for bait."; Gus… |
| ✓ | 0.524 | 0.828 | 0.86 | 0.61 | 1 | day 11, conversation | 13:00 Ike visited you at home: Ike said "Hey Ari, could you spare some bait for me?" [Ike's offer: Ike gives you 1 wood, you give Ike 1 fishing bait;… |
| ✓ | 0.518 | 0.824 | 0.85 | 0.61 | 1 | day 11, conversation | 08:00 ran into Jo at Dax's home: you said "Sure, Ari! I’ll bring a rock if I find one. Do you have any fishing bait to trade for a wood?" [you asked… |

`hash` would show 1 of the same 4; in place of the others: "If I still can’t get bait, maybe try to trade with Dax at 18. Keep an eye on who shows up."; "If bait still missing, try to trade with Gus or Jo next day."; "11:00 ran into Gus at Gus's home: Gus said "Sure, but I need a bait. Can you spare one?"…".

**Cal, day 13** (`d013_h08_notice.txt`; 322 records to draw on)

Query (421 characters):

- *where they are:* at the Dax's home
- *who is nearby:* Nearby: Dax.
- *the last 5 things today:* (day 12) Ask Dax for any leftover wood, trade selzalne for softwood if possible. (day 12) Got the trade. Keep an eye on Jo for more softwood tomorrow. (day 12) Keep track of softwood count (day 12) Keep track of softwood count for bridge. (day 13) If Fen runs out of wood, trade another selzalne for a softwood. If still none, consider asking Jo or Dax. Keep an eye on bridge fund tomorrow.

| | score | similarity | relevance | recency | pick | from | text |
|---|---|---|---|---|---|---|---|
| ✓ | 0.848 | 0.754 | 0.70 | 0.61 | 2 | day 11, held on to | Got an ashrumo moth; trade for softwood. |
| ✓ | 0.831 | 0.748 | 0.69 | 0.61 | 2 | day 11, held on to | Need more softwood for bridge and future trades. |
| ✓ | 0.678 | 0.826 | 0.87 | 0.78 | 1 | day 12, conversation | 14:00 ran into Fen at Jo's home: you said "Sure thing, Fen. Here’s a selzalne for your softwood." [your offer: you give Fen 1 selzalne fish, Fen give… |
| ✓ | 0.651 | 0.686 | 0.54 | 0.61 | 2 | day 11, held on to | Dax unresponsive today. |
|  | 0.600 | 0.784 | 0.77 | 0.78 | 1 | day 12, conversation | 14:00 ran into Fen at Jo's home: you said "Hey Fen, could I borrow a bit of your wood? I could trade a selzalne for softwood."; Fen said "Thanks, Cal… |
|  | 0.576 | 0.771 | 0.74 | 0.78 | 1 | day 12, conversation | 14:00 ran into Fen at Jo's home: Fen said "Got it, thanks!" [Fen accepted: you gave Fen 1 selzalne fish, Fen gave you 1 softwood]; you said "Hey Fen,… |
|  | 0.479 | 0.719 | 0.61 | 0.78 | 1 | day 12, conversation | 14:00 ran into Fen at Jo's home: Fen said "Sure, can you give me another selzalne?" [Fen asked you for 1 selzalne fish]; you said "Sure thing, anothe… |

`hash` would show 2 of the same 4; in place of the others: "Remember to ask Dax for more wood tomorrow if he has any left."; "12:00 knocked at Dax's home, but nobody answered".

### Spotting a bug or a fish

**Ari, day 13** (`d013_h07_spot.txt`; 325 records to draw on)

Query (1,376 characters):

- *what they spotted:* an ashrumo moth on a rock (lower ground, southeast, near rock 6) a small fish shadow in the pond (lower ground, southeast, near rock 1) Earlier today: - 07:00 hit rock 6: gold nugget x1 (pockets full, left some) (also nearby: Ike) - 07:00 ran into Ike at the rocks: you said "Hey Ike, any chance you have some fishing bait?"; Ike said "Sure, here's a wood for bait." [Ike's offer: Ike gives you 1 wood, you give Ike 1 fishing bait]; you said "Sorry, no bait right now." [then you went your ways] Things you remember (anything you don't hold on to at night fades): - (day 9) Got selit fish at 08:00 - (day 9) Ike dropped a selzalne fish after wood offer - (day 9) Still no fishing bait, 200 bells, 7 wood, 12 hardwood, 8 softwood. Your notes from the last 2 days: - (day 12) Got a trade, hope Gus has bait. Keep an eye on inventory. - (day 12) Need bait tomorrow, keep offering wood - (day 12) Keep looking for rocks near the river and maybe try to trade with Jo or Dax tomorrow. - (day 12) Keep track of Gus's patience; maybe he'll give me bait next time if I keep offering wood. - (day 12) Remember to stock up on bait tomorrow, maybe trade with Dax or buy from Cranny when I get a ladder. - (day 13) Need to find bait before next meeting. At 08:00 you planned: fish (where: river). Actions left today: 5 of 10. Mental energy left today: 94.
- *where they are:* at the rocks
- *what they had planned:* fish (where: river).

| | score | similarity | relevance | recency | pick | from | text |
|---|---|---|---|---|---|---|---|
| ✓ | 0.696 | 0.849 | 0.89 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: Gus said "Sure, but I need a bait. Can you spare one?" [Gus asked you for 1 fishing bait]; you said "Sorry, I have… |
| ✓ | 0.684 | 0.844 | 0.88 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: Gus said "Hey Ari, I can give you a piece of wood. In return, can you spare a fishing bait?" [Gus asked you for 1 f… |
|  | 0.684 | 0.843 | 0.88 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: you said "Hey Gus, can I trade you my fishing rod and one wood for a fishing bait?" [your offer: you give Gus 1 fis… |
|  | 0.679 | 0.841 | 0.87 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: you said "" [you handed Gus 1 wood; you asked Gus for 1 fishing bait]; Gus said "Sorry, I still need a bait. Can yo… |
|  | 0.625 | 0.815 | 0.80 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: you said "Hey Gus, I was hoping you had a spare wood or could point me to some rocks near the river for bait."; Gus… |
| ✓ | 0.593 | 0.800 | 0.76 | 0.78 | 1 | day 12, happened | 07:00 spotted a nevin moth on a tree (lower ground, west) and went after it at 08:00 instead of visit (who: Ike) |
| ✓ | 0.584 | 0.875 | 0.96 | 0.61 | 1 | day 11, conversation | 13:00 Ike visited you at home: Ike said "Hey Ari, could you spare some bait for me?" [Ike's offer: Ike gives you 1 wood, you give Ike 1 fishing bait;… |

`hash` would show 2 of the same 4; in place of the others: "Still no fishing bait, 200 bells, 7 wood, 12 hardwood, 8 softwood."; "11:00 ran into Gus at Gus's home: you said "Hey Gus, can I trade you my fishing rod and o…".

**Bo, day 13** (`d013_h11_spot.txt`; 310 records to draw on)

Query (1,500 characters):

- *what they spotted:* a small fish shadow in the river (lower ground, southeast, near rock 6) Earlier today: - 10:00 inside Ike's home: not much furniture yet - 10:00 visited Ike at home: you said "Hey Ike, can I trade you a couple of woods for a wood back?" [your offer: you give Ike 2 wood, Ike gives you 1 wood]; Ike said "Sure thing, thanks!" [Ike's offer: Ike gives you 1 wood, you give Ike 2 wood, but Ike had no room, so there was no offer]; you said "Hey Ike, could I get a fishing rod in return for a wood?" [you asked Ike for 1 fishing rod]; Ike said "Sure, here's a wood for a rod." [Ike's offer: Ike gives you 1 wood, you give Ike 1 fishing rod]; you said "Sure thing, Ike." [you tried to hand Ike 1 fishing rod but Ike had no room; you accepted: Ike gave you 1 wood, you gave Ike 1 fishing rod]; Ike said "Thanks, Bo." [then you went your ways] - 11:00 walked over to Ari's home - 11:00 inside Ari's home: not much furniture yet - 11:00 visited Ari at home: you said "Hey Ari, got any bait? I can spare a few woods." [your offer: you give Ari 3 wood, Ari gives you 1 fishing bait]; Ari said "Sorry, I don’t have bait right now."; you said "Sorry, I'll come back later."; Ari said "Sorry, I still don’t have any bait. Do you have one I could take?" [Ari asked you for 1 fishing bait]; you said "" [your offer: you give Ari 3 wood, Ari gives you 1 fishing bait]; Ari said "Sorry, I still don’t have any bait. Can I borrow one?"; you said "" [just nodded (too tired to talk)] Things you remember (anything you do **[cut here]**
- *where they are:* (past the 1,500 characters; not in the query)
- *what they had planned:* (nothing)

| | score | similarity | relevance | recency | pick | from | text |
|---|---|---|---|---|---|---|---|
| ✓ | 0.779 | 0.890 | 1.00 | 0.78 | 1 | day 12, conversation | 07:00 ran into Ike at Ari's home: Ike said "Sure, but I don't have a rod right now." [Ike asked you for 1 fishing rod]; you said "Sure thing, here's… |
| ✓ | 0.776 | 0.888 | 1.00 | 0.78 | 1 | day 12, conversation | 07:00 ran into Ike at Ari's home: you said "Hey Ike, I can trade you my fishing rod for your 3 wood." [your offer: you give Ike 1 fishing rod, Ike gi… |
|  | 0.774 | 0.888 | 0.99 | 0.78 | 1 | day 12, conversation | 07:00 ran into Ike at Ari's home: Ike said "Hey Bo, can I trade you my fishing rod for a wood?" [Ike's offer: Ike gives you 1 fishing rod, you give I… |
|  | 0.774 | 0.888 | 0.99 | 0.78 | 1 | day 12, conversation | 07:00 ran into Ike at Ari's home: you said "Sure, I'll trade you a wood for the rod." [you accepted: Ike gave you 1 fishing rod, you gave Ike 1 wood;… |
|  | 0.765 | 0.884 | 0.98 | 0.78 | 1 | day 12, conversation | 07:00 ran into Ike at Ari's home: Ike said "Thanks, Bo! I could use a fishing rod, if you have one." [Ike asked you for 1 fishing rod]; you said "Sur… |
| ✓ | 0.710 | 0.859 | 0.91 | 0.78 | 1 | day 12, conversation | 09:00 visited Dax at home: you said "Hey Dax, got a spare bait for me?" [your offer: you give Dax 3 wood, Dax gives you 1 fishing bait] |
| ✓ | 0.666 | 0.839 | 0.86 | 0.78 | 1 | day 12, happened | 19:00 fished for a large shadow in the river: caught a selit fish |

`hash` would show 1 of the same 4; in place of the others: "10:00 visited Ari at home: you said "Hey Ari, can I trade 3 wood for a fishing bait?" [yo…"; "07:00 ran into Ike at Ari's home: Ike said "Hey Bo, can I trade you my fishing rod for a…"; "07:00 ran into Ike at Ari's home: you said "Sure, I'll trade you a wood for the rod." [yo…".

### A turn at the plaza

**Eli, day 10** (`d010_h07_plaza.txt`; 226 records to draw on)

Query (960 characters):

- *the last 8 posts on the board:* [b73] (day 6) Kit: Kit: Offer 1 selit fish for 1 fishing bait. Anyone willing? [b74] (day 6) you: Eli: Need 1 fishing bait and 1 bridge donation. Offer 1 selit fish and 1 softwood. Anyone willing? [b75] (day 7) you: Eli: Need 1 fishing bait and 1 bridge donation. Offer 1 selit fish and 1 softwood. Anyone willing? [b76] (day 7) you: Eli: Need 1 fishing bait and 1 bridge donation. Offer 1 selit fish and 1 softwood. Anyone willing? [b77] (day 7) you: Eli: Need 1 fishing bait and 1 bridge donation. Offer 1 selit fish, 1 softwood, 1 rock. Anyone willing? [b78] (day 7) you: Eli: Need 1 fishing bait and 1 bridge donation. Offer 1 selit fish, 1 softwood, 1 rock. Anyone willing? [b81] (day 8) you: Eli: Need 1 fishing bait. Offer 1 selit fish and 1 softwood. Also, I could use a log or rock if you have one. Any help? [b83] (day 9) you: Eli: Need 1 fishing bait. Offer 1 selit fish and 1 softwood. Also could use a log or rock if someone can spare one. Thanks!
- *letters:* (nothing)
- *the last 5 things today:* (nothing)

| | score | similarity | relevance | recency | pick | from | text |
|---|---|---|---|---|---|---|---|
| ✓ | 0.779 | 0.905 | 1.00 | 0.78 | 1 | day 9, happened | 07:00 posted on the board [b83]: "Eli: Need 1 fishing bait. Offer 1 selit fish and 1 softwood. Also could use a lo" |
| ✓ | 0.587 | 0.890 | 0.97 | 0.61 | 1 | day 8, happened | 10:00 posted on the board [b81]: "Eli: Need 1 fishing bait. Offer 1 selit fish and 1 softwood. Also, I could use a" |
| ✓ | 0.579 | 0.884 | 0.96 | 0.61 | 1 | day 8, happened | 07:00 posted on the board [b80]: "Eli: Need 1 fishing bait and 1 bridge donation. Offer 1 selit fish and 1 softwoo" |
| ✓ | 0.570 | 0.877 | 0.94 | 0.61 | 1 | day 8, happened | 11:00 posted on the board [b82]: "Eli: Need 1 fishing bait and 1 bridge donation. Offer 1 selit fish and 1 softwoo" |
| ✓ | 0.565 | 0.777 | 0.73 | 0.78 | 1 | day 9, happened | 08:00 fished for a medium shadow in the river: caught a selzalne fish |
| ✓ | 0.464 | 0.717 | 0.60 | 0.78 | 1 | day 9, happened | 07:00 spotted a medium fish shadow in the river (lower ground, south, near Resident Services) and went after it at 08:00 instead of visit (who: Jo) |
| ✓ | 0.390 | 0.824 | 0.83 | 0.47 | 1 | day 7, note | Remember to ask Jo for a log or a rock if she can spare one for bait. Also keep an eye on the bridge donation tomorrow. |
| ✓ | 0.389 | 0.588 | 0.32 | 0.61 | 2 | day 8, held on to | No trades today |
|  | 0.381 | 0.667 | 0.49 | 0.78 | 1 | day 9, happened | 09:00 tried to catch_bug: there is no flowers to search you can reach from here with the tools you carry |
|  | 0.375 | 0.809 | 0.79 | 0.47 | 1 | day 7, note | Keep an eye on Jo tomorrow for donation. If no reply, maybe try to trade a log for bait with Ari next day. |
|  | 0.366 | 0.658 | 0.47 | 0.78 | 1 | day 9, happened | 07:00 took down your board post [b82] |

`hash` would show 6 of the same 8; in place of the others: "08:00 spotted a lobas moth on the flowers (lower ground, centre, near Resident Services)…"; "09:00 tried to catch_bug: there is no flowers to search you can reach from here with the…".

### Writing at night

**Ari, day 13** (`d013_reflect.txt`; 331 records to draw on)

Query (1,500 characters):

- *what happened today:* 07:00 hit rock 6: gold nugget x1 (pockets full, left some) (also nearby: Ike) 07:00 ran into Ike at the rocks: you said "Hey Ike, any chance you have some fishing bait?"; Ike said "Sure, here's a wood for bait." [Ike's offer: Ike gives you 1 wood, you give Ike 1 fishing bait]; you said "Sorry, no bait right now." [then you went your ways] 07:00 spotted a small fish shadow in the pond (lower ground, southeast, near rock 1) and went after it at 08:00 instead of fish (where: river) 08:00 tried to fish: pockets full 08:00 spotted a small fish shadow in the pond (lower ground, southeast, near rock 1) and went after it at 09:00 instead of visit (who: Gus) 09:00 tried to fish: pockets full 09:00 spotted a large fish shadow in the pond (lower ground, southeast, near rock 1) and went after it at 10:00 instead of ask (who: Tom Nook) 10:00 tried to fish: pockets full 11:00 rested 11:00 Bo visited you at home: Bo said "Hey Ari, got any bait? I can spare a few woods." [Bo's offer: Bo gives you 3 wood, you give Bo 1 fishing bait]; you said "Sorry, I don’t have bait right now."; Bo said "Sorry, I'll come back later."; you said "Sorry, I still don’t have any bait. Do you have one I could take?" [you asked Bo for 1 fishing bait]; Bo said "" [Bo's offer: Bo gives you 3 wood, you give Bo 1 fishing bait]; you said "Sorry, I still don’t have any bait. Can I borrow one?"; Bo said "" [just nodded (too tired to talk)] 12:00 too tired to plan, so you planned nothing new 19:00 too tired to plan, so yo **[cut here]**

| | score | similarity | relevance | recency | pick | from | text |
|---|---|---|---|---|---|---|---|
| ✓ | 0.714 | 0.876 | 0.92 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: Gus said "Sure, but I need a bait. Can you spare one?" [Gus asked you for 1 fishing bait]; you said "Sorry, I have… |
| ✓ | 0.694 | 0.866 | 0.89 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: you said "" [you handed Gus 1 wood; you asked Gus for 1 fishing bait]; Gus said "Sorry, I still need a bait. Can yo… |
|  | 0.692 | 0.865 | 0.89 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: Gus said "Hey Ari, I can give you a piece of wood. In return, can you spare a fishing bait?" [Gus asked you for 1 f… |
|  | 0.672 | 0.855 | 0.86 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: you said "Hey Gus, can I trade you my fishing rod and one wood for a fishing bait?" [your offer: you give Gus 1 fis… |
|  | 0.654 | 0.847 | 0.84 | 0.78 | 1 | day 12, conversation | 11:00 ran into Gus at Gus's home: you said "Hey Gus, I was hoping you had a spare wood or could point me to some rocks near the river for bait."; Gus… |
| ✓ | 0.572 | 0.885 | 0.94 | 0.61 | 1 | day 11, conversation | 13:00 Ike visited you at home: Ike said "Hey Ari, could you spare some bait for me?" [Ike's offer: Ike gives you 1 wood, you give Ike 1 fishing bait;… |
| ✓ | 0.529 | 0.859 | 0.87 | 0.61 | 1 | day 11, conversation | 08:00 ran into Jo at Dax's home: you said "Sure, Ari! I’ll bring a rock if I find one. Do you have any fishing bait to trade for a wood?" [you asked… |
| ✓ | 0.527 | 0.858 | 0.87 | 0.61 | 1 | day 11, conversation | 10:00 Bo visited you at home: Bo said "Hey Ari, can I trade 3 wood for a fishing bait?" [Bo's offer: Bo gives you 3 wood, you give Bo 1 fishing bait;… |
| ✓ | 0.507 | 0.846 | 0.84 | 0.61 | 1 | day 11, conversation | 08:00 ran into Jo at Dax's home: Jo said "" [Jo's offer: Jo gives you 1 selit fish, you give Jo 1 fishing bait]; you said "Hey Jo, I still need that… |
| ✓ | 0.497 | 0.839 | 0.82 | 0.61 | 1 | day 11, happened | 17:00 fished for a large shadow in the river: caught a selit fish (also nearby: Ike) |
| ✓ | 0.458 | 0.765 | 0.62 | 0.37 | 2 | day 9, held on to | Got selit fish at 08:00 |
| ✓ | 0.456 | 0.813 | 0.75 | 0.61 | 1 | day 11, note | Ask Bo for bait after giving wood. If Dax has it, trade. If not, maybe I’ll need to buy a fishing rod or find another source. |
| ✓ | 0.455 | 0.893 | 0.96 | 0.47 | 1 | day 10, conversation | 07:00 ran into Ike at Ike's home: you said "Hey Ike, can I trade you some wood for a fishing bait?"; Ike said "Sure, here's a wood." [Ike's offer: yo… |

`hash` would show 4 of the same 10; in place of the others: "07:00 ran into Ike at Ike's home: Ike said "Sure, give me that bait." [Ike's offer: you g…"; "08:00 ran into Jo at Dax's home: you said "Thanks, Jo! Do you have any fishing bait I cou…"; "07:00 spotted a nevin moth on a tree (lower ground, west) and went after it at 08:00 inst…".

**Eli, day 13** (`d013_reflect.txt`; 305 records to draw on)

Query (1,102 characters):

- *what happened today:* 07:00 fished for a medium shadow in the river: caught an ashpimo fish 08:00 fished for a medium shadow in the river: caught a selzalne fish 09:00 looked for bugs on the stumps, but didn't see any 09:00 spotted a small fish shadow in the river (lower ground, south, near the plaza) and went after it at 10:00 instead of rest 10:00 fished for a small shadow in the river: caught an ashpimi fish 11:00 tried to propose_bridge: there's no stretch of river narrow enough for a bridge within 8 steps of here (lower ground, south), away from other bridges and stairs; the nearest you can walk to is about 11 steps away (lower ground, south) 12:00 fished for a small shadow in the river: caught a gormo fish 12:00 spotted a small fish shadow in the river (lower ground, centre, near the plaza) and went after it at 13:00 instead of fish (where: river) 13:00 fished for a small shadow in the river: caught an ashpimi fish 14:00 fished for a small shadow in the river: it got away 15:00 fished for a small shadow in the river: caught a gormo fish 16:00 fished for a large shadow in the river: caught a selit fish

| | score | similarity | relevance | recency | pick | from | text |
|---|---|---|---|---|---|---|---|
| ✓ | 0.779 | 0.913 | 1.00 | 0.78 | 1 | day 12, happened | 07:00 fished for a small shadow in the river: caught an ashpimi fish |
| ✓ | 0.765 | 0.904 | 0.98 | 0.78 | 1 | day 12, happened | 08:00 fished for a medium shadow in the river: caught a selzalne fish |
| ✓ | 0.738 | 0.886 | 0.95 | 0.78 | 1 | day 12, happened | 07:00 spotted a medium fish shadow in the river (lower ground, south) and went after it at 08:00 instead of fish (where: river) |
| ✓ | 0.721 | 0.875 | 0.93 | 0.78 | 1 | day 12, happened | 16:00 fished for a large shadow in the river: caught a selit fish |
| ✓ | 0.661 | 0.836 | 0.85 | 0.78 | 1 | day 12, happened | 15:00 looked for fish in the river, but there were no fish shadows in sight (also nearby: Dax) |
| ✓ | 0.628 | 0.814 | 0.81 | 0.78 | 1 | day 12, conversation | 15:00 ran into Dax at the river: you said "Hey Dax, got a selit fish—can you swap it for some bait?"; Dax said "Sure, give me a wood and I'll swap it… |
| ✓ | 0.606 | 0.800 | 0.78 | 0.78 | 1 | day 12, happened | 14:00 looked for fish in the river, but there were no fish shadows in sight |
| ✓ | 0.600 | 0.907 | 0.99 | 0.61 | 1 | day 11, happened | 14:00 fished for a small shadow in the river: caught an ashpimi fish |
| ✓ | 0.597 | 0.794 | 0.77 | 0.78 | 1 | day 12, conversation | 15:00 ran into Dax at the river: Dax's offer: Dax gives you 1 wood, you give Dax 1 selit fish, but Dax had no wood, so there was no offer] |
| ✓ | 0.595 | 0.903 | 0.98 | 0.61 | 1 | day 11, happened | 13:00 fished for a medium shadow in the river: caught a selzalne fish |
|  | 0.593 | 0.792 | 0.76 | 0.78 | 1 | day 12, happened | 12:00 spotted a selashren moth on a stump (lower ground, south, near Able Sisters) and went after it at 13:00 instead of fish (where: river) |
|  | 0.593 | 0.791 | 0.76 | 0.78 | 1 | day 12, happened | 11:00 spotted a very large fish shadow in the river (lower ground, south, near the museum) and went after it at 12:00 |
|  | 0.591 | 0.900 | 0.97 | 0.61 | 1 | day 11, happened | 11:00 fished for a small shadow in the river: caught an ashpimi fish |

`hash` would show 7 of the same 10; in place of the others: "08:00 fished for a small shadow in the sea: caught a tubas fish"; "11:00 fished for a small shadow in the river: caught an ashpimi fish"; "11:00 spotted a very large fish shadow in the river (lower ground, south, near the museum…".

## Limits of this page

- Situations are rebuilt from the saved prompts, so they are close to, not exactly, what a version 20 run would compare with (a plan's "what they can reach" is the first line of that part only).
- Picks are the ones the checkpoint still holds, each as fresh as it was on that day. What a villager had already let fade under the older rule is gone; in a run that started on version 20 it would still be there.
- Records are in the villagers' and the game's own words, so the similarity numbers are specific to this run and this embedding model. Compare models by rerunning with `--embeddings <name>`.
