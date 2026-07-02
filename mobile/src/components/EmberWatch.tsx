/**
 * Ember Watch — the background-task floater. Long-running jobs (script import, knowledge
 * rebuild, image conjuring) run server-side; this corner ember pulses while any are in
 * flight and expands to a stack of job cards on tap. Polls /api/me/tasks/active on a slow
 * cadence (cache-friendly), auto-hides when the forge goes quiet. Mounted globally so it
 * follows the player across every screen, exactly as the desktop floater does.
 */
import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeIn, FadeOut, Easing, useAnimatedStyle, useSharedValue, withRepeat, withTiming, cancelAnimation } from "react-native-reanimated";
import { tasks, ActiveTask } from "@/api";
import { theme, palette } from "@/theme/theme";

const KIND_LABEL: Record<string, string> = {
  import: "剧本导入",
  rebuild: "知识重建",
  image: "生图",
  imagegen: "生图",
};

function labelFor(t: ActiveTask): string {
  if (t.title || t.label) return String(t.title || t.label);
  const kind = String(t.kind || "");
  for (const k of Object.keys(KIND_LABEL)) if (kind.includes(k)) return KIND_LABEL[k];
  return kind || "后台任务";
}

function isActive(t: ActiveTask): boolean {
  const s = String(t.status || "").toLowerCase();
  return s === "running" || s === "pending" || s === "queued" || s === "processing" || s === "";
}

function Pulse() {
  const o = useSharedValue(0.5);
  useEffect(() => {
    o.value = withRepeat(withTiming(1, { duration: 850, easing: Easing.inOut(Easing.ease) }), -1, true);
    return () => cancelAnimation(o);
  }, [o]);
  const style = useAnimatedStyle(() => ({ opacity: o.value }));
  return <Animated.View style={[styles.pulseDot, style]} />;
}

export function EmberWatch() {
  const insets = useSafeAreaInsets();
  const [list, setList] = useState<ActiveTask[]>([]);
  const [expanded, setExpanded] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await tasks.active();
        if (!alive) return;
        const all = r?.tasks ?? r?.items ?? [];
        const active = all.filter(isActive);
        setList(active);
        if (active.length === 0) setExpanded(false);
      } catch {
        /* silent — floater is non-essential */
      }
    };
    poll();
    timer.current = setInterval(poll, 4500);
    return () => {
      alive = false;
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  if (list.length === 0) return null;

  return (
    <View style={[styles.root, { bottom: insets.bottom + theme.space(20) }]} pointerEvents="box-none">
      {expanded ? (
        <Animated.View entering={FadeIn.duration(220)} exiting={FadeOut.duration(160)} style={styles.stack}>
          {list.map((t, i) => {
            const pct = typeof t.progress === "number" ? Math.max(0, Math.min(100, Math.round(t.progress > 1 ? t.progress : t.progress * 100))) : null;
            return (
              <View key={String(t.job_id ?? i)} style={styles.card}>
                <View style={styles.cardHead}>
                  <ActivityIndicator size="small" color={theme.color.accent} />
                  <Text style={styles.cardTitle} numberOfLines={1}>{labelFor(t)}</Text>
                  {pct != null ? <Text style={styles.cardPct}>{pct}%</Text> : null}
                </View>
                {pct != null ? (
                  <View style={styles.track}><View style={[styles.fill, { width: `${pct}%` }]} /></View>
                ) : (
                  <Text style={styles.cardStatus}>{t.status || "进行中…"}</Text>
                )}
              </View>
            );
          })}
        </Animated.View>
      ) : null}

      <Pressable onPress={() => setExpanded((v) => !v)} style={styles.pill}>
        <Pulse />
        <Text style={styles.pillText}>{list.length} 项任务</Text>
        <Text style={styles.pillChevron}>{expanded ? "▾" : "▸"}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { position: "absolute", right: theme.space(5), alignItems: "flex-end", gap: theme.space(2) },
  stack: { gap: theme.space(2), alignItems: "flex-end" },
  card: { width: 230, padding: theme.space(3), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLineStrong, backgroundColor: palette.scrimCard, gap: theme.space(2) },
  cardHead: { flexDirection: "row", alignItems: "center", gap: theme.space(2) },
  cardTitle: { flex: 1, fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.text },
  cardPct: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.accent },
  cardStatus: { fontFamily: theme.font.prose, fontSize: theme.size.xs, color: theme.color.textFaint },
  track: { height: 4, borderRadius: 2, backgroundColor: theme.color.bgInput, overflow: "hidden" },
  fill: { height: "100%", backgroundColor: theme.color.accent },
  pill: { flexDirection: "row", alignItems: "center", gap: theme.space(2), paddingHorizontal: theme.space(4), paddingVertical: theme.space(2.5), borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.accentSoft, backgroundColor: palette.scrimCard, shadowColor: theme.color.accent, shadowOpacity: 0.4, shadowRadius: 12, elevation: 8 },
  pulseDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: theme.color.accentBright },
  pillText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.text },
  pillChevron: { fontSize: 12, color: theme.color.textFaint },
});
