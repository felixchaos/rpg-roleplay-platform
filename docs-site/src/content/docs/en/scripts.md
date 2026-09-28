---
title: "Scripts"
description: "The central hub for importing, browsing, and configuring novel scripts. Upload a TXT file and the system automatically detects chapter structure, splits the content, builds vector indexes, extracts NPC character cards and worldbook entries, then lets you start a game or continue an existing save."
---

Scripts is the central hub for importing, browsing, and configuring your novel scripts. Upload a TXT file and the system automatically detects chapter structure and splits the content. From there you can build vector indexes, extract NPC character cards and worldbook entries, then start a new game or continue an existing save based on that script.

Scripts marked with a "Coming Soon" badge are feature previews — games cannot be started from them yet.

Entry point: top navigation bar → Scripts. The default view is My Scripts. The Online Script Library link is in the top-right corner of the same page.

---

## Key Concepts

### Three Views

- **My Scripts** (default): Lists all scripts you have imported or subscribed to, showing chapter count, word count, split rule, readiness status, and linked save count.
- **Import Script**: Full-screen upload page with chapter split preview, model selection, and pipeline toggles.
- **Online Script Library**: Browse scripts shared publicly by other users and import (clone) them directly to your account.

### Chapter Split Rules (7 options)

During import the system automatically detects the best-fit rule (confidence ≥ 85% shown in green). You can override it manually:

| Rule | Best for |
|---|---|
| Auto-detect | Recommended first choice; covers most Chinese web-novel formats |
| Corpus chapter | Raw corpus format with `第X章` markers |
| Chinese chapter | Chinese numbering: 第X章, 第X回, etc. |
| English chapter | Chapter X / CHAPTER X |
| Numeric dot | Lines starting with `1.`, `2.`, etc. |
| Bracketed number | （1）, 【1】, etc. |
| Custom | Enter a custom regular expression |

### Readiness Status

The Status column in the list shows which modules are ready for each script. Missing items appear in a dropdown; clicking any item jumps to the corresponding tab in the detail panel for quick resolution.

### Vector Index

The vector index has four sub-modules: chapter text (chunks), NPC character cards (cards), worldbook, and canon characters. Each can be rebuilt independently without re-embedding everything. Progress for each sub-module is shown in the Vector Index card on the Overview tab.

### Publishing and References

- **Publishing**: A script is visible only to you by default. In the action menu at the top right of the detail panel, choose **Share to Public Library** to list it in the Online Script Library, where others can browse and import it. Choose **Remove from Public Library** to take it back at any time.
- **References**: If a script references another script, its lore lookups (worldbook, characters, timeline, etc.) read the referenced script's content. A referencing script shows **Reference mode** and the referenced script's number at the top of the detail panel; the author can click **Stop referencing** to go back to reading the script's own lore.

---

## Common Tasks

### Import a New Script (TXT Upload)

1. In My Scripts, click **Import Script** in the top-right corner.
2. Drag and drop or browse to select a `.txt` file (UTF-8 encoded).
3. The system shows a Chapter Split Preview with chapter count, total word count, confidence score, and a list of the first few chapter titles.
4. If confidence is low or chapters are misaligned, click **Retry with Different Rule** and switch rules until the preview looks correct.
5. Click **Confirm Import**. The system runs the full pipeline in the background: splitting, canon character extraction, NPC card generation, worldbook extraction, and timeline anchor construction.
6. You can choose which LLM model to use for extraction, and disable the **Generate Character Cards** or **Generate Worldbook** toggles to skip the LLM phase and only split chapters.

### Import a Script Package (ZIP)

In My Scripts, click **Import Script Package** to upload a `.zip` file containing chapters, character cards, and worldbook entries. Use this to restore an export package received from another user.

### Import from the Online Script Library

1. Switch to the **Online Script Library** view (top nav or page toggle).
2. Use the search box to filter scripts, then click **Import** on a card.
3. The system clones the script to your account; find it in My Scripts to start a game.
4. Scripts you have already imported show an "Imported" badge; scripts you published yourself show a "Mine" badge and cannot be re-imported.

### View and Manage Script Details

Click any script in the list to expand the detail panel below. Tabs:

- **Overview**: Chapter count, word count, split rule, confidence, save count, vector index status (four sub-modules), sharing status
- **Parameters**: View and edit the `script_overrides` JSONB settings (JSON format)
- **Worldbook**: Inline worldbook entry editor; trigger "Rebuild from Canon (free)" or "LLM Re-extract & Enrich"
- **NPC Cards**: NPC list extracted from the script; add, edit, set as protagonist, or convert to a user character card; "AI Review Names/Semantics" is available once there are at least 2 cards
- **Canon Characters**: Normalized entities (characters, organizations, locations, etc.) extracted by the LLM — distinct from NPC cards
- **Timeline**: Story-phase anchor list, grouped by story-time tags
- **Modules**: Unified rebuild matrix for all 7 modules; rebuild individually or all at once
- **Knowledge Extraction**: Trigger a full LLM re-extraction across all modules with one click
- **Narrative Style**: Six script-level sliders — length, lens, dramatic density, psychology, suspense, guidance (author-writable only)

Action buttons at the top of the detail panel:

- **Start Game**: Dropdown to continue an existing save or start a new game
- **View Chapters**: Open the chapter browse/edit modal
- **Review Settings**: Open the KB review interface to verify AI-extracted content
- **Version History**: View and roll back to historical versions (author only)
- **More** dropdown: Build vector index, export script package, publish/unpublish, unsubscribe (for subscribed scripts), delete

### Manage Chapters (ChaptersModal)

Click **View Chapters** at the top of the detail panel:

- Left panel: Full chapter list; click to switch
- Right panel: Current chapter title, word count, and full text (lazy-loaded)
- Right panel actions: Rename, split (by character position), merge with previous chapter, merge with next chapter
- **Re-split Entire Book** (top): Choose a new split rule and re-split the whole script

### Build Vector Index

Select a script → detail panel **More** dropdown → **Build Vector Index**, or click **Redo** on an individual sub-module card in the Overview tab. Each sub-module (chapters / cards / worldbook / canon) can be re-embedded independently.

### Publish a Script

Select your own script → open the action menu at the top right of the detail panel → **Share to Public Library**. The system checks the review status before publishing; scripts that have not been reviewed must complete **Review Settings** first. To withdraw it, choose **Remove from Public Library** in the same menu.

### Fork a Subscribed Script

Click **Fork** in the blue notice area at the top of the detail panel. The system creates an independent copy under your account named "Original Title (Copy)". Future edits to the copy do not affect the original author.

### View Version History and Roll Back

Click **Version History** at the top of the detail panel to open a right-side drawer listing all historical commits (with commit message, type, and timestamp). The author can click **Roll Back** on any commit to restore the script to that version.

### Export a Script Package

Detail panel **More** dropdown → **Export Script Package** → download a `.zip` file containing chapters, character cards, and worldbook entries.

### Delete or Unsubscribe from a Script

- Scripts you created: **More** dropdown → **Delete** → confirm. Deletion also clears the vector index and cannot be undone.
- Subscribed scripts: **More** dropdown → **Unsubscribe** → confirm. This removes the script from your list only; the original author is unaffected.

### Use the Script Editor (Advanced)

For structured edits to script content — worldbook entries, timeline, NPC settings, etc. — use the [Script Editor](/en/md-editor), a VSCode-style multi-pane IDE with AI-assisted writing and rewriting.

---

## FAQ

**The chapter count is wrong after import — what do I do?**
Open **View Chapters** → click **Re-split Entire Book** and try a different rule. Start with "Chinese Chapter", then try "Custom" with a regular expression.

**What does confidence below 70% mean?**
The system has low confidence in its chapter title detection; the actual split may be misaligned. Review each entry in the preview and manually select the correct rule.

**I built the vector index but still don't see NPC cards — why?**
Vector indexing (embedding) and NPC card extraction are separate steps. NPC cards are generated during the **Generate Character Cards** stage of the import pipeline. If you turned that off at import time, trigger a rebuild from the **NPC Cards** tab, or re-run extraction from the **Knowledge Extraction** tab.

**The status column shows "Missing N items" — do I have to handle them one by one?**
You can click each item to jump to the corresponding tab, or go to the **Modules** tab to view and trigger all module rebuilds from one place.

**Can I edit the content of a subscribed script?**
Not directly. Fork a copy to your account first, then edit the copy freely.

**Are saves deleted when I delete a script?**
Yes. Deleting a script also removes all associated vector indexes, and saves linked to that script lose their backing data. Confirm before proceeding.

---

## Related

- [Saves](/en/saves) — Start a game from a script or manage existing saves
- [Character Cards](/en/cards) — User card management (NPC-converted cards appear here too)
- [Review Settings](/en/scripts-review) — Verify AI-extracted characters, worldbook, and timeline content
- [Script Editor](/en/md-editor) — VSCode-style deep-editing entry point
- [Worldbook](/en/worldbook) — Worldbook entry reference
- [Timeline](/en/timeline) — Timeline and story-progress reference
