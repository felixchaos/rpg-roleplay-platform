/**
 * Chronicle — the player's profile. Three movements down the page: an identity crest
 * with login-streak, a stat ledger (rounds / branches / words), an achievement wall,
 * and a usage sparkline. Pure-RN bars (no chart lib) keep it dependency-free. Numbers
 * the backend can't source come back null → rendered as "—", never faked.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { profile, ProfileStats, Achievement, User } from "@/api";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

export default function ProfileScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [user, setUser] = useState<User | null>(null);
  const [stats, setStats] = useState<ProfileStats | null>(null);
  const [achievements, setAchievements] = useState<Achievement[]>([]);
  const [usage, setUsage] = useState<{ label: string; value: number }[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [p, s, a, u] = await Promise.allSettled([
        profile.get(),
        profile.stats(),
        profile.achievements().catch(() => profile.achievementsCatalog()),
        profile.usageTimeline(14, "day"),
      ]);
      if (p.status === "fulfilled") setUser(p.value?.user ?? null);
      if (s.status === "fulfilled") setStats(s.value);
      if (a.status === "fulfilled") setAchievements(a.value?.items ?? []);
      if (u.status === "fulfilled") {
        const raw: any[] = u.value?.series ?? u.value?.daily_breakdown ?? [];
        setUsage(
          raw.slice(-14).map((d: any) => ({
            label: String(d.date || d.day || d.label || "").slice(5),
            value: Number(d.total_tokens ?? d.tokens ?? d.cost_usd ?? d.value ?? 0),
          })),
        );
      }
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const unlocked = achievements.filter((a) => a.unlocked).length;
  const maxUsage = Math.max(1, ...usage.map((u) => u.value));

  const STAT_CARDS = [
    { label: "回合", value: stats?.total_rounds },
    { label: "分支", value: stats?.branches ?? stats?.branch_nodes },
    { label: "最深分叉", value: stats?.max_branch_depth },
    { label: "存档", value: stats?.saves_count },
    { label: "导入字数", value: stats?.imported?.words, fmt: (n: number) => `${(n / 10000).toFixed(1)}万` },
    { label: "剧本", value: stats?.imported?.scripts },
  ];

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Chronicle</Text>
          <Text style={styles.h1}>游侠纪</Text>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <ScrollView contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 50, gap: theme.space(6), paddingTop: theme.space(2) }}>
          {/* crest */}
          <Animated.View entering={FadeInDown.duration(420)} style={styles.crest}>
            <View style={styles.crestSigil}>
              <Text style={styles.crestInitial}>{(user?.display_name || user?.username || "?").charAt(0).toUpperCase()}</Text>
            </View>
            <Text style={styles.crestName}>{user?.display_name || user?.username || "无名旅人"}</Text>
            <View style={styles.streakRow}>
              <Text style={styles.flame}>🜂</Text>
              <Text style={styles.streakText}>
                连续登录 {stats?.login_streak ?? 0} 天
                {stats?.longest_login_streak ? `  ·  最长 ${stats.longest_login_streak} 天` : ""}
              </Text>
            </View>
          </Animated.View>

          {/* stat ledger */}
          <View style={styles.statGrid}>
            {STAT_CARDS.map((s, i) => (
              <Animated.View key={s.label} entering={FadeInDown.delay(80 + i * 40).duration(380)} style={styles.statCard}>
                <Text style={styles.statValue}>
                  {s.value == null ? "—" : (s as any).fmt ? (s as any).fmt(s.value) : s.value}
                </Text>
                <Text style={styles.statLabel}>{s.label}</Text>
              </Animated.View>
            ))}
          </View>

          {/* usage sparkline */}
          {usage.length > 0 ? (
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>近 14 日用量</Text>
              <View style={styles.chart}>
                {usage.map((d, i) => (
                  <View key={i} style={styles.barCol}>
                    <View style={[styles.bar, { height: Math.max(3, (d.value / maxUsage) * 90) }]} />
                  </View>
                ))}
              </View>
            </View>
          ) : null}

          {/* achievement wall */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>
              成就 {achievements.length ? `· ${unlocked}/${achievements.length}` : ""}
            </Text>
            {achievements.length === 0 ? (
              <Text style={styles.muted}>暂无成就数据。</Text>
            ) : (
              <View style={styles.achWrap}>
                {achievements.map((a, i) => {
                  const locked = !a.unlocked;
                  return (
                    <Animated.View key={a.id || i} entering={FadeInDown.delay(i * 30).duration(320)} style={[styles.achChip, locked && styles.achLocked]}>
                      <Text style={[styles.achIcon, locked && { opacity: 0.4 }]}>{a.unlocked ? "✦" : "✧"}</Text>
                      <Text style={[styles.achName, locked && { color: theme.color.textFaint }]} numberOfLines={1}>
                        {a.hidden && locked ? "？？？" : a.title || a.name || a.id}
                      </Text>
                    </Animated.View>
                  );
                })}
              </View>
            )}
          </View>

          <Pressable onPress={() => router.push("/(app)/personas")} style={styles.linkRow}>
            <Text style={styles.linkText}>管理我的身份 ›</Text>
          </Pressable>
        </ScrollView>
      )}
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  crest: { alignItems: "center", gap: theme.space(3), paddingTop: theme.space(4) },
  crestSigil: { width: 88, height: 88, borderRadius: 44, backgroundColor: theme.color.bgCard, borderWidth: 2, borderColor: theme.color.accentSoft, alignItems: "center", justifyContent: "center", shadowColor: theme.color.accent, shadowOpacity: 0.4, shadowRadius: 18, elevation: 8 },
  crestInitial: { fontFamily: theme.font.display, fontSize: theme.size.xxl, color: theme.color.accentBright },
  crestName: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  streakRow: { flexDirection: "row", alignItems: "center", gap: theme.space(2) },
  flame: { fontSize: 16 },
  streakText: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim },
  statGrid: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(3) },
  statCard: { width: "31%", flexGrow: 1, backgroundColor: theme.color.bgCard, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, paddingVertical: theme.space(4), alignItems: "center", gap: theme.space(1) },
  statValue: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.accentBright },
  statLabel: { fontFamily: theme.font.displaySemi, fontSize: 10, letterSpacing: 1.5, textTransform: "uppercase", color: theme.color.textFaint },
  section: { gap: theme.space(3) },
  sectionTitle: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 3, textTransform: "uppercase", color: theme.color.accent },
  chart: { flexDirection: "row", alignItems: "flex-end", gap: 4, height: 100, paddingTop: theme.space(2) },
  barCol: { flex: 1, alignItems: "center", justifyContent: "flex-end" },
  bar: { width: "70%", borderRadius: 2, backgroundColor: theme.color.accent, opacity: 0.85 },
  muted: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint },
  achWrap: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(2) },
  achChip: { flexDirection: "row", alignItems: "center", gap: theme.space(2), paddingHorizontal: theme.space(3), paddingVertical: theme.space(2), borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  achLocked: { borderColor: theme.color.surfaceLine, backgroundColor: "transparent" },
  achIcon: { fontSize: 14, color: theme.color.accentBright },
  achName: { fontFamily: theme.font.proseMedium, fontSize: theme.size.sm, color: theme.color.text, maxWidth: 140 },
  linkRow: { paddingVertical: theme.space(3), alignItems: "center" },
  linkText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.accentBright },
});
