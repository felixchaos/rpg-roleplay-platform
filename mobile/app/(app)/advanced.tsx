/**
 * Arcane Apparatus — the power-user bench. Three sections fold the desktop's modelparams,
 * permissions, and tools/plugins surfaces into one mobile screen:
 *  · Generation — max_tokens / temperature / reasoning dials (persisted as preference keys)
 *  · Permission — how freely the LLM may write game state (ask / auto / readonly)
 *  · Apparatus — a read-only registry of the tools, MCP servers, skills and plugins wired in
 * Gesture sliders (RN ships none) match the GM-style bench so the whole app tunes alike.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Alert, LayoutChangeEvent, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Animated, { FadeInDown, runOnJS } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { EmberButton } from "@/components/ui";
import { prefs, permissions, apparatus } from "@/api";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

// Generation params: stored as preference keys, each with a real numeric range.
const PARAMS: { key: string; label: string; min: number; max: number; step: number; fallback: number }[] = [
  { key: "max_tokens", label: "回复上限 (tokens)", min: 256, max: 8192, step: 256, fallback: 2048 },
  { key: "temperature", label: "温度 (创意度)", min: 0, max: 2, step: 0.05, fallback: 1 },
  { key: "reasoning_effort", label: "推理强度", min: 0, max: 100, step: 5, fallback: 0 },
];

const PERM_MODES = [
  { id: "ask", label: "询问", note: "每次状态写入都请求你确认" },
  { id: "auto", label: "自动", note: "允许引擎自由更新游戏状态" },
  { id: "readonly", label: "只读", note: "禁止引擎写入状态（仅叙事）" },
];

function ParamDial({ label, min, max, step, value, onChange }: { label: string; min: number; max: number; step: number; value: number; onChange: (v: number) => void }) {
  const widthRef = useRef(1);
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);

  const onLayout = (e: LayoutChangeEvent) => { widthRef.current = Math.max(1, e.nativeEvent.layout.width); };
  const setFromX = useCallback((x: number) => {
    const frac = Math.max(0, Math.min(1, x / widthRef.current));
    const raw = min + frac * (max - min);
    const snapped = Math.round(raw / step) * step;
    const clamped = Math.max(min, Math.min(max, snapped));
    setLocal(clamped);
    onChange(clamped);
  }, [min, max, step, onChange]);

  const pan = Gesture.Pan().onBegin((e) => runOnJS(setFromX)(e.x)).onUpdate((e) => runOnJS(setFromX)(e.x));
  const tap = Gesture.Tap().onEnd((e) => runOnJS(setFromX)(e.x));
  const pct = ((local - min) / (max - min)) * 100;
  const display = step < 1 ? local.toFixed(2) : String(Math.round(local));

  return (
    <View style={styles.dial}>
      <View style={styles.dialHead}>
        <Text style={styles.dialLabel}>{label}</Text>
        <Text style={styles.dialValue}>{display}</Text>
      </View>
      <GestureDetector gesture={Gesture.Simultaneous(pan, tap)}>
        <View style={styles.trackWrap} onLayout={onLayout} hitSlop={{ top: 14, bottom: 14 }}>
          <View style={styles.track}><View style={[styles.fill, { width: `${pct}%` }]} /></View>
          <View style={[styles.bead, { left: `${pct}%` }]} />
        </View>
      </GestureDetector>
    </View>
  );
}

export default function AdvancedScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [values, setValues] = useState<Record<string, number>>({});
  const [permMode, setPermMode] = useState("ask");
  const [reg, setReg] = useState<{ tools: number; mcp: number; skills: number; plugins: number }>({ tools: 0, mcp: 0, skills: 0, plugins: 0 });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const p = await prefs.get().catch(() => null);
        const pv = p?.preferences ?? {};
        const seed: Record<string, number> = {};
        for (const par of PARAMS) seed[par.key] = typeof pv[par.key] === "number" ? pv[par.key] : par.fallback;
        setValues(seed);
        if (pv["permission_mode"]) setPermMode(String(pv["permission_mode"]));
        // registry counts (best-effort, read-only)
        const [t, m, s, pl] = await Promise.allSettled([apparatus.tools(), apparatus.mcpRuntime(), apparatus.skills(), apparatus.plugins()]);
        setReg({
          tools: t.status === "fulfilled" ? (t.value?.tools?.length ?? 0) : 0,
          mcp: m.status === "fulfilled" ? (m.value?.servers?.length ?? 0) : 0,
          skills: s.status === "fulfilled" ? (s.value?.skills?.length ?? 0) : 0,
          plugins: pl.status === "fulfilled" ? (pl.value?.plugins?.length ?? 0) : 0,
        });
      } catch (e) {
        if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await prefs.set({ ...values, permission_mode: permMode });
      try { await permissions.setMode(permMode); } catch { /* permission endpoint is per-save; non-fatal */ }
      Alert.alert("已保存", "高级设置已更新。");
    } catch (e) {
      Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setSaving(false);
    }
  };

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Apparatus</Text>
          <Text style={styles.h1}>高级</Text>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <ScrollView contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 50, gap: theme.space(6), paddingTop: theme.space(3) }}>
          <Animated.View entering={FadeInDown.duration(360)} style={styles.section}>
            <Text style={styles.sectionTitle}>生成参数</Text>
            {PARAMS.map((p) => (
              <ParamDial key={p.key} label={p.label} min={p.min} max={p.max} step={p.step} value={values[p.key] ?? p.fallback} onChange={(v) => setValues((s) => ({ ...s, [p.key]: v }))} />
            ))}
          </Animated.View>

          <Animated.View entering={FadeInDown.delay(80).duration(360)} style={styles.section}>
            <Text style={styles.sectionTitle}>状态写入权限</Text>
            {PERM_MODES.map((m) => {
              const active = permMode === m.id;
              return (
                <Pressable key={m.id} onPress={() => setPermMode(m.id)} style={[styles.permRow, active && styles.permRowActive]}>
                  <View style={[styles.radio, active && styles.radioOn]}>{active ? <View style={styles.radioDot} /> : null}</View>
                  <View style={{ flex: 1 }}>
                    <Text style={[styles.permLabel, active && { color: theme.color.accentBright }]}>{m.label}</Text>
                    <Text style={styles.permNote}>{m.note}</Text>
                  </View>
                </Pressable>
              );
            })}
          </Animated.View>

          <Animated.View entering={FadeInDown.delay(160).duration(360)} style={styles.section}>
            <Text style={styles.sectionTitle}>装置注册表</Text>
            <View style={styles.regGrid}>
              {[
                { label: "工具", n: reg.tools, glyph: "⌬" },
                { label: "MCP", n: reg.mcp, glyph: "⎈" },
                { label: "技能", n: reg.skills, glyph: "⚝" },
                { label: "插件", n: reg.plugins, glyph: "◈" },
              ].map((r) => (
                <View key={r.label} style={styles.regCard}>
                  <Text style={styles.regGlyph}>{r.glyph}</Text>
                  <Text style={styles.regNum}>{r.n}</Text>
                  <Text style={styles.regLabel}>{r.label}</Text>
                </View>
              ))}
            </View>
            <Pressable onPress={() => router.push("/(app)/apparatus")} style={styles.manageRow}>
              <Text style={styles.manageText}>管理 MCP 服务器与技能 ›</Text>
            </Pressable>
          </Animated.View>

          <EmberButton label={saving ? "保存中…" : "保存高级设置"} onPress={save} loading={saving} />
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
  dial: { gap: theme.space(2) },
  dialHead: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between" },
  dialLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.text },
  dialValue: { fontFamily: theme.font.mono, fontSize: theme.size.base, color: theme.color.accentBright },
  trackWrap: { height: 28, justifyContent: "center" },
  track: { height: 5, borderRadius: 3, backgroundColor: theme.color.bgInput, borderWidth: 1, borderColor: theme.color.surfaceLine, overflow: "hidden" },
  fill: { height: "100%", backgroundColor: theme.color.accent },
  bead: { position: "absolute", width: 20, height: 20, borderRadius: 10, backgroundColor: theme.color.accentBright, borderWidth: 2, borderColor: theme.color.bg, marginLeft: -10, shadowColor: theme.color.accent, shadowOpacity: 0.7, shadowRadius: 8, elevation: 6 },
  permRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), padding: theme.space(3), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  permRowActive: { borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  radio: { width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: theme.color.surfaceLineStrong, alignItems: "center", justifyContent: "center" },
  radioOn: { borderColor: theme.color.accent },
  radioDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: theme.color.accentBright },
  permLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  permNote: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, marginTop: 2 },
  regGrid: { flexDirection: "row", gap: theme.space(3) },
  regCard: { flex: 1, alignItems: "center", paddingVertical: theme.space(4), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard, gap: theme.space(1) },
  regGlyph: { fontSize: 20, color: theme.color.accent },
  regNum: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.accentBright },
  regLabel: { fontFamily: theme.font.displaySemi, fontSize: 10, letterSpacing: 1, textTransform: "uppercase", color: theme.color.textFaint },
  regHint: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 19 },
  manageRow: { paddingVertical: theme.space(3), alignItems: "center", borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost, marginTop: theme.space(1) },
  manageText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.accentBright },
});
