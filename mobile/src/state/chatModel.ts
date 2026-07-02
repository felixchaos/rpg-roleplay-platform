/** Chat message model + normalization of backend game-state history → bubbles. */

export type ToolEvent = {
  kind: "call" | "result";
  name: string;
  payload?: unknown;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  tools?: ToolEvent[];
  pending?: boolean;
  error?: string;
  /** 0-based index into the backend history array; undefined for in-flight drafts. */
  messageIndex?: number;
};

/**
 * The backend keeps turn history in state.tavern.history (or state.history) as a list
 * of { role, content/text/player_action/gm_output } entries. We flatten those into
 * alternating user/assistant bubbles, tolerating the several shapes the engine emits.
 * messageIndex tracks the raw array position so message-edit can target the right turn.
 */
export function normalizeHistory(state: any): ChatMessage[] {
  if (!state) return [];
  const raw: any[] =
    state?.tavern?.history ||
    state?.history ||
    state?.messages ||
    [];
  const out: ChatMessage[] = [];
  raw.forEach((entry, i) => {
    if (!entry) return;
    // Shape A: { role, content }
    if (entry.role && (entry.content != null || entry.text != null)) {
      const role = entry.role === "user" || entry.role === "player" ? "user" : "assistant";
      out.push({ id: `h-${i}`, role, text: String(entry.content ?? entry.text ?? ""), messageIndex: i });
      return;
    }
    // Shape B: branch commit { player_action, gm_output }
    if (entry.player_action != null || entry.gm_output != null) {
      if (entry.player_action) out.push({ id: `h-${i}-u`, role: "user", text: String(entry.player_action), messageIndex: i });
      if (entry.gm_output) out.push({ id: `h-${i}-a`, role: "assistant", text: String(entry.gm_output), messageIndex: i });
    }
  });
  return out.filter((m) => m.text.trim().length > 0);
}
