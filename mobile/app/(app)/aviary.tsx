/**
 * Aviary — 鸟巢. Per-provider edit and model-roster management. Each provider is a perch
 * where its named birds (models) roost. The perch itself can be re-attuned (Base URL,
 * connection mode, key replacement); individual birds can be released (visibility off)
 * or called in fresh from the remote list (sync). The page lives as a deeper drilldown
 * from Settings — Settings stays a quick switcher; this is the breeder's ledger.
 *
 * Aesthetic direction: Candlelit Grimoire, ornithological register. Each provider is a
 * vellum card with a curling-fern divider and a perch silhouette; models hang as small
 * folio rows beneath. A "sync" action opens the Aviary's mirror — a sheet listing what
 * the remote currently shows vs. what's perched here (added / removed / kept), so the
 * breeder can decide what to admit.
 *
 * Backend (no new endpoints — strictly reusing what exists):
 *   GET  /api/v1/models                       — provider+model catalog
 *   GET  /api/v1/me/credentials               — per-user has_key + enabled overlay
 *   POST /api/v1/me/credentials               — upsert/edit key + base_url override
 *   POST /api/v1/me/credentials/delete        — release the key
 *   GET  /api/v1/me/credentials/test          — connectivity probe
 *   POST /api/models/remote/sync              — pull remote list
 *   GET  /api/models/diff?api_id=             — added/removed/kept
 *   POST /api/models/model                    — add a single model
 *   POST /api/models/model/delete             — remove a single model
 *   POST /api/me/models/visibility            — per-user visibility (synced ones)
 *   POST /api/models/health/refresh-all       — refresh connectivity for all
 *   GET  /api/v1/me/usage?group=api           — per-provider cost
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { BlurView } from "expo-blur";
import Animated, { FadeIn, FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { settings, ProviderInfo, ModelInfo } from "@/api";
import { ApiError } from "@/api/http";
import { EmberButton } from "@/components/ui";
import { theme, palette } from "@/theme/theme";

type EditState = {
  api_id: string;
  display_name: string;
  api_key: string;
  base_url: string;
  service_account_json: string;
  enabled: boolean;
};

type DiffShape = {
  added: string[];
  removed: string[];
  kept: string[];
};

function normalizeDiff(r: any): DiffShape {
  return {
    added: r?.added ?? r?.remote_only ?? [],
    removed: r?.removed ?? r?.local_only ?? [],
    kept: r?.kept ?? r?.matching ?? r?.common ?? [],
  };
}

export default function AviaryScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshingHealth, setRefreshingHealth] = useState(false);
  const [usageByApi, setUsageByApi] = useState<Record<string, number>>({});
  const [edit, setEdit] = useState<EditState | null>(null);
  const [saving, setSaving] = useState(false);
  const [diffApi, setDiffApi] = useState<string | null>(null);
  const [diff, setDiff] = useState<DiffShape | null>(null);
  const [diffPhase, setDiffPhase] = useState<"loading" | "done" | "error">("loading");
  const [diffError, setDiffError] = useState("");
  const [admitting, setAdmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const m = await settings.models();
      const list: ProviderInfo[] = m?.models?.apis ?? [];
      for (const p of list) if (!p.api_id) p.api_id = (p as any).id;
      try {
        const c = await settings.credentials();
        const credMap = new Map((c?.items ?? []).map((it: any) => [it.api_id, it]));
        for (const p of list) {
          const cred = credMap.get(p.api_id);
          if (cred) {
            p.has_credential = !!cred.has_credential || !!p.has_credential;
            if (cred.enabled != null) p.enabled = cred.enabled;
            if (cred.base_url_override) (p as any).base_url_override = cred.base_url_override;
          }
        }
      } catch {}
      setProviders(list);
      try {
        const u = await settings.usageByProvider(30);
        const m2: Record<string, number> = {};
        const byApi = u?.by_api || {};
        for (const k of Object.keys(byApi)) {
          const v = byApi[k];
          if (typeof v === "number") m2[k] = v;
          else if (v && typeof v.cost_usd === "number") m2[k] = v.cost_usd;
          else if (v && typeof v.total === "number") m2[k] = v.total;
        }
        setUsageByApi(m2);
      } catch {}
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const refreshHealth = async () => {
    setRefreshingHealth(true);
    try {
      await settings.refreshHealthAll();
      await load();
    } catch (e) {
      Alert.alert("刷新失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setRefreshingHealth(false);
    }
  };

  const openEdit = (p: ProviderInfo) => {
    setEdit({
      api_id: p.api_id,
      display_name: p.display_name || p.api_id,
      api_key: "",
      base_url: (p as any).base_url_override || "",
      service_account_json: "",
      enabled: p.enabled !== false,
    });
  };

  const saveEdit = async () => {
    if (!edit) return;
    setSaving(true);
    try {
      const body: any = { api_id: edit.api_id, enabled: edit.enabled };
      if (edit.api_key.trim()) body.api_key = edit.api_key.trim();
      if (edit.base_url.trim()) body.base_url_override = edit.base_url.trim();
      // Vertex AI Service Account JSON gets posted as api_key for the SA flow
      if (edit.service_account_json.trim()) body.api_key = edit.service_account_json.trim();
      await settings.setCredential(body);
      setEdit(null);
      load();
      Alert.alert("已更新", "凭据已加密存储。");
    } catch (e) {
      Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setSaving(false);
    }
  };

  const releaseKey = (api_id: string) => {
    Alert.alert("释放密钥", "将删除此 provider 的本账号密钥。模型选项仍保留。", [
      { text: "取消", style: "cancel" },
      {
        text: "释放",
        style: "destructive",
        onPress: async () => {
          try {
            await settings.deleteCredential(api_id);
            setEdit(null);
            load();
          } catch (e) {
            Alert.alert("失败", e instanceof ApiError ? e.message : "请重试");
          }
        },
      },
    ]);
  };

  const testConnection = async (api_id: string) => {
    try {
      const r = await settings.testCredential(api_id);
      Alert.alert(r.ok ? "连接正常" : "连接失败", r.ok ? `延迟 ${r.latency_ms ?? "?"}ms` : r.error || "未知错误");
    } catch (e) {
      Alert.alert("测试失败", e instanceof ApiError ? e.message : "请重试");
    }
  };

  const openDiff = async (api_id: string) => {
    setDiffApi(api_id);
    setDiff(null);
    setDiffError("");
    setDiffPhase("loading");
    try {
      // Pull remote first so /diff has fresh data to compare against
      await settings.syncRemote(api_id).catch(() => {});
      const r = await settings.modelsDiff(api_id);
      setDiff(normalizeDiff(r));
      setDiffPhase("done");
    } catch (e) {
      setDiffError(e instanceof ApiError ? e.message : "拉取失败");
      setDiffPhase("error");
    }
  };

  const admitAll = async () => {
    if (!diff || !diffApi || diff.added.length === 0) return;
    setAdmitting(true);
    let ok = 0, fail = 0;
    for (const real_name of diff.added) {
      try {
        await settings.upsertModel({ api_id: diffApi, real_name, display_name: real_name, enabled: true });
        ok++;
      } catch { fail++; }
    }
    setAdmitting(false);
    Alert.alert(fail ? "部分成功" : "已纳入", fail ? `成功 ${ok}，失败 ${fail}` : `${ok} 只新鸟入巢`);
    setDiffApi(null);
    setDiff(null);
    load();
  };

  const toggleVisible = async (p: ProviderInfo, m: ModelInfo) => {
    const visible = (m as any).enabled !== false;
    try {
      await settings.setModelVisibility({ api_id: p.api_id, model: m.id, visible: !visible });
      load();
    } catch (e) {
      Alert.alert("操作失败", e instanceof ApiError ? e.message : "该模型可能不属于你的同步清单，请使用「逐只移除」。");
    }
  };

  const deleteModel = (p: ProviderInfo, m: ModelInfo) => {
    Alert.alert("逐只移除", `从巢中除名 ${m.name || m.id}？`, [
      { text: "取消", style: "cancel" },
      {
        text: "除名",
        style: "destructive",
        onPress: async () => {
          try {
            await settings.deleteModel({ api_id: p.api_id, model_id: m.id, real_name: m.id });
            load();
          } catch (e) {
            Alert.alert("失败", e instanceof ApiError ? e.message : "请重试");
          }
        },
      },
    ]);
  };

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Aviary</Text>
          <Text style={styles.h1}>鸟巢 · 模型管理</Text>
        </View>
        <Pressable
          onPress={refreshHealth}
          hitSlop={8}
          disabled={refreshingHealth}
          style={styles.refreshChip}
        >
          {refreshingHealth ? (
            <ActivityIndicator color={theme.color.accent} size="small" />
          ) : (
            <Text style={styles.refreshChipLabel}>巡哨</Text>
          )}
        </Pressable>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <ScrollView
          contentContainerStyle={{ paddingHorizontal: theme.space(5), paddingBottom: insets.bottom + 60, gap: theme.space(5), paddingTop: theme.space(2) }}
        >
          <Animated.View entering={FadeIn.duration(500)}>
            <Text style={styles.introText}>
              每个 provider 是一根栖架，其上的模型是巢中之鸟。可重新调律栖架本身、点鸟出入册。
            </Text>
          </Animated.View>

          {providers.map((p, idx) => {
            const usage = usageByApi[p.api_id];
            return (
              <Animated.View
                key={p.api_id}
                entering={FadeInDown.delay(idx * 40).duration(380)}
                style={styles.perch}
              >
                <View style={styles.perchHead}>
                  <View style={[styles.perchDot, { backgroundColor: p.has_credential ? theme.color.success : theme.color.textFaint }]} />
                  <View style={{ flex: 1 }}>
                    <Text style={styles.perchName}>{p.display_name || p.api_id}</Text>
                    <Text style={styles.perchMeta}>
                      {(p.models || []).length} 只 · {p.has_credential ? "已系密钥" : "未系密钥"}
                      {usage != null ? ` · 近 30 天 $${usage.toFixed(3)}` : ""}
                    </Text>
                  </View>
                </View>

                <View style={styles.perchActions}>
                  <Pressable onPress={() => openEdit(p)} hitSlop={6}>
                    <Text style={styles.perchAction}>{p.has_credential ? "调律" : "系上密钥"}</Text>
                  </Pressable>
                  {p.has_credential ? (
                    <Pressable onPress={() => testConnection(p.api_id)} hitSlop={6}>
                      <Text style={styles.perchAction}>试音</Text>
                    </Pressable>
                  ) : null}
                  <Pressable onPress={() => openDiff(p.api_id)} hitSlop={6}>
                    <Text style={styles.perchAction}>召新</Text>
                  </Pressable>
                </View>

                {(p.models || []).length > 0 ? (
                  <View style={styles.roost}>
                    {(p.models || []).map((m) => {
                      const visible = (m as any).enabled !== false;
                      const health = (m as any).health || (m as any).connectivity?.status;
                      return (
                        <View key={m.id} style={styles.bird}>
                          <View style={[styles.birdDot, health === "ok" ? { backgroundColor: theme.color.success } : health === "err" ? { backgroundColor: theme.color.danger } : { backgroundColor: theme.color.textFaint }]} />
                          <Text style={[styles.birdName, !visible && { color: theme.color.textFaint, textDecorationLine: "line-through" as const }]} numberOfLines={1}>
                            {m.name || m.id}
                          </Text>
                          <Pressable onPress={() => toggleVisible(p, m)} hitSlop={6}>
                            <Text style={styles.birdAction}>{visible ? "藏" : "现"}</Text>
                          </Pressable>
                          <Pressable onPress={() => deleteModel(p, m)} hitSlop={6}>
                            <Text style={[styles.birdAction, { color: theme.color.danger }]}>除</Text>
                          </Pressable>
                        </View>
                      );
                    })}
                  </View>
                ) : (
                  <Text style={styles.empty}>此巢空——点「召新」从远端拉取。</Text>
                )}
              </Animated.View>
            );
          })}
        </ScrollView>
      )}

      {/* Edit Sheet */}
      <Modal visible={!!edit} transparent animationType="slide" onRequestClose={() => setEdit(null)}>
        <Pressable style={styles.backdrop} onPress={() => setEdit(null)} />
        <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
          <BlurView intensity={36} tint="dark" style={styles.fill} />
          <View style={styles.grabber} />
          <Text style={styles.kicker}>Perch</Text>
          <Text style={styles.sheetTitle}>{edit?.display_name}</Text>

          <ScrollView style={{ maxHeight: 480 }} contentContainerStyle={{ gap: theme.space(4) }}>
            <View>
              <Text style={styles.fieldLabel}>API Key</Text>
              <Text style={styles.fieldHint}>留空保留原值；填写则覆盖。</Text>
              <TextInput
                value={edit?.api_key ?? ""}
                onChangeText={(s) => edit && setEdit({ ...edit, api_key: s })}
                placeholder="sk-… 或 vertex 的 SA JSON"
                placeholderTextColor={theme.color.textFaint}
                secureTextEntry
                autoCapitalize="none"
                style={styles.input}
              />
            </View>

            <View>
              <Text style={styles.fieldLabel}>Base URL 覆盖（可选）</Text>
              <Text style={styles.fieldHint}>填写自建/中转地址。留空走默认。</Text>
              <TextInput
                value={edit?.base_url ?? ""}
                onChangeText={(s) => edit && setEdit({ ...edit, base_url: s })}
                placeholder="https://api.your-proxy.com/v1"
                placeholderTextColor={theme.color.textFaint}
                autoCapitalize="none"
                style={styles.input}
              />
            </View>

            {/Vertex|google|gemini/i.test(edit?.api_id || "") ? (
              <View>
                <Text style={styles.fieldLabel}>Service Account JSON（Vertex AI）</Text>
                <Text style={styles.fieldHint}>整段粘贴 SA 文件内容。覆盖 API Key 字段。</Text>
                <TextInput
                  value={edit?.service_account_json ?? ""}
                  onChangeText={(s) => edit && setEdit({ ...edit, service_account_json: s })}
                  placeholder={'{"type":"service_account","project_id":"…","client_email":"…","private_key":"-----BEGIN PRIVATE KEY-----\\n…"}'}
                  placeholderTextColor={theme.color.textFaint}
                  autoCapitalize="none"
                  multiline
                  style={[styles.input, { minHeight: 120, fontFamily: theme.font.mono, fontSize: 11 }]}
                />
              </View>
            ) : null}

            <View style={styles.toggleRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.fieldLabel}>启用此 provider</Text>
                <Text style={styles.fieldHint}>关闭后此 provider 的模型在选择器中隐藏。</Text>
              </View>
              <Pressable
                onPress={() => edit && setEdit({ ...edit, enabled: !edit.enabled })}
                style={[styles.toggle, edit?.enabled && styles.toggleOn]}
              >
                <View style={[styles.toggleThumb, edit?.enabled && styles.toggleThumbOn]} />
              </Pressable>
            </View>

            {edit && providers.find((p) => p.api_id === edit.api_id)?.has_credential ? (
              <Pressable onPress={() => releaseKey(edit.api_id)} style={styles.dangerRow}>
                <Text style={styles.dangerText}>释放此 provider 的本账号密钥</Text>
              </Pressable>
            ) : null}
          </ScrollView>

          <View style={{ flexDirection: "row", gap: theme.space(3), marginTop: theme.space(4) }}>
            <EmberButton label="取消" variant="ghost" onPress={() => setEdit(null)} style={{ flex: 1 }} />
            <EmberButton label="保存" onPress={saveEdit} loading={saving} style={{ flex: 1 }} />
          </View>
        </View>
      </Modal>

      {/* Diff Sheet */}
      <Modal visible={!!diffApi} transparent animationType="slide" onRequestClose={() => setDiffApi(null)}>
        <Pressable style={styles.backdrop} onPress={() => setDiffApi(null)} />
        <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
          <BlurView intensity={36} tint="dark" style={styles.fill} />
          <View style={styles.grabber} />
          <Text style={styles.kicker}>Aviary Mirror</Text>
          <Text style={styles.sheetTitle}>{providers.find((p) => p.api_id === diffApi)?.display_name || diffApi}</Text>

          {diffPhase === "loading" ? (
            <View style={{ paddingVertical: theme.space(10), alignItems: "center", gap: theme.space(3) }}>
              <ActivityIndicator color={theme.color.accent} />
              <Text style={styles.fieldHint}>正向远端栖架请鸟…</Text>
            </View>
          ) : diffPhase === "error" ? (
            <View style={{ paddingVertical: theme.space(8), alignItems: "center", gap: theme.space(2) }}>
              <Text style={[styles.fieldHint, { color: theme.color.danger }]}>⚠ {diffError}</Text>
              <Text style={styles.fieldHint}>可能是密钥失效或网络不通。</Text>
            </View>
          ) : (
            <ScrollView style={{ maxHeight: 420 }} contentContainerStyle={{ gap: theme.space(4) }}>
              {diff && diff.added.length > 0 ? (
                <View>
                  <Text style={styles.diffHead}>新鸟 · {diff.added.length}</Text>
                  {diff.added.map((n) => (
                    <Text key={`a-${n}`} style={[styles.diffItem, { color: theme.color.success }]}>＋ {n}</Text>
                  ))}
                </View>
              ) : null}
              {diff && diff.removed.length > 0 ? (
                <View>
                  <Text style={styles.diffHead}>失踪 · {diff.removed.length}</Text>
                  {diff.removed.map((n) => (
                    <Text key={`r-${n}`} style={[styles.diffItem, { color: theme.color.danger }]}>－ {n}</Text>
                  ))}
                </View>
              ) : null}
              {diff && diff.kept.length > 0 ? (
                <View>
                  <Text style={styles.diffHead}>留巢 · {diff.kept.length}</Text>
                  {diff.kept.slice(0, 30).map((n) => (
                    <Text key={`k-${n}`} style={styles.diffItem}>· {n}</Text>
                  ))}
                  {diff.kept.length > 30 ? (
                    <Text style={styles.fieldHint}>… 等 {diff.kept.length} 只</Text>
                  ) : null}
                </View>
              ) : null}
              {diff && diff.added.length === 0 && diff.removed.length === 0 ? (
                <Text style={styles.fieldHint}>巢与远端一致，无需变更。</Text>
              ) : null}
            </ScrollView>
          )}

          <View style={{ flexDirection: "row", gap: theme.space(3), marginTop: theme.space(4) }}>
            <EmberButton label="关闭" variant="ghost" onPress={() => setDiffApi(null)} style={{ flex: 1 }} />
            <EmberButton
              label={diff && diff.added.length > 0 ? `纳入 ${diff.added.length} 只` : "已同步"}
              onPress={admitAll}
              loading={admitting}
              disabled={!diff || diff.added.length === 0}
              style={{ flex: 1 }}
            />
          </View>
        </View>
      </Modal>
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  refreshChip: { paddingVertical: theme.space(2), paddingHorizontal: theme.space(4), borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  refreshChipLabel: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1, color: theme.color.accent, textTransform: "uppercase" },

  introText: { fontFamily: theme.font.proseItalic, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 22 },

  perch: { borderWidth: 1, borderColor: theme.color.surfaceLine, borderRadius: theme.radius.md, padding: theme.space(4), gap: theme.space(3), backgroundColor: theme.color.bgCard },
  perchHead: { flexDirection: "row", alignItems: "center", gap: theme.space(3) },
  perchDot: { width: 10, height: 10, borderRadius: 5 },
  perchName: { fontFamily: theme.font.display, fontSize: theme.size.md, color: theme.color.text, letterSpacing: 0.5 },
  perchMeta: { fontFamily: theme.font.prose, fontSize: theme.size.xs, color: theme.color.textFaint, marginTop: 2 },
  perchActions: { flexDirection: "row", gap: theme.space(5) },
  perchAction: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.accent },

  roost: { gap: theme.space(2), paddingTop: theme.space(2), borderTopWidth: 1, borderTopColor: theme.color.surfaceLine },
  bird: { flexDirection: "row", alignItems: "center", gap: theme.space(3), paddingVertical: theme.space(2) },
  birdDot: { width: 6, height: 6, borderRadius: 3 },
  birdName: { flex: 1, fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.textDim },
  birdAction: { fontFamily: theme.font.proseSemi, fontSize: theme.size.xs, color: theme.color.accent, letterSpacing: 1 },
  empty: { fontFamily: theme.font.proseItalic, fontSize: theme.size.sm, color: theme.color.textFaint, textAlign: "center", paddingVertical: theme.space(3) },

  // Edit sheet
  fill: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: theme.color.scrim },
  sheet: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    maxHeight: "92%",
    backgroundColor: "rgba(18,14,10,0.96)",
    borderTopLeftRadius: theme.radius.xl, borderTopRightRadius: theme.radius.xl,
    borderWidth: 1, borderColor: theme.color.surfaceLineStrong,
    overflow: "hidden",
    paddingHorizontal: theme.space(5), paddingTop: theme.space(3),
  },
  grabber: { alignSelf: "center", width: 44, height: 4, borderRadius: 2, backgroundColor: theme.color.surfaceLineStrong, marginBottom: theme.space(2) },
  sheetTitle: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1, marginBottom: theme.space(4) },
  fieldLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.text },
  fieldHint: { fontFamily: theme.font.prose, fontSize: theme.size.xs, color: theme.color.textFaint, marginTop: 2, marginBottom: theme.space(2), lineHeight: 17 },
  input: { backgroundColor: theme.color.bgInput, borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine, paddingHorizontal: theme.space(3), paddingVertical: theme.space(3), color: theme.color.text, fontFamily: theme.font.prose, fontSize: theme.size.sm },
  toggleRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3) },
  toggle: { width: 48, height: 28, borderRadius: 14, padding: 3, backgroundColor: theme.color.bgInput, borderWidth: 1, borderColor: theme.color.surfaceLine, justifyContent: "center" },
  toggleOn: { backgroundColor: theme.color.accent, borderColor: theme.color.accent },
  toggleThumb: { width: 20, height: 20, borderRadius: 10, backgroundColor: theme.color.textFaint },
  toggleThumbOn: { backgroundColor: theme.color.bg, transform: [{ translateX: 20 }] },
  dangerRow: { paddingVertical: theme.space(3), alignItems: "center", borderTopWidth: 1, borderTopColor: theme.color.surfaceLine },
  dangerText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.danger, letterSpacing: 0.5 },

  // Diff sheet
  diffHead: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 2, textTransform: "uppercase", color: theme.color.accent, marginBottom: theme.space(2) },
  diffItem: { fontFamily: theme.font.mono, fontSize: 12, color: theme.color.textDim, paddingVertical: 2 },
});
