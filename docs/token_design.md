# Thinking on a budget: how the island spends tokens

Every villager on the island is an LLM, and every thought, plan and sentence costs tokens. That
matters for two reasons. The practical one: free tiers cap how many tokens a run can use per
minute and per day, so a design that wastes tokens can't run at all. The conceptual one: people
also have limited attention and energy, and what they choose to spend it on is part of who they
are. So the budget isn't only a constraint to work around. It is part of the model: what a
villager spends thought on is behaviour we can observe.

These notes record the decisions behind the current design, what each replaced, the numbers that
motivated it, and what is still open. Numbers marked *mock* come from the offline mock backend,
whose replies are short; numbers marked *real* come from runs on Groq's free tier in October 2026
with 10 blank villagers.

## 1. Keep the LLM for decisions

The simulation is split into a mind, a body, a world and a space (see the README's
Architecture section). Only the mind is an LLM. It plans intents ("mine a rock at 07:00", "visit
Bo after lunch"), thinks privately and talks. Everything that is bookkeeping is code:

- **Body:** walking, pathfinding, which tools a route needs (a ladder for cliffs, a pole for the
  river), tool wear, who gets somewhere first within an hour, going home to sleep.
- **World:** what a rock or tree gives, opening hours, prices, NPC replies, mail taking a day.
- **Space:** the tile grid, distances, what is reachable.

None of this is ever written into a prompt as rules to apply. The model never sees coordinates,
and never has to work out whether a shop is open: it tries, and the world answers ("Nook's Cranny
is closed at 06:00 (open 08:00-22:00)"). The alternative, asking the model to reason through
movement and rules in text, would cost tokens on every call and fail in ways that are hard to
tell apart from interesting behaviour.

## 2. Tell them less, let them learn

The first version gave every villager the full rulebook in every call: what each resource
yields, prices, opening hours, recipes, NPC services. It was 61% of a plan prompt.

Now villagers are told only what they can't find out by trying: the clock, how many actions they
have, when they plan and when the plaza meets, who runs which building, their energy, and what is
private. The rest they learn by trying (failures say why), by asking an NPC (`ask` Tom Nook,
Blathers, Timmy and Tommy...), and from each other.

| *mock*, per call | before | after |
|---|---|---|
| plan prompt | ~3,000 tokens | ~1,750 tokens |
| conversation turn | ~2,800 tokens | ~1,200 tokens |
| rules' share of a plan prompt | 61% | 29% |

After the change, a plan prompt was roughly: rules 29%, the villager's state 28%, memory 20%,
instructions 17%, persona 3%, the nightly page 2%.

The cost is a few days of discovery at the start of a run. The benefit goes beyond tokens: what
villagers learn, how and from whom becomes observable. A villager who never asks Timmy and Tommy
what things sell for, or never finds out that Blathers assesses fossils, is visibly worse off.

An option we didn't take: show the rulebook once and rely on memory. Models don't keep what they
saw in earlier calls; anything not in the prompt is gone.

## 3. One pool of mental energy

Each villager gets one pool of mental energy every morning (150 points, a point being about 100
tokens). Every call they make draws from it: plans, deciding whether to approach someone, each
line of a conversation, the plaza, writing at night. An earlier design had separate budgets per
activity; it was replaced so that villagers decide where their energy goes.

What a call costs:
- **Charged:** what changes from call to call (the situation, their own nightly page) and
  everything they write back.
- **Not charged:** the fixed rules and persona in the system prompt. They are the same every
  time; charging them would mostly measure how often a villager re-reads the rulebook.
- **Reading other villagers' mail** is booked as talking, wherever it is read.

Deciding whether to spend energy can't itself cost a call, so villagers decide ahead, in each
plan: go to the plaza tonight or not, write tonight or not, and at the next planning time plan
again or follow habit (repeat what they did yesterday at that time, which is free). When the pool
runs out they follow habit, only nod when spoken to, skip the plaza and don't write until morning.

*Real:* on the first day, villagers spent 33 to 76 of their 150 points (average 55) and nobody
ran out. 150 is generous; a lower budget is needed for the choices to bite. *Mock*, 5 days:

| daily budget | villager tokens | plans replaced by habit (ran out) |
|---|---|---|
| off | 343k | 0 of 100 |
| 150 | 318k | 0 of 101 |
| 60 | 296k | 0 of 104 |
| 30 | 201k | 23 of 109 |

## 4. Social energy: introversion and extraversion as a cost

Talking is one of the things energy goes on. Noticing someone and deciding whether to go over is
a (cheap) call; each line of a conversation is a call; the plaza is a call; letters and board
posts are written at the plaza and read later in a plan. To keep that honest:

- letters are capped at 350 characters and board posts at 250
- a plan shows at most the 8 oldest unread letters; the rest wait, so mail can't snowball
- reading mail is booked as talking, not planning

So being social has a price, paid by the one who talks and by the one who reads. The blank
villagers start with no personality at all. If stable differences appear, some villagers who
approach people, go to the plaza and write letters, others who keep to themselves, they emerge
from the environment (where they live, who they run into, how their days go) and from what they
wrote to themselves, not from a trait we gave them. The traits analysis measures this with
`approach_rate`, `chats`, `messages`, `talk_energy` and plaza skips. Whether such differences
are stable across days, and whether they follow the environment or are just noise, is an open
question that needs several seeds and a control run with the nightly page off.

## 5. Planning on each villager's own clock

Plans are the biggest cost: 43% of a day's tokens (*real*, day 0). The first idea for saving was
fewer planning times. Measuring first changed the picture:

- With three fixed planning times (05:00, 12:00, 19:00), the 19:00 plan almost never happened.
  Villagers used up their 10 actions by then, so it was already two plans a day in practice.
  Cutting a planning time saved almost nothing.
- With two fixed times (05:00 and 15:00), the morning plan could use all 10 actions. Villagers
  who did that had nothing to do in the evening, and late risers got shorter plans. Fixed times
  are unfair to whoever doesn't keep those hours.

The current design: each villager plans when they wake up and again halfway through their waking
hours, each plan covering half the time they are awake, and the first plan can use at most half
of the day's actions (whatever it doesn't use carries over). Early risers and night owls get the
same planning. It is about fairness, not tokens.

## 6. Memory that fades unless it matters

The first memory was simple: the last 15 notes a villager wrote, yesterday's events, today's
events, and the nightly page. Notes piled up in order and dropped off the end whatever they were.

The current memory is a basic model, loosely after how human memory works:

- **Consolidation at night.** After the nightly page, a villager can name up to 3 things to hold
  on to: something from the day in their own words, or the number of an older memory still on
  their mind. Nothing else from the day lasts beyond tomorrow. A villager who doesn't write that
  night keeps nothing new.
- **Salience comes from the villager.** What is memorable is their call, not a rule or a rating
  we impose. That keeps us from directing their thoughts, and it matches the idea that people
  consolidate what mattered to them.
- **Forgetting curve.** Strength is `exp(-days since last recalled / stability)`. New memories
  have a stability of 4 days and are forgotten below 0.25, so a memory nobody comes back to is
  gone after about 6 days.
- **Rehearsal.** Naming a memory again doubles its stability (4, 8, 16, 32 days...). Things a
  villager keeps coming back to last longer and longer, a nod to the spacing and testing effects.
- **Bounded.** Prompts show at most 10 memories, plus notes from the last 2 days.

This is not a validated model of memory, and it doesn't save many tokens: it keeps memory
meaningful and bounded as runs get long. The biggest memory cost is still the full list of
yesterday's events, about 355 tokens of a 2,200-token plan prompt (*real*, day 1).

## 7. Hidden reasoning

Reasoning models think before they answer, in text the villager never writes down. That is
deliberation outside the energy budget: a villager could think at length and pay only for what
it writes.

What we saw on Groq:
- gpt-oss-20b at its default reasoning effort returned **empty replies** under a 250-token cap:
  the reasoning used the whole budget. At `reasoning_effort: low` it answered in under a second.
- At `low`, hidden reasoning was about 5% of the day's tokens (*real*, day 0: 4.8k of 96.5k).
- qwen3.8-27b didn't reason at all on Groq, whatever we set.

Reasoning tokens are always recorded, per villager and call type, and the reasoning text is
saved with the prompts. A switch, `charge_reasoning`, charges it to the energy pool as thinking.
Either way, the model and its reasoning effort are experimental conditions: comparing gpt-oss at
`low` with qwen changes both the model and the amount of hidden deliberation at once.

## 8. What the free tier allows

Groq's free tier (October 2026), per model: 30 requests and 8,000 tokens a minute, 1,000
requests and 200,000 tokens a day. An island day for 10 villagers is about 65 to 70 calls and
100k tokens (*real*, day 0, on both gpt-oss-20b and qwen3.8-27b), and later days cost more. So:

- about two island days per real day per model; a longer run is spread over days, with the
  cache replaying the finished part of an interrupted day for free
- the per-minute token cap, not the model's speed, sets the pace: 10 to 15 minutes per island day
- the client paces itself on tokens as well as requests, and stops cleanly on a daily cap

Things we learned the hard way: Groq retired its Llama chat models, so the default moved to
gpt-oss-20b; Cloudflare in front of Groq rejects Python's default User-Agent ("error code
1010") before a request reaches the API; and Groq's prompt caching showed no effect in a test,
so every token counts.

## 9. Open questions and next steps

**Memory**
- A model with more grounding: for example ACT-R's base-level activation (memories strengthened
  by each use and decaying as a power law), or retrieval by recency, importance and relevance to
  the current situation, as in generative-agent work. Emotional salience is the hard part without
  asking the model to rate things, which we have avoided.
- Relevance: show the memories that relate to what the villager is doing, not just the strongest.
- Gist: compress yesterday's events ("hit rocks x4") instead of listing every action.

**Energy**
- Calibrate the budget so it binds (150 was generous on day 0).
- Should reading the rules cost something once a day, as attention would?
- Charge hidden reasoning or not, and does it change behaviour?
- Carry-over: should unspent energy partly roll over?

**Social energy**
- Do stable introvert and extravert patterns emerge, and do they follow homesite, luck or who a
  villager happens to meet? Several seeds per condition, and a control without the nightly page.

**Tokens**
- The "where you are and what you can reach" note is 17% of a plan prompt, and the museum list
  grows every day; both could be shorter (counts in the prompt, details when asked).
- A shorter action menu. (Done differently from prompt version 16: the system prompt lists every kind
  of action and what it needs, and each plan adds a numbered list of what the villager can do then,
  about 700 tokens more per plan in all, in exchange for no plans failing on how they were written.)

**Validity**
- One model shares one training distribution, so villagers are less different than they look.
- Results depend on prompt wording; wording variants are conditions, and every condition needs
  several seeds.
