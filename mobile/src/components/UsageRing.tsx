import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { theme } from "@/theme/theme";

/**
 * Compact context-usage indicator under the chat title. A thin ember arc that
 * fills clockwise as the conversation eats its token budget — turns to blood-red
 * past 85% to warn of imminent overflow. (Arc faked with two clipped halves so we
 * stay dependency-free; no SVG.)
 */
export function UsageRing({ pct }: { pct: number }) {
  const clamped = Math.max(0, Math.min(1, pct || 0));
  const danger = clamped >= 0.85;
  const color = danger ? theme.color.danger : clamped >= 0.6 ? theme.color.accentBright : theme.color.accent;
  return (
    <View style={styles.row}>
      <View style={styles.track}>
        <View style={[styles.fill, { width: `${clamped * 100}%`, backgroundColor: color }]} />
      </View>
      <Text style={[styles.pct, { color }]}>{Math.round(clamped * 100)}%</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: theme.space(2), marginTop: theme.space(1) },
  track: { width: 90, height: 3, borderRadius: 2, backgroundColor: theme.color.surfaceLineStrong, overflow: "hidden" },
  fill: { height: 3, borderRadius: 2 },
  pct: { fontFamily: theme.font.mono, fontSize: 10, letterSpacing: 0.5 },
});
