/**
 * Loom of Fate — the worldline panel, the platform's signature "event-driven design" made
 * visible. Three strata: 剧本期望线 (the novel's anchor chapters, marked 已度过/当前/待解锁
 * against current_chapter), 实际足迹线 (the phases you actually walked, with key events), and
 * 世界线收束 (the convergence pressure — average drift, per-phase pressure, and the pending
 * anchors the GM is steering toward, with 必发生 flags and a player-driven 标记已到达). Pulls
 * saves.timeline + saves.anchors; satisfying an anchor advances the worldline deterministically.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import Animated, { FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { saves } from "@/api";
import { ApiError } from "@/api/http";
import { theme, palette } from "@/theme/theme";

const STATUS_LABEL: Record<string, string> = { pending: "待发生", occurred: "已发生", variant: "变体", superseded: "绕过" };
const STATUS_COLOR: Record<string, string> = { pending: palette.parchmentFaint, occurred: palette.jade, variant: palette.ember, superseded: palette.blood };

function driftColor(pct: number): string {
  if (pct < 30) return palette.jade;
  if (pct < 70) return theme.color.accent;
  return theme.color.danger;
}

export default function WorldlineScreen() {
  const { id, title } = useLocalSearchParams<{ id: string; title?: string }>();
  const saveId = Number(id);
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [tl, setTl] = useState<any>(null);
  const [anc, setAnc] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [t, a] = await Promise.allSettled([saves.timeline(saveId), saves.anchors(saveId)]);
      if (t.status === "fulfilled") setTl(t.value);
      if (a.status === "fulfilled") setAnc(a.value);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, [saveId]);

  useEffect(() => { load(); }, [load]);

  const markReached = (anchorKey: string, summary: string) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    Alert.alert("标记已到达", `把「${summary || anchorKey}」标记为已发生？世界线进度将前进至该锚点所在章节。`, [
      { text: "取消", style: "cancel" },
      {
        text: "确认推进",
        onPress: async () => {
          setBusyKey(anchorKey);
          try {
            const r = await saves.satisfyAnchor(saveId, anchorKey);
            if (r?.ok === false) Alert.alert("无法推进", r.error || "该锚点当前不可手动推进。");
            else load();
          } catch (e) {
            Alert.alert("操作失败", e instanceof ApiError ? e.message : "请重试");
          } finally {
            setBusyKey(null);
          }
        },
      },
    ]);
  };

  const scriptAnchors: any[] = tl?.script_anchors ?? [];
  const phases: any[] = tl?.save_phases ?? [];
  const curChapter: number = tl?.current_chapter ?? 1;
  const summary = anc?.summary ?? {};
  const byPhase: any[] = anc?.by_phase ?? [];
  const pending: any[] = anc?.recent_pending ?? [];
  const occurred: any[] = anc?.recent_occurred ?? [];
  const avgDrift = Math.round((summary.avg_drift ?? 0) > 1 ? summary.avg_drift : (summary.avg_drift ?? 0) * 100);

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Worldline</Text>
          <Text style={styles.h1} numberOfLines={1}>{title || "世界线"}</Text>
        </View>
        <Pressable onPress={load} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.refreshGlyph}>⟳</Text>
        </Pressable>
      </View>

      {loading && !tl && !anc ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <ScrollView contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 50, gap: theme.space(6), paddingTop: theme.space(2) }}>
          {/* 剧本期望线 */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>剧本期望线</Text>
            {scriptAnchors.length === 0 ? (
              <Text style={styles.empty}>此剧本没有预设时间线。</Text>
            ) : (
              scriptAnchors.map((a, i) => {
                const chMin = a.chapter_min ?? a.chapter ?? a.source_chapter ?? 0;
                const past = chMin < curChapter;
                const current = chMin === curChapter || (a.chapter_max != null && curChapter >= chMin && curChapter <= a.chapter_max);
                const state = current ? "current" : past ? "past" : "locked";
                return (
                  <Animated.View key={i} entering={FadeInDown.delay(Math.min(i, 10) * 30).duration(320)} style={styles.expRow}>
                    <View style={[styles.expDot, state === "current" && styles.expDotCurrent, state === "past" && styles.expDotPast]} />
                    <View style={{ flex: 1 }}>
                      <View style={styles.expHead}>
                        <Text style={[styles.expTitle, state === "locked" && { color: theme.color.textFaint }]} numberOfLines={1}>
                          {a.scene || a.title || a.label || a.summary || `锚点 ${i + 1}`}
                        </Text>
                        {state === "current" ? <Text style={styles.curTag}>当前</Text> : null}
                      </View>
                      <Text style={styles.expMeta}>
                        {state === "past" ? "已度过" : state === "locked" ? "待解锁" : "进行中"}
                        {a.chapter_min != null ? ` · 第 ${a.chapter_min}${a.chapter_max != null && a.chapter_max !== a.chapter_min ? `–${a.chapter_max}` : ""} 章` : ""}
                      </Text>
                    </View>
                  </Animated.View>
                );
              })
            )}
          </View>

          {/* 实际足迹线 */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>实际足迹线</Text>
            {phases.length === 0 ? (
              <Text style={styles.empty}>暂无足迹。游戏推进数轮后，阶段会在此积累。</Text>
            ) : (
              phases.map((p, i) => {
                const events: any[] = Array.isArray(p.key_events) ? p.key_events : [];
                const active = i === phases.length - 1;
                return (
                  <Animated.View key={i} entering={FadeInDown.delay(Math.min(i, 10) * 30).duration(320)} style={styles.phaseCard}>
                    <View style={styles.phaseHead}>
                      <Text style={styles.phaseNum}>P{i + 1}</Text>
                      <Text style={styles.phaseLabel} numberOfLines={1}>{p.phase_label || p.story_time_label || `阶段 ${i + 1}`}</Text>
                      {active ? <Text style={styles.curTag}>进行中</Text> : null}
                    </View>
                    {p.story_time_label ? <Text style={styles.phaseTime}>{p.story_time_label}</Text> : null}
                    {p.summary ? <Text style={styles.phaseSummary} numberOfLines={3}>{p.summary}</Text> : null}
                    {events.length > 0 ? (
                      <View style={styles.eventList}>
                        {events.slice(0, 6).map((e, j) => (
                          <Text key={j} style={styles.eventLine} numberOfLines={2}>· {typeof e === "string" ? e : e.text || e.summary || JSON.stringify(e)}</Text>
                        ))}
                      </View>
                    ) : null}
                  </Animated.View>
                );
              })
            )}
          </View>

          {/* 世界线收束 */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>世界线收束 · 锚点</Text>
            {!anc || (summary.total ?? 0) === 0 ? (
              <Text style={styles.empty}>尚未生成时间线锚点。剧本初始化或缺少章节摘要时为空。</Text>
            ) : (
              <>
                <View style={styles.driftRow}>
                  <Text style={styles.driftLabel}>平均偏离度</Text>
                  <Text style={[styles.driftVal, { color: driftColor(avgDrift) }]}>{avgDrift}%</Text>
                </View>
                <View style={styles.driftTrack}><View style={[styles.driftFill, { width: `${avgDrift}%`, backgroundColor: driftColor(avgDrift) }]} /></View>
                <View style={styles.statRow}>
                  {[
                    { k: "待发生", v: summary.pending },
                    { k: "已发生", v: summary.occurred },
                    { k: "变体", v: summary.variant },
                    { k: "绕过", v: summary.superseded },
                  ].map((s) => (
                    <View key={s.k} style={styles.statCell}>
                      <Text style={styles.statVal}>{s.v ?? 0}</Text>
                      <Text style={styles.statKey}>{s.k}</Text>
                    </View>
                  ))}
                </View>

                <Text style={styles.subLabel}>待发生锚点</Text>
                {pending.length === 0 ? (
                  <Text style={styles.empty}>当前无待发生锚点。</Text>
                ) : (
                  pending.map((a, i) => {
                    const key = a.anchor_key || String(i);
                    const fatal = !!a.is_fatal;
                    return (
                      <View key={key} style={styles.anchorRow}>
                        <View style={{ flex: 1 }}>
                          <View style={styles.anchorHead}>
                            <Text style={styles.anchorSummary} numberOfLines={2}>{a.summary || key}</Text>
                            {fatal ? <Text style={styles.fatalTag}>必发生</Text> : null}
                          </View>
                          {a.chapter || a.source_chapter ? <Text style={styles.anchorMeta}>第 {a.chapter || a.source_chapter} 章{a.phase_label ? ` · ${a.phase_label}` : ""}</Text> : null}
                        </View>
                        {!fatal ? (
                          <Pressable onPress={() => markReached(key, a.summary)} disabled={busyKey === key} style={styles.reachBtn}>
                            {busyKey === key ? <ActivityIndicator size="small" color={theme.color.bg} /> : <Text style={styles.reachText}>标记已到达</Text>}
                          </Pressable>
                        ) : null}
                      </View>
                    );
                  })
                )}

                {occurred.length > 0 ? (
                  <>
                    <Text style={styles.subLabel}>近期已发生</Text>
                    {occurred.map((a, i) => (
                      <View key={a.anchor_key || i} style={styles.occRow}>
                        <View style={[styles.occDot, { backgroundColor: STATUS_COLOR[a.status] || palette.jade }]} />
                        <Text style={styles.occSummary} numberOfLines={2}>{a.summary || a.anchor_key}</Text>
                        <Text style={styles.occStatus}>{STATUS_LABEL[a.status] || a.status}</Text>
                      </View>
                    ))}
                  </>
                ) : null}
              </>
            )}
          </View>
        </ScrollView>
      )}
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  refreshGlyph: { fontSize: 22, color: theme.color.textDim },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  section: { gap: theme.space(3) },
  sectionTitle: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 3, textTransform: "uppercase", color: theme.color.accent },
  empty: { fontFamily: theme.font.proseItalic, fontSize: theme.size.base, color: theme.color.textFaint, lineHeight: 22, paddingVertical: theme.space(2) },
  expRow: { flexDirection: "row", gap: theme.space(3) },
  expDot: { width: 12, height: 12, borderRadius: 6, borderWidth: 2, borderColor: theme.color.surfaceLineStrong, backgroundColor: theme.color.bg, marginTop: 3 },
  expDotCurrent: { borderColor: theme.color.accent, backgroundColor: theme.color.accent },
  expDotPast: { borderColor: palette.jade, backgroundColor: palette.jade },
  expHead: { flexDirection: "row", alignItems: "center", gap: theme.space(2) },
  expTitle: { flex: 1, fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  expMeta: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint, marginTop: 1 },
  curTag: { fontFamily: theme.font.displaySemi, fontSize: 10, letterSpacing: 1, color: theme.color.accentBright, textTransform: "uppercase" },
  phaseCard: { padding: theme.space(4), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard, gap: theme.space(2) },
  phaseHead: { flexDirection: "row", alignItems: "center", gap: theme.space(3) },
  phaseNum: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.accent },
  phaseLabel: { flex: 1, fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  phaseTime: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.accent },
  phaseSummary: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim, lineHeight: 21 },
  eventList: { gap: theme.space(1), marginTop: theme.space(1), paddingTop: theme.space(2), borderTopWidth: 1, borderTopColor: theme.color.surfaceLine },
  eventLine: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 19 },
  driftRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "baseline" },
  driftLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.text },
  driftVal: { fontFamily: theme.font.mono, fontSize: theme.size.md },
  driftTrack: { height: 8, borderRadius: 4, backgroundColor: theme.color.bgInput, borderWidth: 1, borderColor: theme.color.surfaceLine, overflow: "hidden" },
  driftFill: { height: "100%" },
  statRow: { flexDirection: "row", gap: theme.space(2), marginTop: theme.space(1) },
  statCell: { flex: 1, alignItems: "center", paddingVertical: theme.space(3), borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  statVal: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.accentBright },
  statKey: { fontFamily: theme.font.displaySemi, fontSize: 10, letterSpacing: 1, color: theme.color.textFaint },
  subLabel: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 2, textTransform: "uppercase", color: theme.color.textFaint, marginTop: theme.space(3) },
  anchorRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), paddingVertical: theme.space(3), borderBottomWidth: 1, borderBottomColor: theme.color.surfaceLine },
  anchorHead: { flexDirection: "row", alignItems: "center", gap: theme.space(2) },
  anchorSummary: { flex: 1, fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.text, lineHeight: 21 },
  fatalTag: { fontFamily: theme.font.displaySemi, fontSize: 10, letterSpacing: 1, color: theme.color.danger, textTransform: "uppercase" },
  anchorMeta: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint, marginTop: 1 },
  reachBtn: { paddingHorizontal: theme.space(3), paddingVertical: theme.space(2), borderRadius: theme.radius.pill, backgroundColor: theme.color.accent },
  reachText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 0.5, color: theme.color.bg },
  occRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), paddingVertical: theme.space(2) },
  occDot: { width: 8, height: 8, borderRadius: 4 },
  occSummary: { flex: 1, fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim },
  occStatus: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint },
});
