/**
 * Loom of Minds — in-chat model switch + Effort tuning. Tap to pick which model drives
 * this save's GM (save-scoped, not global), then dial its thinking budget. Effort persists
 * per-model in user preferences (model_effort dict), mirroring the desktop EffortSection.
 * A slide-up sheet of provider→model staves with a six-notch effort rail beneath.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { BlurView } from "expo-blur";
import { settings, prefs, ProviderInfo } from "@/api";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

const EFFORT_TIERS = ["off", "low", "medium", "high", "extra", "max"];
const EFFORT_LABEL: Record<string, string> = { off: "关", low: "低", medium: "中", high: "高", extra: "极", max: "满" };

export function ModelEffortPicker({
  visible,
  saveId,
  onClose,
  onPicked,
}: {
  visible: boolean;
  saveId: number;
  onClose: () => void;
  onPicked?: (label: string) => void;
}) {
  const insets = useSafeAreaInsets();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [selected, setSelected] = useState<{ api_id?: string; model_id?: string }>({});
  const [effort, setEffort] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const selKey = selected.api_id && selected.model_id ? `${selected.api_id}::${selected.model_id}` : "";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const m = await settings.models();
      const list: ProviderInfo[] = m?.models?.apis ?? [];
      for (const p of list) if (!p.api_id) p.api_id = (p as any).id;
      setProviders(list.filter((p) => p.has_credential));
      setSelected({ api_id: m?.selected?.api_id, model_id: m?.selected?.model_id });
      const pr = await prefs.get().catch(() => null);
      const me = pr?.preferences?.model_effort;
      if (me && typeof me === "object") setEffort(me);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (visible) load();
  }, [visible, load]);

  const pick = async (api_id: string, model_id: string, label: string) => {
    setBusy(true);
    try {
      await settings.selectModel(api_id, model_id, saveId);
      setSelected({ api_id, model_id });
      // Sync console_assistant_model_override so FamiliarSanctum follows this choice
      prefs.set({ console_assistant_model_override: { api_id, model: model_id } }).catch(() => {});
      onPicked?.(label);
    } catch (e) {
      Alert.alert("切换失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setBusy(false);
    }
  };

  const setTier = async (tier: string) => {
    if (!selKey) return;
    const next = { ...effort, [selKey]: tier };
    setEffort(next);
    try {
      await prefs.set({ model_effort: next });
    } catch {
      /* non-fatal */
    }
  };

  const currentTier = selKey ? effort[selKey] || "off" : "off";

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
        <BlurView intensity={30} tint="dark" style={styles.fill} />
        <View style={styles.grab} />
        <Text style={styles.title}>心智之织</Text>

        {loading ? (
          <ActivityIndicator color={theme.color.accent} style={{ marginVertical: theme.space(10) }} />
        ) : (
          <>
            <ScrollView style={styles.body}>
              {providers.length === 0 ? (
                <Text style={styles.empty}>没有已配置密钥的模型。请先在「设置 → 模型与密钥」添加。</Text>
              ) : (
                providers.map((p) => (
                  <View key={p.api_id} style={{ marginBottom: theme.space(3) }}>
                    <Text style={styles.provider}>{p.display_name || p.api_id}</Text>
                    {(p.models || []).slice(0, 10).map((mo: any) => {
                      const active = selected.api_id === p.api_id && selected.model_id === mo.id;
                      return (
                        <Pressable key={mo.id} onPress={() => pick(p.api_id, mo.id, mo.name || mo.id)} disabled={busy} style={[styles.modelRow, active && styles.modelActive]}>
                          <Text style={[styles.modelName, active && { color: theme.color.accentBright }]} numberOfLines={1}>{mo.name || mo.id}</Text>
                          {active ? <Text style={styles.check}>✦</Text> : null}
                        </Pressable>
                      );
                    })}
                  </View>
                ))
              )}
            </ScrollView>

            {selKey ? (
              <View style={styles.effortWrap}>
                <Text style={styles.effortLabel}>思考深度</Text>
                <View style={styles.effortRail}>
                  {EFFORT_TIERS.map((t) => {
                    const on = currentTier === t;
                    return (
                      <Pressable key={t} onPress={() => setTier(t)} style={[styles.effortNotch, on && styles.effortNotchOn]}>
                        <Text style={[styles.effortNotchText, on && { color: theme.color.bg }]}>{EFFORT_LABEL[t]}</Text>
                      </Pressable>
                    );
                  })}
                </View>
              </View>
            ) : null}
          </>
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  fill: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: theme.color.scrim },
  sheet: { position: "absolute", left: 0, right: 0, bottom: 0, maxHeight: "78%", backgroundColor: "rgba(20,16,12,0.92)", borderTopLeftRadius: theme.radius.xl, borderTopRightRadius: theme.radius.xl, borderWidth: 1, borderColor: theme.color.surfaceLineStrong, overflow: "hidden", paddingHorizontal: theme.space(5), paddingTop: theme.space(3) },
  grab: { alignSelf: "center", width: 44, height: 4, borderRadius: 2, backgroundColor: theme.color.surfaceLineStrong, marginBottom: theme.space(3) },
  title: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.text, letterSpacing: 1, marginBottom: theme.space(3) },
  body: { maxHeight: 360 },
  empty: { fontFamily: theme.font.proseItalic, fontSize: theme.size.md, color: theme.color.textFaint, textAlign: "center", paddingVertical: theme.space(10), lineHeight: 22 },
  provider: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 2, textTransform: "uppercase", color: theme.color.accent, marginBottom: theme.space(2) },
  modelRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: theme.space(2.5), paddingHorizontal: theme.space(3), borderRadius: theme.radius.sm },
  modelActive: { backgroundColor: theme.color.accentGhost },
  modelName: { flex: 1, fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.textDim },
  check: { color: theme.color.accentBright, fontSize: theme.size.md },
  effortWrap: { paddingTop: theme.space(3), borderTopWidth: 1, borderTopColor: theme.color.surfaceLine, gap: theme.space(2) },
  effortLabel: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 2, textTransform: "uppercase", color: theme.color.textFaint },
  effortRail: { flexDirection: "row", gap: theme.space(2) },
  effortNotch: { flex: 1, paddingVertical: theme.space(2.5), alignItems: "center", borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  effortNotchOn: { backgroundColor: theme.color.accent, borderColor: theme.color.accent },
  effortNotchText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.base, color: theme.color.textDim },
});
