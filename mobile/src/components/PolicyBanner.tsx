/**
 * Proclamation — a dismissible banner for pending policy notices (terms/privacy updates
 * the operator has dispatched). Polls /api/policy/notices; if any are pending, it unfurls
 * a thin ember-edged ribbon at the top of home. Tapping opens the linked document; the ✕
 * dismisses it locally for the session (the authoritative ack still lives server-side).
 */
import React, { useEffect, useState } from "react";
import { Linking, Pressable, StyleSheet, Text, View } from "react-native";
import Animated, { FadeInDown } from "react-native-reanimated";
import { compliance } from "@/api";
import { theme } from "@/theme/theme";

type Notice = { id?: number | string; title?: string; summary?: string; url?: string; slug?: string; [k: string]: unknown };

export function PolicyBanner() {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await compliance.policyNotices();
        const list = r?.notices ?? [];
        if (alive && list.length > 0) setNotice(list[0]);
      } catch {
        /* silent — banner is non-essential */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (!notice || dismissed) return null;

  const open = () => {
    if (notice.url) Linking.openURL(notice.url).catch(() => {});
  };

  return (
    <Animated.View entering={FadeInDown.duration(420)} style={styles.banner}>
      <Pressable onPress={open} style={styles.content} hitSlop={4}>
        <Text style={styles.glyph}>❡</Text>
        <View style={{ flex: 1 }}>
          <Text style={styles.title} numberOfLines={1}>{notice.title || "政策更新"}</Text>
          {notice.summary ? <Text style={styles.summary} numberOfLines={2}>{notice.summary}</Text> : null}
        </View>
      </Pressable>
      <Pressable onPress={() => setDismissed(true)} hitSlop={10} style={styles.close}>
        <Text style={styles.closeGlyph}>✕</Text>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: "row",
    alignItems: "center",
    marginHorizontal: theme.space(5),
    marginBottom: theme.space(2),
    paddingVertical: theme.space(3),
    paddingHorizontal: theme.space(4),
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.color.accentSoft,
    backgroundColor: theme.color.accentGhost,
    gap: theme.space(3),
  },
  content: { flex: 1, flexDirection: "row", alignItems: "center", gap: theme.space(3) },
  glyph: { fontSize: 18, color: theme.color.accentBright },
  title: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.text },
  summary: { fontFamily: theme.font.prose, fontSize: theme.size.xs, color: theme.color.textDim, lineHeight: 16, marginTop: 1 },
  close: { padding: theme.space(1) },
  closeGlyph: { fontSize: 14, color: theme.color.textFaint },
});
