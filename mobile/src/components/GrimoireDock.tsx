/**
 * GrimoireDock — a 5-slot bottom navigation. Each slot pairs a candleglyph with a Chinese
 * label so the meaning never depends on the user inferring an emoji. Active slot lights
 * ember-bright with a subtle underline. Non-active slots stay parchment-faint.
 *
 * Mount this at the foot of any top-level screen (chats, console, apparatus, personas,
 * settings). It uses expo-router's router.replace to switch tabs without stacking — the
 * back gesture should not retrace through tabs.
 *
 * Layout: a fixed-bottom row with a thin top hairline and ink-deep backing. Safe-area
 * inset is handled by the consumer — the dock itself sits flush; consumers add bottom
 * padding to their scroll content to clear it (use `DOCK_HEIGHT`).
 */
import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter, usePathname } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { theme } from "@/theme/theme";

export const DOCK_HEIGHT = 64;

type Slot = {
  /** Pathname prefix that marks this slot active. */
  match: string;
  /** Route to navigate to when tapped. */
  route: string;
  /** Single-glyph icon. */
  glyph: string;
  label: string;
};

// Order matches the spec: 对话 / 剧本 / 工坊 / 角色 / 我的.
// Glyphs picked to fit the Candlelit Grimoire vocabulary — quill, scroll, anvil, mask,
// candle. Always pair with the label so meaning is explicit.
const SLOTS: Slot[] = [
  { match: "/chats", route: "/(app)/chats", glyph: "✒", label: "对话" },
  { match: "/console", route: "/(app)/console", glyph: "❧", label: "剧本" },
  { match: "/apparatus", route: "/(app)/apparatus", glyph: "⚒", label: "工坊" },
  { match: "/personas", route: "/(app)/personas", glyph: "◐", label: "角色" },
  { match: "/settings", route: "/(app)/settings", glyph: "✶", label: "我的" },
];

export function GrimoireDock() {
  const router = useRouter();
  const pathname = usePathname() || "";
  const insets = useSafeAreaInsets();

  return (
    <View style={[styles.dock, { paddingBottom: Math.max(insets.bottom, 6) }]}>
      <View style={styles.hairline} />
      <View style={styles.row}>
        {SLOTS.map((s) => {
          const active = pathname.startsWith(s.match);
          return (
            <Pressable
              key={s.match}
              onPress={() => {
                if (active) return;
                // Replace (not push) so back gesture doesn't bounce through tabs.
                router.replace(s.route as any);
              }}
              style={styles.slot}
              hitSlop={4}
              accessibilityRole={"tab"}
              accessibilityLabel={s.label}
              accessibilityState={active ? { selected: true } : undefined}
            >
              <Text style={[styles.glyph, active && styles.glyphActive]}>{s.glyph}</Text>
              <Text style={[styles.label, active && styles.labelActive]}>{s.label}</Text>
              {active ? <View style={styles.ember} /> : null}
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  dock: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: theme.color.bg,
  },
  hairline: {
    height: 1,
    backgroundColor: theme.color.surfaceLine,
  },
  row: {
    flexDirection: "row",
    height: DOCK_HEIGHT - 4,
    alignItems: "stretch",
  },
  slot: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingTop: 6,
    gap: 2,
  },
  glyph: {
    fontSize: 18,
    color: theme.color.textFaint,
    lineHeight: 22,
  },
  glyphActive: {
    color: theme.color.accentBright,
    textShadowColor: theme.color.accent,
    textShadowRadius: 8,
  },
  label: {
    fontFamily: theme.font.proseSemi,
    fontSize: 10,
    letterSpacing: 1.5,
    color: theme.color.textFaint,
  },
  labelActive: {
    color: theme.color.accent,
  },
  ember: {
    position: "absolute",
    bottom: 0,
    width: 18,
    height: 2,
    borderRadius: 1,
    backgroundColor: theme.color.accent,
    shadowColor: theme.color.accent,
    shadowOpacity: 0.7,
    shadowRadius: 4,
  },
});
