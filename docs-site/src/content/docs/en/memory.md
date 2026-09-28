---
title: "Memory Management"
description: "The memory system controls how much history the GM can recall and which content is retrieved and injected into the prompt each turn. Memory operates on two levels: during a game session you can view and edit the current save's memory in the sidebar panel; global defaults are configured under Platform Settings → Memory."
---

The memory system controls how much history the GM can recall and which content is retrieved and injected into the prompt each turn. Memory operates on two levels: during a game session you can view and edit the current save's memory in the sidebar panel; global defaults are configured under Platform Settings → Memory.

Access: In-game right panel → Memory tab; or Platform Settings → Memory.

---

## Key Concepts

### Memory Buckets

Memory is organized into three buckets by type:

- **Pinned bucket**: Facts you have pinned. Use this for facts that must never be forgotten, such as "the protagonist has lost their left hand."
- **World bucket**: Main quest, current objective, facts extracted by the GM, and player notes.
- **Character bucket**: Abilities and resources (items, currency, etc.).

Each turn the GM gets the most recent entries of each type (the count and overall cap are set in Settings → Memory). Each bucket can be toggled independently under Settings → Memory.

### Retrieval and Injection

Before the GM generates a reply each turn, the system retrieves the most relevant memory fragments for the current scene (retrieval) and injects them into the prompt (injection). Injected content is bounded by the per-turn memory token limit; content beyond that limit is truncated.

### Facts Library

After each GM turn, the Extractor automatically pulls important facts from the conversation and writes them to the facts library. The facts library requires no manual upkeep and participates in retrieval by relevance.

### Relationship to the World Book

The world book and memory buckets are two parallel mechanisms that do not override each other:

- **World book**: Manually maintained world-setting entries, injected by keyword or relevance. Best for long-lived, stable rules, character summaries, and place descriptions.
- **Memory buckets**: Facts dynamically generated during play or manually pinned, focused on the current save's runtime state.

Both contribute content to the GM in the same turn and share the same token budget. When many world book entries are active, consider lowering the memory injection limit accordingly.

---

## Common Tasks

### Pin an Important Fact (In-Game)

Enter a game → right panel Memory tab → click + in the Pinned Memory section → enter the content and confirm. The entry is pinned immediately; the GM will see it on the next turn.

### Unpin a Memory (In-Game)

Find the entry in the Pinned Memory list → click × on the right → confirm removal. The entry is no longer guaranteed to be injected, but its content is not deleted.

### Add a Player Note (In-Game)

Right panel Memory tab → click + in the Player Notes section → enter the content. The GM sees the most recent notes, and notes win over automatically extracted facts when they conflict; with many entries, the memory budget means not every note makes it into every turn.

### See What the GM Referenced This Turn

The Recall section at the bottom of the Memory tab shows the fragments retrieved from the manuscript and history this turn, along with the paragraph count.

### Adjust Memory Settings

Platform Settings → Memory. You can tune entries recalled per memory type, the archive check interval, the per-turn injection token limit, how many turns automatic facts are kept, the pinned memory limit, and the enabled state of each bucket. Changes save automatically and apply from the next turn.

---

## FAQ

**What is the difference between pinned memory and player notes?**
Both are given to the GM, and both take priority over automatically extracted facts when they conflict. Pinned memory suits critical settings that must be remembered; player notes suit quick jottings. Both are subject to the per-turn count and token budget, so don't overfill them.

**What happens when the pinned memory bucket is full?**
Pinned memory has a count limit (20 by default, adjustable in settings). Once it is reached, the memory panel asks you to delete old entries before adding new ones; existing entries are never moved or deleted automatically.

**The GM keeps forgetting something. What should I do?**
Pin that fact in the in-game memory panel, or write it as a player note. If it is still being forgotten, check whether the per-type recall count or the per-turn injection token limit is set too low, so the entry was left out that turn.

**Will turning off a bucket delete its contents?**
No. Disabling a bucket only stops retrieval and injection from that bucket; the data is retained and resumes normal operation when the bucket is re-enabled.

**Will using memory and the world book together exceed the token budget?**
Both draw from the same per-turn token budget. If the context is frequently truncated, lower the per-turn injection token limit, disable one of the buckets, or reduce the number of always-on world book entries.

---

## See Also

- [In-Game Memory Panel](/en/game-memory) — View pinned memory, facts library, notes, and this-turn recall
- [Settings · Memory](/en/settings-memory) — Retrieval depth, token limit, bucket toggles
- [World Book](/en/worldbook) — The world-setting injection mechanism that runs parallel to memory
- [In-Game World Book Panel](/en/game-worldbook) — View world book state during a session
