---
title: "Settings: Memory"
description: "Control the save memory given to the GM each turn — how many entries of each memory type, the total token cap, how long automatic facts are kept, and the toggles for the three memory buckets plus the pinned-memory limit."
---

Control the save memory given to the GM each turn — how many entries of each memory type, the total token cap, how long automatic facts are kept, and the toggles for the three memory buckets plus the pinned-memory limit. Tuning these parameters lets you balance how much the GM remembers against per-turn context cost.

Navigation: top bar **Settings** → **Memory** (left sidebar). Each change saves automatically and takes effect from the next turn.

---

## Key Concepts

### Memory types in a save

The in-game **Memory** panel shows: main quest, current objective, facts (extracted by the GM automatically), player notes, pinned memories, abilities, and resources (items, currency, etc.). Before each reply, the system gives the GM **the most recent entries of each type**, capped overall by the token budget.

### Memory buckets

Three switches cover three groups of types:

- **Pinned bucket**: pinned memories.
- **World bucket**: main quest, current objective, facts and player notes.
- **Character bucket**: abilities and resources.

When a bucket is off, that group is no longer included in the per-turn memory injection.

### How long automatic facts are kept

Facts the GM extracts automatically keep piling up. Automatic facts older than **Keep facts for N turns** are no longer injected (they are not deleted); player notes, pinned memories, abilities and resources are never archived this way. The system checks every **Archive check interval** turns.

---

## Parameters

### Memory · Recall Behavior

| Parameter | Meaning | Default | Range |
|---|---|---|---|
| Entries recalled per memory type | How many of the most recent facts, notes, abilities, resources and pinned memories (each type separately) are given to the GM | 5 | 2–20 |
| Archive check interval | How often (in turns) to check whether old facts should be archived | 10 | 3–20 |
| Memory token budget per turn | Cap on the total memory given to the GM each turn (tokens estimated from length); entries over the cap are skipped for that turn | 800 | 200–2000 (step 50) |
| Keep facts for N turns | Automatic facts older than this are no longer injected | 50 | 10–200 (step 5) |

Entries per type and the token budget together decide how much is injected: no matter how high the per-type count is, the total never exceeds the token budget.

### Memory · Bucket Configuration

| Parameter | Meaning | Default |
|---|---|---|
| Pinned memory limit | Max pinned memories you can add from the memory panel (5–100); delete old ones to add more once the limit is reached | 20 |
| Enable pinned bucket | When off, pinned memories are no longer included in the per-turn memory injection | On |
| Enable world bucket | When off, main quest, objective, facts and player notes are no longer included | On |
| Enable character bucket | When off, abilities and resources are no longer included | On |

---

## Common Tasks

### Adjust entries per type or the token budget

1. Go to **Settings → Memory** and find the parameter in **Memory · Recall Behavior**.
2. Drag the slider or type a value in the number box.
3. Releasing the slider (or leaving the number box) saves automatically.

### Turn off a memory bucket

1. Find the switch in **Memory · Bucket Configuration** (e.g. **Enable world bucket**).
2. Switch it off; it applies from the next turn.

### Change the pinned memory limit

1. Find **Pinned memory limit** in **Memory · Bucket Configuration**.
2. Enter a new value (5–100); it saves when the box loses focus.

> Lowering the limit does not delete existing pinned memories; you just can't add new ones from the memory panel until the count is below the limit.

---

## Tuning Tips

### Early in a story (little memory)

The defaults (5 per type, 800-token budget) are enough for most conversations.

### Later in a story (lots of memory)

- Raise entries per type (8–12) so the GM sees more recent entries of each type.
- Raise the token budget (1000–1500) to fit them.
- Lower **Keep facts for N turns** (30–40) so very old automatic facts drop out sooner.

### Saving API cost

- Lower the token budget (400–600) — the most direct way to cut per-turn usage.
- Turn off buckets you don't need, e.g. the character bucket when abilities and resources don't matter.

---

## FAQ

**Q: The GM keeps forgetting an important detail. What should I do?**
A: Add it as a pinned memory in the in-game **Memory** panel, or write it as a player note (notes win over automatic facts when they conflict). If there are many entries, raise both the per-type count and the token budget. See [Game: Memory Panel](/en/game-memory).

**Q: What are the downsides of a very high per-type count?**
A: More tokens are injected each turn, which raises API cost and makes it easier to hit the token budget, in which case the extra entries are skipped for that turn. Raise the token budget first, then the per-type count.

**Q: Are automatic facts lost once they pass the keep window?**
A: No. They stay in the save; they are just no longer injected every turn.

**Q: What does a larger archive check interval do?**
A: Checks happen less often, so facts past the keep window drop out a few turns later. There is usually no need to change it.

**Q: How does the memory token budget relate to the model's context window?**
A: The memory token budget controls only the memory portion of the prompt. The overall context window (including conversation history and system prompt) is configured on the **Model Parameters** page. The memory budget cannot exceed the total context allocation.

---

## Related

- [Game: Memory Panel](/en/game-memory)
- [Model Configuration](/en/settings-models)
- [Module Model Assignment](/en/settings-modules)
- [Model Parameters](/en/settings-modelparams)
