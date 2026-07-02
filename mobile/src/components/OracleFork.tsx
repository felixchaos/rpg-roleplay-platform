/**
 * Oracle's Fork — the GM's "choose your path" prompt. When the agent calls
 * ask_player_choice (or the engine extracts trailing options), the question lands in
 * state.permissions.pending_questions. This renders it as an in-chat decision card:
 * the question, then ember-edged option staves the player taps. Answering posts to
 * /api/questions/clear and feeds the choice back as the next utterance.
 */
import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import Animated, { FadeInUp } from "react-native-reanimated";
import { theme } from "@/theme/theme";

export type PendingQuestion = {
  id?: string | number;
  question?: string;
  prompt?: string;
  options?: any[];
  choices?: any[];
};

function optionText(o: any): string {
  if (typeof o === "string") return o;
  return o?.label || o?.text || o?.value || String(o);
}

export function OracleFork({
  question,
  onChoose,
  onDismiss,
  busy,
}: {
  question: PendingQuestion;
  onChoose: (choice: string) => void;
  onDismiss: () => void;
  busy?: boolean;
}) {
  const opts = (question.options || question.choices || []).map(optionText).filter(Boolean);
  if (opts.length === 0) return null;
  const prompt = question.question || question.prompt || "你想怎么做？";

  return (
    <Animated.View entering={FadeInUp.duration(420).springify().damping(20)} style={styles.wrap}>
      <View style={styles.header}>
        <Text style={styles.sigil}>✧</Text>
        <Text style={styles.prompt}>{prompt}</Text>
      </View>
      <View style={styles.options}>
        {opts.map((o, i) => (
          <Pressable
            key={i}
            onPress={() => !busy && onChoose(o)}
            disabled={busy}
            style={({ pressed }) => [styles.stave, pressed && styles.stavePressed, busy && { opacity: 0.5 }]}
          >
            <Text style={styles.staveIndex}>{String.fromCharCode(65 + i)}</Text>
            <Text style={styles.staveText}>{o}</Text>
          </Pressable>
        ))}
      </View>
      <Pressable onPress={onDismiss} disabled={busy} hitSlop={8} style={styles.dismiss}>
        <Text style={styles.dismissText}>自行决定 ›</Text>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    marginVertical: theme.space(3),
    padding: theme.space(4),
    borderRadius: theme.radius.lg,
    borderWidth: 1,
    borderColor: theme.color.accentSoft,
    backgroundColor: theme.color.accentGhost,
    gap: theme.space(3),
  },
  header: { flexDirection: "row", gap: theme.space(3), alignItems: "flex-start" },
  sigil: { fontSize: 18, color: theme.color.accentBright, marginTop: 2 },
  prompt: { flex: 1, fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text, lineHeight: 24 },
  options: { gap: theme.space(2) },
  stave: {
    flexDirection: "row",
    alignItems: "center",
    gap: theme.space(3),
    paddingVertical: theme.space(3),
    paddingHorizontal: theme.space(4),
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.color.surfaceLineStrong,
    backgroundColor: theme.color.bgCard,
  },
  stavePressed: { backgroundColor: theme.color.accentSoft, borderColor: theme.color.accent },
  staveIndex: { fontFamily: theme.font.display, fontSize: theme.size.md, color: theme.color.accent, width: 20, textAlign: "center" },
  staveText: { flex: 1, fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.text, lineHeight: 22 },
  dismiss: { alignSelf: "flex-end" },
  dismissText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.textFaint },
});
