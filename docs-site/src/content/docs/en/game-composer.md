---
title: "In-Game · Input Box"
description: "The input area at the bottom of the main game interface is the primary channel for communicating with the GM (Game Master). Beyond regular dialogue, it supports slash commands, attachments, character mentions, and permission controls."
---

The input area at the bottom of the main game interface is the primary channel for communicating with the GM (Game Master). Beyond regular dialogue, it supports slash commands, attachments, character mentions, and permission controls.

Access: Bottom toolbar in any game save interface.

---

## Basic Dialogue

Type what you want to do, say, or describe as an action, then send. The GM reads your input and produces a narrative response based on the current story state.

- The input box auto-expands up to 280px in height
- The GM streams output after you send; click "Stop" at any time to interrupt
- You can drag text directly into the input box to insert it

If the player character is in a passive state (unconscious, spectating, cutscene), click the "Continue" button in the toolbar to have the GM advance the story without requiring any input.

---

## Send Mode

The current send shortcut is shown in the bottom-right of the toolbar, with a toggle button beside it:

- **Enter to send** (default): Press Enter to send; Shift + Enter adds a newline
- **Cmd/Ctrl + Enter to send**: Enter adds a newline; Cmd + Enter (Mac) or Ctrl + Enter (Windows) sends

Click the toggle button to switch between modes. The setting is saved locally and persists across page refreshes. Pressing Enter during IME composition does not trigger a send.

---

## Slash Commands

Type `/` in the input box to open the command picker. Use `↑↓` to navigate, Enter to confirm, and Esc to dismiss. You can also type a command name directly followed by its arguments. Once a command is selected, a command tag appears above the input box; press Backspace to remove it.

### Query

| Command | Description |
|---|---|
| `/status` | Show the current state summary (character, location, time, etc.) |
| `/debug` | View the GM's internal retrieval results from the previous turn, useful for troubleshooting |

### State write

| Command | Example |
|---|---|
| `/set <description>` | `/set time=dawn; location=harbor` — Force-write a set of game parameters using natural language. Writes immediately to disk; takes priority over GM-derived values |
| `/loc <place>` | `/loc Fogport Docks` — Quickly update the current location |
| `/time <moment>` | `/time next morning` — Advance the in-story timeline |
| `/rel <character> <status>` | `/rel Shen Zhiwei ally` — Update a character relationship |
| `/var <variable>=<value>` | `/var phase=harbor-dusk` — Set a custom worldline variable |

### Memory

| Command | Description |
|---|---|
| `/pin <text>` | Add content to pinned memory; the GM reads it every turn |
| `/note <text>` | Private player note; does not affect the GM |

### Mode

| Command | Description |
|---|---|
| `/permission default\|review\|read_only\|full_access` | Switch GM write permissions (see "Permission Control" below) |

### Utility

| Command | Description |
|---|---|
| `/save` | Manually save the current game |
| `/retry` | Retry the GM's last output to get a different result |

---

## @ Character Mentions

Type `@` in the input box to open the character list, which shows relationship characters from the current save and the player character. Selecting one inserts `@CharacterName` into the input box, and the GM treats it as the player addressing or acting toward that character. Use `↑↓` to navigate, Enter or Tab to insert, and Esc to dismiss.

The character list is sourced from the current save's relationship data; it may be empty before the first turn of a new save.

---

## Attachments (+ Button)

Click the + icon on the left side of the toolbar to open the attachment menu:

### Local files

- **File / Text** (TXT, MD, JSON): Upload a document or text snippet for the GM to reference in this turn
- **Image** (PNG, JPG, WEBP): Upload an image; the GM supports multimodal understanding and can describe or analyze it

### Script resources

- **Insert chapter**: Search the current script's table of contents and inject a chapter as context
- **Character card**: Search for or drag in a character card for the GM to reference its full profile
- **Worldbook entry**: Full-text search the worldbook and inject matching entries into the GM's context

### Capability extensions

- **MCP tool**: Attach a configured MCP server tool, giving the GM access to external capabilities in this conversation (see [What is MCP](/en/mcp))
- **Skill pack**: Attach a rules adjudication pack (e.g., dice rules, 5E ruleset) for the GM to use when making rulings
- **Plan mode**: Preview a narrative path without writing back to save state

Attached items appear above the input box. Click × on any attachment to remove it.

---

## AI Image Generation

The toolbar includes an AI image generation button. Clicking it opens a generation dialog for creating images during play and inserting them into the scene. See [Image Generation](/en/image-gen).

---

## Permission Control

The current LLM write permission level is shown on the left side of the toolbar; click it to open a selection menu:

| Mode | Description |
|---|---|
| **Read-only** | All GM state writes require manual player approval — most restrictive |
| **Default** | Whitelisted fields (location, time, etc.) are written automatically; others require approval |
| **Auto-review** | Whitelisted fields plus relationships and world variables are written automatically; others require approval |
| **Full access** | All writes except the hard blacklist are automatic |

When the GM submits a write request or poses a question, pending items appear above the input box. Write requests can be expanded to see before/after values and a justification, then approved or rejected individually. GM questions appear as selectable options. If the GM detects a formulaic response pattern, a prompt to regenerate the current turn also appears in this area.

---

## Model Switching

The current GM model is shown on the right side of the toolbar. Click it to open the model selector overlay, which lists all available models with configured credentials, along with their health status and pricing. Switching affects only the current save.

The overlay also includes an **Effort** selector controlling the model's reasoning depth:

| Level | Description |
|---|---|
| Off | Thinking disabled — fastest and cheapest |
| Low | ~1k tokens of thinking |
| Medium | ~4k tokens of thinking |
| High | ~8k tokens of thinking (default) |
| Extra | ~16k tokens of thinking |
| Max | ~24k tokens of thinking — deepest reasoning, highest cost |

Effort settings are saved per model. Switching models automatically restores the Effort level last used with that model.

---

## Context Usage

A ring progress indicator on the right side of the toolbar shows current context utilization: turns orange above 70%, red above 90%. Click the ring to expand a detail panel showing token usage and distribution for each component (memory, worldbook, chat history, etc.). When context is nearly full, consider saving and starting a new conversation.

---

## Related

- [Save Management](/en/saves)
- [Permissions and Approval](/en/game-worldbook)
- [Model Settings](/en/settings-models)
- [Branch Graph](/en/branch-graph)
- [MCP Integration](/en/mcp)
- [Image Generation](/en/image-gen)
