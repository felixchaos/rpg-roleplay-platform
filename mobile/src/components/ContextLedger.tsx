/**
 * Context Ledger — what's filling the model's context window this turn. The backend
 * returns labeled segments (history / system prompt / retrieval / memory / cards / …)
 * with token counts; we render them as a stacked ribbon plus an itemized list, so the
 * player can see exactly what the GM is "holding in mind" — and why a turn might overflow.
 */
import React, { useEffect, useState } from "react";
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { BlurView } from "expo-blur";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeIn } from "react-native-reanimated";
import { game } from "@/api";
import { theme, palette } from "@/theme/theme";

type Segment = { key?: string; label?: string; tokens?: number; pct?: number; color?: string };

// A small ember→arcane palette to color segments deterministically by position.
const SEG_COLORS = ["#e8923a", "#c9762b", "#9d7bd8", "#6fae87", "#b8453a", "#7d6f59", "#ffb55c"];

export function ContextLedger({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const insets = useSafeAreaInsets();
  const [loading, setLoading] = useState(false);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [total, setTotal] = useState(0);
  const [limit, setLimit] = useState(0);

  useEffect(() => {
    if (!visible) return;
    setLoading(true);
    (async () => {
      try {
        const r = await game.contextBreakdown();
        const segs: Segment[] = (r?.breakdown ?? []).filter((s: Segment) => (s.tokens ?? 0) > 0);
        setSegments(segs);
        setTotal(r?.total_tokens ?? segs.reduce((a, s) => a + (s.tokens ?? 0), 0));
        setLimit(r?.ctx_limit ?? 0);
      } catch {
        setSegments([]);
      } finally {
        setLoading(false);
      }
    })();
  }, [visible]);

  const usedPct = limit > 0 ? Math.min(100, Math.round((total / limit) * 100)) : 0;

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
        <BlurView intensity={30} tint="dark" style={styles.fill} />
        <View style={styles.grabber} />
        <Text style={styles.title}>上下文账本</Text>

        {loading ? (
          <ActivityIndicator color={theme.color.accent} style={{ marginVertical: theme.space(10) }} />
        ) : segments.length === 0 ? (
          <Text style={styles.empty}>暂无上下文数据。发起一轮对话后再来查看。</Text>
        ) : (
          <ScrollView style={styles.body}>
            {/* headline */}
            <View style={styles.headline}>
              <Text style={styles.bigNum}>{total.toLocaleString()}</Text>
              <Text style={styles.bigUnit}>
                tokens{limit > 0 ? ` / ${limit.toLocaleString()}  ·  ${usedPct}%` : ""}
              </Text>
            </View>

            {/* stacked ribbon */}
            <View style={styles.ribbon}>
              {segments.map((s, i) => {
                const frac = total > 0 ? (s.tokens ?? 0) / total : 0;
                if (frac <= 0) return null;
                return (
                  <View
                    key={s.key || i}
                    style={{ width: `${frac * 100}%`, backgroundColor: s.color || SEG_COLORS[i % SEG_COLORS.length] }}
                  />
                );
              })}
            </View>

            {/* legend */}
            <View style={{ gap: theme.space(1), marginTop: theme.space(4) }}>
              {segments.map((s, i) => {
                const pct = s.pct ?? (total > 0 ? Math.round(((s.tokens ?? 0) / total) * 100) : 0);
                return (
                  <Animated.View key={s.key || i} entering={FadeIn.delay(i * 40).duration(300)} style={styles.legendRow}>
                    <View style={[styles.swatch, { backgroundColor: s.color || SEG_COLORS[i % SEG_COLORS.length] }]} />
                    <Text style={styles.legendLabel} numberOfLines={1}>{s.label || s.key || "—"}</Text>
                    <Text style={styles.legendTokens}>{(s.tokens ?? 0).toLocaleString()}</Text>
                    <Text style={styles.legendPct}>{pct}%</Text>
                  </Animated.View>
                );
              })}
            </View>
          </ScrollView>
        )}
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
    maxHeight: "76%",
    backgroundColor: palette.scrimCard90,
    borderTopLeftRadius: theme.radius.xl,
    borderTopRightRadius: theme.radius.xl,
    borderWidth: 1,
    borderColor: theme.color.surfaceLineStrong,
    overflow: "hidden",
    paddingHorizontal: theme.space(5),
    paddingTop: theme.space(3),
  },
  grabber: { alignSelf: "center", width: 44, height: 4, borderRadius: 2, backgroundColor: theme.color.surfaceLineStrong, marginBottom: theme.space(3) },
  title: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.text, letterSpacing: 1, marginBottom: theme.space(3) },
  empty: { fontFamily: theme.font.proseItalic, fontSize: theme.size.md, color: theme.color.textFaint, textAlign: "center", paddingVertical: theme.space(10) },
  body: { maxHeight: 440 },
  headline: { flexDirection: "row", alignItems: "baseline", gap: theme.space(2), marginBottom: theme.space(3) },
  bigNum: { fontFamily: theme.font.display, fontSize: theme.size.xxl, color: theme.color.accentBright },
  bigUnit: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.textDim },
  ribbon: { flexDirection: "row", height: 16, borderRadius: theme.radius.sm, overflow: "hidden", borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgInput },
  legendRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), paddingVertical: theme.space(2), borderBottomWidth: 1, borderBottomColor: theme.color.surfaceLine },
  swatch: { width: 12, height: 12, borderRadius: 3 },
  legendLabel: { flex: 1, fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.text },
  legendTokens: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.textDim },
  legendPct: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.accent, width: 44, textAlign: "right" },
});
