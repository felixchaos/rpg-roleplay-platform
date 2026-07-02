/**
 * Attunements — account-level preferences that shape how the engine behaves for you,
 * everywhere. These persist server-side via /api/me/preference, so they follow you
 * across clients. Toggles here are real behavioral switches (the extractor + black-swan
 * sub-agents, autosave), not cosmetics — each carries a one-line note on what it changes.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { prefs } from "@/api";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

type ToggleDef = { key: string; label: string; note: string };

const TOGGLES: ToggleDef[] = [
  { key: "autosave", label: "自动存档", note: "每回合后自动保存进度，无需手动落子。" },
  { key: "extractor.enabled", label: "提取子代理", note: "对话后台自动抽取角色卡 / 世界书 / 时间线。" },
  { key: "black_swan.enabled", label: "黑天鹅事件", note: "允许引擎在叙事中投下意料之外的转折。" },
  { key: "gc.keepFailedTurn", label: "保留未响应对话", note: "生成失败/中断时不自动撤回玩家发言，保留对话便于重试。" },
];

const LANGS = [
  { id: "zh-CN", label: "简体中文" },
  { id: "en", label: "English" },
];

export default function PreferencesScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [values, setValues] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await prefs.get();
      setValues(r?.preferences ?? {});
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Optimistic patch: flip locally, persist, roll back on failure.
  const patch = async (key: string, value: any) => {
    const prev = values[key];
    setValues((v) => ({ ...v, [key]: value }));
    try {
      await prefs.set({ [key]: value });
    } catch (e) {
      setValues((v) => ({ ...v, [key]: prev }));
      Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试");
    }
  };

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Attunements</Text>
          <Text style={styles.h1}>偏好</Text>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <ScrollView contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 50, gap: theme.space(5), paddingTop: theme.space(3) }}>
          {/* behavioral toggles */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>引擎行为</Text>
            {TOGGLES.map((tg, i) => (
              <Animated.View key={tg.key} entering={FadeInDown.delay(80 + i * 50).duration(360)} style={styles.toggleRow}>
                <View style={{ flex: 1, paddingRight: theme.space(3) }}>
                  <Text style={styles.toggleLabel}>{tg.label}</Text>
                  <Text style={styles.toggleNote}>{tg.note}</Text>
                </View>
                <Switch
                  value={!!values[tg.key]}
                  onValueChange={(v) => patch(tg.key, v)}
                  trackColor={{ false: theme.color.bgInput, true: theme.color.accentDeep }}
                  thumbColor={values[tg.key] ? theme.color.accentBright : theme.color.textFaint}
                />
              </Animated.View>
            ))}
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
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  section: { gap: theme.space(3) },
  sectionTitle: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 3, textTransform: "uppercase", color: theme.color.accent },
  langRow: { flexDirection: "row", gap: theme.space(3) },
  langChip: { flex: 1, paddingVertical: theme.space(3), alignItems: "center", borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  langChipActive: { borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  langText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.textDim },
  langNote: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 19 },
  toggleRow: { flexDirection: "row", alignItems: "center", paddingVertical: theme.space(3), borderBottomWidth: 1, borderBottomColor: theme.color.surfaceLine },
  toggleLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  toggleNote: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 19, marginTop: 2 },
});
