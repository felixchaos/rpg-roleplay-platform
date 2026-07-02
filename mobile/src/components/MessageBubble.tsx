import React, { useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
  cancelAnimation,
} from "react-native-reanimated";
import { theme } from "@/theme/theme";
import { ChatMessage, ToolEvent } from "@/state/chatModel";

/** A candle-bright caret that breathes while the GM is still writing. */
function StreamCaret() {
  const o = useSharedValue(1);
  useEffect(() => {
    o.value = withRepeat(withTiming(0.15, { duration: 620, easing: Easing.inOut(Easing.ease) }), -1, true);
    return () => cancelAnimation(o);
  }, [o]);
  const style = useAnimatedStyle(() => ({ opacity: o.value }));
  return <Animated.Text style={[styles.caret, style]}>▍</Animated.Text>;
}

function ToolBlock({ tools }: { tools: ToolEvent[] }) {
  const [open, setOpen] = useState(false);
  if (!tools.length) return null;
  return (
    <View style={styles.toolWrap}>
      <Pressable onPress={() => setOpen((v) => !v)} style={styles.toolHeader} accessibilityRole={"button"} accessibilityLabel={`${tools.length} 个工具调用`} accessibilityState={{expanded: open}}>
        <Text style={styles.toolSigil}>⌬</Text>
        <Text style={styles.toolHeaderText}>
          {tools.length} 个工具调用 {open ? "▾" : "▸"}
        </Text>
      </Pressable>
      {open ? (
        <View style={styles.toolBody}>
          {tools.map((t, i) => (
            <View key={`${t.kind}-${t.name}-${i}`} style={styles.toolRow}>
              <Text style={styles.toolName}>
                {t.kind === "call" ? "→ " : "← "}
                {t.name}
              </Text>
              {t.payload != null ? (
                <Text style={styles.toolPayload} numberOfLines={6}>
                  {typeof t.payload === "string" ? t.payload : JSON.stringify(t.payload, null, 2)}
                </Text>
              ) : null}
            </View>
          ))}
        </View>
      ) : null}
    </View>
  );
}

export function MessageBubble({
  message,
  onLongPress,
  onRetry,
}: {
  message: ChatMessage;
  onLongPress?: (m: ChatMessage) => void;
  onRetry?: () => void;
}) {
  const isUser = message.role === "user";
  const canEdit = !!onLongPress && message.messageIndex != null && !message.pending;
  const longPress = canEdit ? () => onLongPress!(message) : undefined;

  if (isUser) {
    return (
      <Pressable onLongPress={longPress} delayLongPress={350} style={styles.userRow} accessibilityRole={"text"} accessibilityLabel={message.text || "用户消息"}>
        <View style={styles.userBubble}>
          <Text style={styles.userText}>{message.text}</Text>
          {message.error ? <Text style={styles.errText}>⚠ {message.error}</Text> : null}
        </View>
      </Pressable>
    );
  }

  // GM / character prose — full-width, novel-like, no bubble chrome.
  return (
    <Pressable onLongPress={longPress} delayLongPress={350} style={styles.gmWrap} accessibilityRole={"text"} accessibilityLabel={message.text || "GM 消息"}>
      <View style={styles.gmMarker} />
      <View style={{ flex: 1 }}>
        {message.tools && message.tools.length > 0 ? <ToolBlock tools={message.tools} /> : null}
        {message.text ? (
          <Text style={styles.gmText}>{message.text}</Text>
        ) : message.pending ? (
          <Text style={styles.thinking}>正在落笔…</Text>
        ) : null}
        {message.pending && message.text ? <StreamCaret /> : null}
        {message.error ? (
          <View style={styles.errRow}>
            <Text style={styles.errText}>⚠ {message.error}</Text>
            {onRetry ? (
              <Pressable onPress={onRetry} hitSlop={8} style={styles.retryBtn}>
                <Text style={styles.retryText}>重试</Text>
              </Pressable>
            ) : null}
          </View>
        ) : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  userRow: { alignItems: "flex-end", marginVertical: theme.space(2) },
  userBubble: {
    maxWidth: "82%",
    backgroundColor: theme.color.accentGhost,
    borderColor: theme.color.accentSoft,
    borderWidth: 1,
    borderRadius: theme.radius.lg,
    borderTopRightRadius: theme.radius.sm,
    paddingHorizontal: theme.space(4),
    paddingVertical: theme.space(3),
  },
  userText: { fontFamily: theme.font.proseMedium, fontSize: theme.size.md, color: theme.color.accentBright, lineHeight: 23 },

  gmWrap: { flexDirection: "row", gap: theme.space(3), marginVertical: theme.space(3), paddingRight: theme.space(2) },
  gmMarker: { width: 2, borderRadius: 1, backgroundColor: theme.color.surfaceLineStrong, marginTop: theme.space(1) },
  gmText: { fontFamily: theme.font.prose, fontSize: theme.size.lg, color: theme.color.text, lineHeight: 30, letterSpacing: 0.2 },
  thinking: { fontFamily: theme.font.proseItalic, fontSize: theme.size.md, color: theme.color.textFaint },
  caret: { color: theme.color.accent, fontSize: theme.size.lg },
  errText: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.danger, marginTop: theme.space(2) },
  errRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), marginTop: theme.space(2) },
  retryBtn: { paddingHorizontal: theme.space(3), paddingVertical: theme.space(1.5), borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  retryText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.accentBright },

  toolWrap: { marginBottom: theme.space(3), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.magicSoft, backgroundColor: "rgba(157,123,216,0.06)", overflow: "hidden" },
  toolHeader: { flexDirection: "row", alignItems: "center", gap: theme.space(2), paddingHorizontal: theme.space(3), paddingVertical: theme.space(2) },
  toolSigil: { color: theme.color.magic, fontSize: theme.size.sm },
  toolHeaderText: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.magicDim, letterSpacing: 1 },
  toolBody: { paddingHorizontal: theme.space(3), paddingBottom: theme.space(3), gap: theme.space(2) },
  toolRow: { gap: theme.space(1) },
  toolName: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.magic },
  toolPayload: { fontFamily: theme.font.mono, fontSize: 10, color: theme.color.textFaint, lineHeight: 15 },
});
