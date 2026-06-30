/**
 * Familiar's Sanctum — the editor's AI companion, conjured into a slide-up grimoire panel.
 *
 * Aesthetic direction: Candlelit Grimoire, leaned into. This is the scribe's most intimate
 * tool — speaking directly to a familiar that can reach into the knowledge base — so the
 * UI treats it as ritual, not chat. A vellum-colored thread of exchange, with the
 * familiar's words flowing in serif ink and the player's whispers set in a recessed
 * stanza. The composer is sealed by a candle-ember send sigil that flares while the
 * familiar replies.
 *
 * Differentiation: instead of a generic bot bubble, the familiar's lines stream into a
 * full-bleed prose column (no avatar, no bubble chrome), so it reads like an inscription
 * forming on the page. The player's stanza is right-anchored with a thin gilt left rule,
 * a quieter inversion. Streaming uses a fading rune caret, not a spinner.
 *
 * Backend: streams /api/console_assistant/chat (token / error / done). Persists
 * conversation_id across sends so the familiar keeps memory of the dialogue.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { BlurView } from "expo-blur";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, {
  cancelAnimation,
  Easing,
  FadeIn,
  FadeInUp,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
} from "react-native-reanimated";
import { consoleAssistant, AssistantMessage } from "@/api";
import { ApiError } from "@/api/http";
import { SseController } from "@/api/sse";
import { theme, palette } from "@/theme/theme";

type Line = AssistantMessage & { id: string; streaming?: boolean; error?: string };

function RuneCaret() {
  const o = useSharedValue(1);
  useEffect(() => {
    o.value = withRepeat(withTiming(0.15, { duration: 720, easing: Easing.inOut(Easing.ease) }), -1, true);
    return () => cancelAnimation(o);
  }, [o]);
  const style = useAnimatedStyle(() => ({ opacity: o.value }));
  return <Animated.Text style={[styles.caret, style]}>❖</Animated.Text>;
}

export function FamiliarSanctum({
  visible,
  scriptId,
  seed,
  onClose,
}: {
  visible: boolean;
  scriptId: number;
  /** Optional invocation pre-cast into the composer when the sanctum opens. */
  seed?: string | null;
  onClose: () => void;
}) {
  const insets = useSafeAreaInsets();
  const [lines, setLines] = useState<Line[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [convId, setConvId] = useState<string | null>(null);
  const [boot, setBoot] = useState(false);
  const draftIdRef = useRef<string | null>(null);
  const streamRef = useRef<SseController | null>(null);
  const scrollRef = useRef<ScrollView | null>(null);

  // Begin a fresh consultation when the sanctum is opened. We don't restore old threads —
  // each summoning is a clean parchment, matching the desktop's transient editor sidebar.
  // If a seed was cast (e.g. from a chapter-save sync prompt), pre-fill the composer with
  // it instead of clearing — the scribe arrives mid-incantation, ready to release.
  useEffect(() => {
    if (!visible) return;
    setBoot(true);
    setLines([]);
    setInput(seed || "");
    setConvId(null);
    consoleAssistant
      .newConversation({ script_id: scriptId, page: "script-editor" })
      .then((r) => setConvId(r?.conversation_id || r?.id || null))
      .catch(() => {})
      .finally(() => setBoot(false));
    return () => {
      streamRef.current?.close();
      streamRef.current = null;
    };
  }, [visible, scriptId]);

  const scrollToEnd = useCallback(() => {
    requestAnimationFrame(() => scrollRef.current?.scrollToEnd({ animated: true }));
  }, []);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setStreaming(true);

    const userLine: Line = { id: `u-${Date.now()}`, role: "user", content: text };
    const draftId = `a-${Date.now()}`;
    draftIdRef.current = draftId;
    const draft: Line = { id: draftId, role: "assistant", content: "", streaming: true };
    setLines((prev) => [...prev, userLine, draft]);
    scrollToEnd();

    streamRef.current = await consoleAssistant.chat(
      { message: text, conversation_id: convId ?? undefined, page_context: { script_id: scriptId } },
      {
        token: (d) => {
          const chunk = typeof d === "string" ? d : d?.text || "";
          if (!chunk) return;
          setLines((prev) =>
            prev.map((l) => (l.id === draftIdRef.current ? { ...l, content: l.content + chunk } : l)),
          );
          scrollToEnd();
        },
        error: (d) => {
          const msg = typeof d === "string" ? d : d?.message || "familiar 失语了";
          setLines((prev) =>
            prev.map((l) => (l.id === draftIdRef.current ? { ...l, error: msg, streaming: false } : l)),
          );
        },
        done: () => {
          setLines((prev) =>
            prev.map((l) => (l.id === draftIdRef.current ? { ...l, streaming: false } : l)),
          );
          setStreaming(false);
          streamRef.current = null;
          scrollToEnd();
        },
        onError: (e) => {
          setLines((prev) =>
            prev.map((l) =>
              l.id === draftIdRef.current ? { ...l, error: e.message, streaming: false } : l,
            ),
          );
          setStreaming(false);
        },
      },
    );
  }, [input, streaming, convId, scriptId, scrollToEnd]);

  const stop = useCallback(() => {
    streamRef.current?.close();
    streamRef.current = null;
    setLines((prev) => prev.map((l) => (l.id === draftIdRef.current ? { ...l, streaming: false } : l)));
    setStreaming(false);
  }, []);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(3) }]}>
        <BlurView intensity={36} tint="dark" style={styles.fill} />
        <View style={styles.grabber} />

        <View style={styles.header}>
          <View style={{ flex: 1 }}>
            <Text style={styles.kicker}>Familiar</Text>
            <Text style={styles.title}>司笔灵</Text>
          </View>
          {boot ? <ActivityIndicator color={theme.color.accent} size="small" /> : null}
        </View>

        <ScrollView
          ref={scrollRef}
          style={styles.body}
          contentContainerStyle={{ paddingVertical: theme.space(4), gap: theme.space(5) }}
          keyboardShouldPersistTaps="handled"
          onContentSizeChange={scrollToEnd}
        >
          {lines.length === 0 && !boot ? (
            <Animated.View entering={FadeIn.duration(500)} style={styles.invocation}>
              <Text style={styles.invocationGlyph}>✶</Text>
              <Text style={styles.invocationText}>
                以自然语言召唤司笔灵。它能润色章节、新增世界书条目、合并角色卡。{"\n\n"}
                诸如：「把第三章末尾的对话扩展两倍，加些雨声」、{"\n"}
                「为『北境议会』新建一条世界书」。
              </Text>
            </Animated.View>
          ) : null}

          {lines.map((line, i) => {
            if (line.role === "user") {
              return (
                <Animated.View
                  key={line.id}
                  entering={FadeInUp.delay(Math.min(i, 4) * 30).duration(360).springify().damping(20)}
                  style={styles.userStanza}
                >
                  <View style={styles.userRule} />
                  <Text style={styles.userText}>{line.content}</Text>
                </Animated.View>
              );
            }
            return (
              <Animated.View
                key={line.id}
                entering={FadeInUp.delay(Math.min(i, 4) * 30).duration(360)}
                style={styles.familiarLine}
              >
                {line.content ? (
                  <Text style={styles.familiarText}>{line.content}</Text>
                ) : line.streaming ? (
                  <View style={styles.thinking}>
                    <RuneCaret />
                    <Text style={styles.thinkingText}>司笔灵正在凝神…</Text>
                  </View>
                ) : null}
                {line.streaming && line.content ? <RuneCaret /> : null}
                {line.error ? <Text style={styles.error}>⚠ {line.error}</Text> : null}
              </Animated.View>
            );
          })}
        </ScrollView>

        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <View style={styles.composer}>
            <TextInput
              value={input}
              onChangeText={setInput}
              placeholder="对司笔灵低语…"
              placeholderTextColor={theme.color.textFaint}
              style={styles.input}
              multiline
              editable={!boot}
            />
            {streaming ? (
              <Pressable onPress={stop} style={[styles.sigil, styles.sigilStop]}>
                <View style={styles.stopMark} />
              </Pressable>
            ) : (
              <Pressable
                onPress={send}
                disabled={!input.trim() || boot}
                style={[styles.sigil, (!input.trim() || boot) && { opacity: 0.4 }]}
              >
                <Text style={styles.sigilGlyph}>✶</Text>
              </Pressable>
            )}
          </View>
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  fill: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: theme.color.scrim },
  sheet: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    maxHeight: "88%",
    minHeight: "60%",
    backgroundColor: palette.scrimDeep,
    borderTopLeftRadius: theme.radius.xl,
    borderTopRightRadius: theme.radius.xl,
    borderWidth: 1,
    borderColor: theme.color.surfaceLineStrong,
    overflow: "hidden",
    paddingHorizontal: theme.space(5),
    paddingTop: theme.space(3),
  },
  grabber: {
    alignSelf: "center",
    width: 44,
    height: 4,
    borderRadius: 2,
    backgroundColor: theme.color.surfaceLineStrong,
    marginBottom: theme.space(2),
  },
  header: { flexDirection: "row", alignItems: "center", paddingVertical: theme.space(2) },
  kicker: {
    fontFamily: theme.font.displaySemi,
    fontSize: theme.size.xs,
    letterSpacing: 4,
    textTransform: "uppercase",
    color: theme.color.accent,
  },
  title: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },

  body: { flex: 1, marginTop: theme.space(1) },

  // First-summoning invocation: not a placeholder, but a quiet ritual prologue.
  invocation: {
    alignItems: "center",
    gap: theme.space(3),
    paddingVertical: theme.space(10),
    paddingHorizontal: theme.space(4),
  },
  invocationGlyph: {
    fontSize: 38,
    color: theme.color.accent,
    opacity: 0.55,
    textShadowColor: theme.color.accentSoft,
    textShadowRadius: 14,
  },
  invocationText: {
    fontFamily: theme.font.proseItalic,
    fontSize: theme.size.base,
    color: theme.color.textFaint,
    textAlign: "center",
    lineHeight: 24,
  },

  // Player's whisper: right-anchored stanza in muted parchment, gilt-rule at left.
  userStanza: {
    flexDirection: "row",
    alignSelf: "flex-end",
    maxWidth: "82%",
    gap: theme.space(3),
    paddingLeft: theme.space(3),
  },
  userRule: {
    width: 2,
    backgroundColor: theme.color.accent,
    opacity: 0.55,
    borderRadius: 1,
    marginVertical: 2,
  },
  userText: {
    flex: 1,
    fontFamily: theme.font.proseItalic,
    fontSize: theme.size.md,
    color: palette.parchmentDim,
    lineHeight: 24,
    letterSpacing: 0.1,
  },

  // Familiar's reply: full-bleed serif inscription, no bubble chrome.
  familiarLine: { gap: theme.space(2) },
  familiarText: {
    fontFamily: theme.font.prose,
    fontSize: theme.size.md,
    color: theme.color.text,
    lineHeight: 27,
    letterSpacing: 0.15,
  },
  thinking: { flexDirection: "row", alignItems: "center", gap: theme.space(2) },
  thinkingText: {
    fontFamily: theme.font.proseItalic,
    fontSize: theme.size.sm,
    color: theme.color.textFaint,
    letterSpacing: 0.3,
  },
  caret: { fontSize: 13, color: theme.color.accentBright },
  error: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.danger, marginTop: theme.space(1) },

  // Composer: a parchment-toned input flanked by a candle-ember send sigil.
  composer: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: theme.space(3),
    paddingTop: theme.space(3),
    paddingBottom: theme.space(1),
    borderTopWidth: 1,
    borderTopColor: theme.color.surfaceLine,
  },
  input: {
    flex: 1,
    maxHeight: 132,
    minHeight: 48,
    backgroundColor: theme.color.bgInput,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    paddingHorizontal: theme.space(4),
    paddingTop: theme.space(3),
    paddingBottom: theme.space(3),
    color: theme.color.text,
    fontFamily: theme.font.prose,
    fontSize: theme.size.md,
    lineHeight: 22,
  },
  sigil: {
    width: 48,
    height: 48,
    borderRadius: theme.radius.pill,
    backgroundColor: theme.color.accent,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 1,
    shadowColor: theme.color.accent,
    shadowOpacity: 0.5,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 6,
  },
  sigilStop: { backgroundColor: theme.color.danger },
  sigilGlyph: { fontSize: 20, color: theme.color.bg, marginTop: -1 },
  stopMark: { width: 12, height: 12, borderRadius: 2, backgroundColor: theme.color.text },
});
