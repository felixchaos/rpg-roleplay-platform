/**
 * Save Settings — per-save overrides that diverge from your account defaults. The
 * headline knob is a model override (this save can run on a different model than your
 * global pick); we also surface whatever other scalar knobs the backend returns,
 * rendered generically so the screen never goes stale when the schema grows.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { EmberButton, RuneDivider } from "@/components/ui";
import { saveSettings, settings as modelApi, ProviderInfo } from "@/api";
import { ApiError } from "@/api/http";
import { usePrompt } from "@/components/PromptDialog";
import { theme } from "@/theme/theme";

export default function SaveSettingsScreen() {
  const { id, title } = useLocalSearchParams<{ id: string; title?: string }>();
  const saveId = Number(id);
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { prompt, promptNode } = usePrompt();
  const [config, setConfig] = useState<Record<string, any>>({});
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, m] = await Promise.all([
        saveSettings.get(saveId),
        modelApi.models().catch(() => null),
      ]);
      setConfig(s?.settings ?? {});
      setProviders(m?.models?.apis ?? []);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, [saveId]);

  useEffect(() => {
    load();
  }, [load]);

  const setKey = (k: string, v: any) => setConfig((c) => ({ ...c, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      await saveSettings.patch(saveId, config);
      Alert.alert("已保存", "此存档的设置已更新。", [{ text: "好", onPress: () => router.back() }]);
    } catch (e) {
      Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setSaving(false);
    }
  };

  const currentModel = config.model_id || config.model || "（跟随账号默认）";

  // Render any leftover scalar knobs generically (skip the model keys we render specially).
  const SKIP = new Set(["model_id", "model", "api_id"]);
  const scalarKeys = Object.keys(config).filter((k) => !SKIP.has(k) && ["boolean", "number", "string"].includes(typeof config[k]));

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Save Settings</Text>
          <Text style={styles.h1} numberOfLines={1}>{title || "存档设置"}</Text>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <ScrollView contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 50, gap: theme.space(4), paddingTop: theme.space(2) }}>
          {/* model override */}
          <Animated.View entering={FadeInDown.duration(380)} style={styles.section}>
            <Text style={styles.sectionTitle}>模型覆盖</Text>
            <Pressable onPress={() => setModelPickerOpen((v) => !v)} style={styles.modelRow}>
              <Text style={styles.modelCurrent} numberOfLines={1}>{currentModel}</Text>
              <Text style={styles.chevron}>{modelPickerOpen ? "▾" : "▸"}</Text>
            </Pressable>
            {modelPickerOpen ? (
              <View style={styles.modelList}>
                <Pressable onPress={() => { setKey("model_id", null); setKey("api_id", null); setModelPickerOpen(false); }} style={styles.modelOpt}>
                  <Text style={styles.modelOptText}>跟随账号默认</Text>
                </Pressable>
                {providers.flatMap((p) =>
                  (p.models || []).slice(0, 6).map((m) => {
                    const active = config.model_id === m.id;
                    return (
                      <Pressable
                        key={`${p.api_id}:${m.id}`}
                        onPress={() => { setKey("api_id", p.api_id); setKey("model_id", m.id); setModelPickerOpen(false); }}
                        style={[styles.modelOpt, active && styles.modelOptActive]}
                      >
                        <Text style={[styles.modelOptText, active && { color: theme.color.accentBright }]} numberOfLines={1}>
                          {m.name || m.id}
                        </Text>
                        <Text style={styles.modelOptProvider}>{p.display_name || p.api_id}</Text>
                      </Pressable>
                    );
                  }),
                )}
              </View>
            ) : null}
          </Animated.View>

          {scalarKeys.length > 0 ? (
            <Animated.View entering={FadeInDown.delay(80).duration(380)} style={styles.section}>
              <Text style={styles.sectionTitle}>其它</Text>
              {scalarKeys.map((k) => (
                <View key={k} style={styles.knobRow}>
                  <Text style={styles.knobLabel}>{k}</Text>
                  {typeof config[k] === "boolean" ? (
                    <Switch
                      value={config[k]}
                      onValueChange={(v) => setKey(k, v)}
                      trackColor={{ false: theme.color.bgInput, true: theme.color.accentDeep }}
                      thumbColor={config[k] ? theme.color.accentBright : theme.color.textFaint}
                    />
                  ) : (
                    <Pressable
                      hitSlop={8}
                      onPress={() =>
                        prompt({
                          title: `编辑 ${k}`,
                          initialValue: String(config[k] ?? ""),
                          placeholder: "新的值",
                          onConfirm: (v) => {
                            // preserve numeric type when the original was a number
                            const next = typeof config[k] === "number" && v.trim() !== "" && !Number.isNaN(Number(v)) ? Number(v) : v;
                            setKey(k, next);
                          },
                        })
                      }
                    >
                      <Text style={[styles.knobValue, styles.knobEditable]} numberOfLines={1}>{String(config[k]) || "—"} ✎</Text>
                    </Pressable>
                  )}
                </View>
              ))}
            </Animated.View>
          ) : null}

          <RuneDivider />
          <EmberButton label={saving ? "保存中…" : "保存设置"} onPress={save} loading={saving} />
        </ScrollView>
      )}
      {promptNode}
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  section: { gap: theme.space(2) },
  sectionTitle: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 3, textTransform: "uppercase", color: theme.color.accent, marginBottom: theme.space(1) },
  modelRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: theme.space(4), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  modelCurrent: { flex: 1, fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.text },
  chevron: { fontSize: 16, color: theme.color.textFaint },
  modelList: { gap: 2, marginTop: theme.space(1) },
  modelOpt: { paddingVertical: theme.space(3), paddingHorizontal: theme.space(4), borderRadius: theme.radius.sm },
  modelOptActive: { backgroundColor: theme.color.accentGhost },
  modelOptText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.textDim },
  modelOptProvider: { fontFamily: theme.font.mono, fontSize: 10, color: theme.color.textFaint, marginTop: 2 },
  knobRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: theme.space(3), borderBottomWidth: 1, borderBottomColor: theme.color.surfaceLine },
  knobLabel: { flex: 1, fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.text },
  knobValue: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.textDim, maxWidth: 160 },
  knobEditable: { color: theme.color.accent },
});
